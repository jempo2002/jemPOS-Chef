"""Reportes y contabilidad del restaurante (rol Admin).

Toma la idea del Finanzas de jemPOS (core._build_dashboard_data): ventas,
costo de lo vendido, utilidad bruta, gastos y utilidad neta del periodo, con
comparativo contra el periodo anterior. Cambios para un restaurante:

- Todo se filtra por sede (o todas las sedes del restaurante).
- El costo de cada linea es el que tenia el plato al cobrar
  (detalle_ventas.costo_unitario_historico); las ventas viejas sin ese dato
  usan el costo actual del producto.
- La propina no es venta ni utilidad: es de los meseros. Se muestra aparte.
- Mermas (lo anulado despues de enviarlo a cocina) restan a la utilidad.
- Las compras de insumos no restan como gasto (ya restan como costo cuando
  el plato se vende): se muestran en el flujo de caja.

Fechas: ventas.fecha_creacion es TIMESTAMP y la conexion usa la zona del
negocio (database.ZONA_MYSQL), asi que DATE() y HOUR() dan el dia y la hora
locales.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.services.gastos_service import CATEGORIAS, CATEGORIAS_NO_OPERATIVAS
from app.utils.helpers import ahora_local
from database import get_db

PERIODOS = {
    "hoy": "Hoy",
    "ayer": "Ayer",
    "semana": "Esta semana",
    "mes": "Este mes",
    "mes_anterior": "Mes pasado",
    "rango": "Elegir fechas",
}
DIAS_RANGO_MAX = 366
# Hasta este numero de dias la grafica va por dia; mas largo, por mes.
DIAS_GRAFICA_DIARIA = 62
TIPOS_PEDIDO = {"mesa": "Mesas", "llevar": "Para llevar", "domicilio": "Domicilios", None: "Sin pedido"}
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
DIAS_SEMANA = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


# --- periodo y sedes ---------------------------------------------------------------

def _dia(texto) -> date | None:
    try:
        return datetime.strptime(str(texto or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def periodo(clave: str | None, desde=None, hasta=None) -> dict:
    """{clave, inicio, fin (exclusivo), etiqueta, desde, hasta} del filtro.
    Un rango invalido cae en 'hoy' en vez de fallar: es una pantalla de
    consulta."""
    ahora = ahora_local()
    hoy = ahora.date()
    clave = clave if clave in PERIODOS else "hoy"
    if clave == "rango":
        d, h = _dia(desde), _dia(hasta)
        if d and h and d <= h <= hoy and (h - d).days < DIAS_RANGO_MAX:
            primero, ultimo = d, h
        else:
            clave = "hoy"
    if clave == "hoy":
        primero = ultimo = hoy
    elif clave == "ayer":
        primero = ultimo = hoy - timedelta(days=1)
    elif clave == "semana":
        primero, ultimo = hoy - timedelta(days=hoy.weekday()), hoy
    elif clave == "mes":
        primero, ultimo = hoy.replace(day=1), hoy
    elif clave == "mes_anterior":
        ultimo = hoy.replace(day=1) - timedelta(days=1)
        primero = ultimo.replace(day=1)
    inicio = datetime(primero.year, primero.month, primero.day)
    fin = datetime(ultimo.year, ultimo.month, ultimo.day) + timedelta(days=1)
    if primero == ultimo:
        etiqueta = f"{DIAS_SEMANA[primero.weekday()]} {primero.day} {MESES[primero.month - 1]} {primero.year}"
    else:
        etiqueta = (f"{primero.day} {MESES[primero.month - 1]}"
                    f"{'' if primero.year == ultimo.year else f' {primero.year}'} al "
                    f"{ultimo.day} {MESES[ultimo.month - 1]} {ultimo.year}")
    return {
        "clave": clave,
        "inicio": inicio,
        "fin": fin,
        "dias": (ultimo - primero).days + 1,
        "etiqueta": etiqueta,
        "desde": primero.isoformat(),
        "hasta": ultimo.isoformat(),
    }


def sedes_de(id_tienda: int) -> list[dict]:
    """Todas las sedes, tambien las eliminadas: su historia sigue contando."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id_sede, nombre, estado FROM sedes WHERE id_tienda = %s ORDER BY estado, es_principal DESC, nombre",
            (id_tienda,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def _en_sedes(columna: str, ids: list[int]) -> tuple[str, tuple]:
    """Fragmento ' AND col IN (...)' con sus parametros. `ids` nunca viene
    vacio: quien llama ya resolvio las sedes de la tienda."""
    return f" AND {columna} IN ({', '.join(['%s'] * len(ids))})", tuple(ids)


# --- resumen -------------------------------------------------------------------------

def _ventas_y_costo(cur, id_tienda: int, ids: list[int], inicio: datetime, fin: datetime) -> dict:
    filtro, params = _en_sedes("v.id_sede", ids)
    cur.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(v.total_final), 0) AS ventas, COALESCE(SUM(v.propina), 0) AS propinas "
        "FROM ventas v WHERE v.id_tienda = %s AND v.estado_venta = 'Pagada' "
        "AND v.fecha_creacion >= %s AND v.fecha_creacion < %s" + filtro,
        (id_tienda, inicio, fin) + params,
    )
    r = cur.fetchone()
    cur.execute(
        "SELECT COALESCE(SUM(dv.cantidad * COALESCE(dv.costo_unitario_historico, p.precio_costo)), 0) AS costo, "
        "COUNT(DISTINCT CASE WHEN COALESCE(dv.costo_unitario_historico, p.precio_costo) = 0 THEN dv.id_producto END) "
        "  AS sin_costo "
        "FROM detalle_ventas dv JOIN ventas v ON v.id_venta = dv.id_venta "
        "JOIN productos p ON p.id_producto = dv.id_producto "
        "WHERE v.id_tienda = %s AND v.estado_venta = 'Pagada' "
        "AND v.fecha_creacion >= %s AND v.fecha_creacion < %s" + filtro,
        (id_tienda, inicio, fin) + params,
    )
    c = cur.fetchone()
    filtro_m, params_m = _en_sedes("id_sede", ids)
    cur.execute(
        "SELECT COALESCE(SUM(cantidad * costo_unitario), 0) AS mermas FROM mermas "
        "WHERE id_tienda = %s AND creada_en >= %s AND creada_en < %s" + filtro_m,
        (id_tienda, inicio, fin) + params_m,
    )
    m = cur.fetchone()
    filtro_g, params_g = _en_sedes("id_sede", ids)
    cur.execute(
        "SELECT categoria, COUNT(*) AS n, COALESCE(SUM(monto), 0) AS total FROM gastos_caja "
        "WHERE id_tienda = %s AND estado_activo = 1 AND fecha_creacion >= %s AND fecha_creacion < %s" + filtro_g
        + " GROUP BY categoria",
        (id_tienda, inicio, fin) + params_g,
    )
    gastos = {f["categoria"]: (int(f["n"]), float(f["total"])) for f in cur.fetchall()}
    ventas = float(r["ventas"])
    costo = round(float(c["costo"]), 2)
    mermas = round(float(m["mermas"]), 2)
    operacion = sum(t for k, (_, t) in gastos.items() if k not in CATEGORIAS_NO_OPERATIVAS)
    bruta = ventas - costo
    return {
        "num_ventas": int(r["n"]),
        "ventas": ventas,
        "propinas": float(r["propinas"]),
        "costo": costo,
        "sin_costo": int(c["sin_costo"] or 0),
        "utilidad_bruta": round(bruta, 2),
        "mermas": mermas,
        "gastos": gastos,
        "gastos_operacion": round(operacion, 2),
        "compras": round(sum(t for k, (_, t) in gastos.items() if k in CATEGORIAS_NO_OPERATIVAS), 2),
        "utilidad_neta": round(bruta - mermas - operacion, 2),
    }


def _variacion(actual: float, anterior: float) -> dict | None:
    if not anterior:
        return None
    pct = (actual - anterior) / abs(anterior) * 100
    return {"sube": pct >= 0, "texto": f"{'+' if pct >= 0 else ''}{pct:.0f} %"}


def _por_metodo(cur, id_tienda: int, ids: list[int], inicio: datetime, fin: datetime) -> list[dict]:
    """Lo que entro por cada medio, con propina (lo que hay que encontrar en
    el cajon, en Nequi y en el datafono). Un Mixto reparte su efectivo y su
    transferencia; en una cuenta dividida la parte pagada con tarjeta sale de
    la transferencia (pedido_cuentas), el resto es Nequi/Daviplata, igual
    que en caja_service._resumen."""
    filtro, params = _en_sedes("v.id_sede", ids)
    cur.execute(
        "SELECT v.metodo_pago, COUNT(*) AS n, COALESCE(SUM(v.total_final + v.propina), 0) AS total, "
        "COALESCE(SUM(v.monto_efectivo), 0) AS efectivo, COALESCE(SUM(v.monto_transferencia), 0) AS transferencia, "
        "COALESCE(SUM(pc.tarjeta), 0) AS tarjeta "
        "FROM ventas v LEFT JOIN (SELECT c.id_venta, SUM(c.monto + c.propina) AS tarjeta FROM pedido_cuentas c "
        "  WHERE c.metodo_pago = 'Tarjeta' GROUP BY c.id_venta) pc ON pc.id_venta = v.id_venta AND v.metodo_pago = 'Mixto' "
        "WHERE v.id_tienda = %s AND v.estado_venta = 'Pagada' AND v.fecha_creacion >= %s AND v.fecha_creacion < %s"
        + filtro + " GROUP BY v.metodo_pago",
        (id_tienda, inicio, fin) + params,
    )
    totales = {"Efectivo": 0.0, "Nequi/Daviplata": 0.0, "Tarjeta": 0.0}
    mixtas = 0
    for f in cur.fetchall():
        if f["metodo_pago"] == "Mixto":
            mixtas = int(f["n"])
            tarjeta = float(f["tarjeta"])
            totales["Efectivo"] += float(f["efectivo"])
            totales["Tarjeta"] += tarjeta
            totales["Nequi/Daviplata"] += float(f["transferencia"]) - tarjeta
        elif f["metodo_pago"] in totales:
            totales[f["metodo_pago"]] += float(f["total"])
    suma = sum(totales.values()) or 1
    filas = [{"metodo": m, "total": round(t, 2), "pct": round(t / suma * 100, 1)} for m, t in totales.items()]
    if mixtas:
        filas.append({"nota": f"Incluye {mixtas} venta{'s' if mixtas != 1 else ''} con pago mixto, repartida{'s' if mixtas != 1 else ''} en cada medio."})
    return filas


def _serie(cur, id_tienda: int, ids: list[int], inicio: datetime, fin: datetime, dias: int) -> list[dict]:
    """Ventas y gastos de operacion por dia (o por mes en rangos largos)."""
    mensual = dias > DIAS_GRAFICA_DIARIA
    grupo = "DATE_FORMAT({c}, '%%Y-%%m-01')" if mensual else "DATE({c})"
    filtro, params = _en_sedes("v.id_sede", ids)
    cur.execute(
        f"SELECT {grupo.format(c='v.fecha_creacion')} AS dia, COUNT(*) AS n, COALESCE(SUM(v.total_final), 0) AS total "
        "FROM ventas v WHERE v.id_tienda = %s AND v.estado_venta = 'Pagada' "
        "AND v.fecha_creacion >= %s AND v.fecha_creacion < %s" + filtro + " GROUP BY dia",
        (id_tienda, inicio, fin) + params,
    )
    ventas = {str(f["dia"]): (int(f["n"]), float(f["total"])) for f in cur.fetchall()}
    filas = []
    dia = inicio.date().replace(day=1) if mensual else inicio.date()
    while dia < fin.date():
        clave = dia.isoformat()
        n, total = ventas.get(clave, (0, 0.0))
        etiqueta = (f"{MESES[dia.month - 1]} {dia.year}" if mensual
                    else f"{DIAS_SEMANA[dia.weekday()]} {dia.day} {MESES[dia.month - 1]}")
        filas.append({"dia": clave, "etiqueta": etiqueta, "ventas": n, "total": total})
        if mensual:
            dia = (dia.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            dia += timedelta(days=1)
    tope = max((f["total"] for f in filas), default=0) or 1
    for f in filas:
        f["pct"] = round(f["total"] / tope * 100, 1)
    return filas


def _agrupado(cur, sql: str, params: tuple) -> list[dict]:
    cur.execute(sql, params)
    filas = cur.fetchall()
    tope = max((float(f["total"]) for f in filas), default=0) or 1
    return [{**f, "total": float(f["total"]), "pct": round(float(f["total"]) / tope * 100, 1)} for f in filas]


def resumen(id_tienda: int, ids: list[int], per: dict) -> dict:
    """Estado de resultados del periodo y las ventas vistas de varias formas."""
    inicio, fin = per["inicio"], per["fin"]
    anterior_inicio = inicio - (fin - inicio)
    filtro, params = _en_sedes("v.id_sede", ids)
    base = (id_tienda, inicio, fin) + params
    donde = ("WHERE v.id_tienda = %s AND v.estado_venta = 'Pagada' "
             "AND v.fecha_creacion >= %s AND v.fecha_creacion < %s" + filtro)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        actual = _ventas_y_costo(cur, id_tienda, ids, inicio, fin)
        previo = _ventas_y_costo(cur, id_tienda, ids, anterior_inicio, inicio)
        metodos = _por_metodo(cur, id_tienda, ids, inicio, fin)
        serie = _serie(cur, id_tienda, ids, inicio, fin, per["dias"])
        por_tipo = _agrupado(
            cur,
            "SELECT pe.tipo, COUNT(*) AS n, COALESCE(SUM(v.total_final), 0) AS total "
            "FROM ventas v LEFT JOIN pedidos pe ON pe.id_pedido = v.id_pedido " + donde
            + " GROUP BY pe.tipo ORDER BY total DESC",
            base,
        )
        por_hora = _agrupado(
            cur,
            "SELECT HOUR(v.fecha_creacion) AS hora, COUNT(*) AS n, COALESCE(SUM(v.total_final), 0) AS total "
            "FROM ventas v " + donde + " GROUP BY hora ORDER BY hora",
            base,
        )
        por_sede = _agrupado(
            cur,
            "SELECT s.nombre, COUNT(*) AS n, COALESCE(SUM(v.total_final), 0) AS total "
            "FROM ventas v JOIN sedes s ON s.id_sede = v.id_sede " + donde
            + " GROUP BY v.id_sede, s.nombre ORDER BY total DESC",
            base,
        ) if len(ids) > 1 else []
    finally:
        conn.close()

    ventas = actual["ventas"]
    gastos = [
        {"clave": k, "nombre": CATEGORIAS[k], "n": actual["gastos"][k][0], "total": actual["gastos"][k][1],
         "operativo": k not in CATEGORIAS_NO_OPERATIVAS}
        for k in CATEGORIAS if k in actual["gastos"]
    ]
    for t in por_tipo:
        t["nombre"] = TIPOS_PEDIDO.get(t["tipo"], t["tipo"])
    return {
        **{k: v for k, v in actual.items() if k != "gastos"},
        "ticket_promedio": round(ventas / actual["num_ventas"], 2) if actual["num_ventas"] else 0,
        "venta_diaria": round(ventas / per["dias"], 2),
        "margen_bruto": round(actual["utilidad_bruta"] / ventas * 100, 1) if ventas else None,
        "margen_neto": round(actual["utilidad_neta"] / ventas * 100, 1) if ventas else None,
        "var_ventas": _variacion(ventas, previo["ventas"]),
        "var_utilidad": _variacion(actual["utilidad_neta"], previo["utilidad_neta"]),
        "gastos_categoria": gastos,
        "metodos": [m for m in metodos if "metodo" in m],
        "nota_metodos": next((m["nota"] for m in metodos if "nota" in m), None),
        "serie": serie,
        "serie_mensual": per["dias"] > DIAS_GRAFICA_DIARIA,
        "por_tipo": por_tipo,
        "por_hora": por_hora,
        "por_sede": por_sede,
        # Efectivo neto del periodo: lo que entro menos todo lo que salio
        # (compras incluidas). Propinas fuera: se reparten a los meseros.
        "flujo_caja": round(ventas - actual["gastos_operacion"] - actual["compras"], 2),
    }


# --- ventas por producto y por mesero ---------------------------------------------

def productos(id_tienda: int, ids: list[int], per: dict) -> list[dict]:
    filtro, params = _en_sedes("v.id_sede", ids)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT p.id_producto, p.nombre, c.nombre AS categoria, SUM(dv.cantidad) AS cantidad, "
            "SUM(dv.subtotal_linea) AS ventas, "
            "SUM(dv.cantidad * COALESCE(dv.costo_unitario_historico, p.precio_costo)) AS costo "
            "FROM detalle_ventas dv JOIN ventas v ON v.id_venta = dv.id_venta "
            "JOIN productos p ON p.id_producto = dv.id_producto "
            "LEFT JOIN categorias c ON c.id_categoria = p.id_categoria "
            "WHERE v.id_tienda = %s AND v.estado_venta = 'Pagada' AND v.fecha_creacion >= %s AND v.fecha_creacion < %s"
            + filtro + " GROUP BY p.id_producto, p.nombre, c.nombre ORDER BY ventas DESC",
            (id_tienda, per["inicio"], per["fin"]) + params,
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    total = sum(float(f["ventas"]) for f in filas) or 1
    salida = []
    for f in filas:
        ventas, costo = float(f["ventas"]), float(f["costo"] or 0)
        salida.append({
            "id_producto": f["id_producto"],
            "nombre": f["nombre"],
            "categoria": f["categoria"] or "",
            "cantidad": float(f["cantidad"]),
            "ventas": ventas,
            "costo": round(costo, 2),
            "utilidad": round(ventas - costo, 2),
            "margen": round((ventas - costo) / ventas * 100, 1) if ventas else None,
            "sin_costo": costo == 0,
            "participacion": round(ventas / total * 100, 1),
        })
    return salida


def meseros(id_tienda: int, ids: list[int], per: dict) -> list[dict]:
    filtro, params = _en_sedes("v.id_sede", ids)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT COALESCE(u.nombre_completo, 'Sin mesero') AS nombre, COUNT(*) AS n, "
            "COALESCE(SUM(v.total_final), 0) AS total, COALESCE(SUM(v.propina), 0) AS propinas "
            "FROM ventas v LEFT JOIN usuarios u ON u.id_usuario = v.id_mesero "
            "WHERE v.id_tienda = %s AND v.estado_venta = 'Pagada' AND v.fecha_creacion >= %s AND v.fecha_creacion < %s"
            + filtro + " GROUP BY v.id_mesero, u.nombre_completo ORDER BY total DESC",
            (id_tienda, per["inicio"], per["fin"]) + params,
        )
        return [{**f, "total": float(f["total"]), "propinas": float(f["propinas"])} for f in cur.fetchall()]
    finally:
        conn.close()


def _paginar(pagina, total: int, por_pagina: int) -> dict:
    paginas = max(1, -(-total // por_pagina))
    try:
        pagina = min(max(1, int(pagina)), paginas)
    except (TypeError, ValueError):
        pagina = 1
    return {"pagina": pagina, "paginas": paginas, "total": total, "por_pagina": por_pagina,
            "offset": (pagina - 1) * por_pagina}


# --- gastos ------------------------------------------------------------------------

def gastos(id_tienda: int, ids: list[int], per: dict, categoria: str | None, pagina, por_pagina: int = 30) -> dict:
    """Lista de gastos del periodo (activos y anulados) con totales."""
    filtro, params = _en_sedes("g.id_sede", ids)
    donde = "WHERE g.id_tienda = %s AND g.fecha_creacion >= %s AND g.fecha_creacion < %s" + filtro
    base = (id_tienda, per["inicio"], per["fin"]) + params
    if categoria in CATEGORIAS:
        donde += " AND g.categoria = %s"
        base += (categoria,)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN g.estado_activo = 1 THEN g.monto ELSE 0 END), 0) AS total "
            f"FROM gastos_caja g {donde}",
            base,
        )
        tot = cur.fetchone()
        pag = _paginar(pagina, int(tot["n"]), por_pagina)
        cur.execute(
            "SELECT g.id_gasto, g.concepto, g.categoria, g.descripcion, g.monto, g.monto_transferencia, g.metodo_pago, "
            "g.fuente_dinero, g.id_turno, g.fecha_creacion, g.estado_activo, g.anulado_en, g.motivo_anulacion, "
            "s.nombre AS sede, u.nombre_completo AS usuario "
            "FROM gastos_caja g LEFT JOIN sedes s ON s.id_sede = g.id_sede "
            f"LEFT JOIN usuarios u ON u.id_usuario = g.id_usuario {donde} "
            "ORDER BY g.fecha_creacion DESC, g.id_gasto DESC LIMIT %s OFFSET %s",
            base + (pag["por_pagina"], pag["offset"]),
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    lista = []
    for g in filas:
        metodo = g["metodo_pago"] or ("Efectivo" if g["fuente_dinero"] != "Bancos" else "Transferencia")
        lista.append({
            "id_gasto": g["id_gasto"],
            "fecha": g["fecha_creacion"].strftime("%Y-%m-%d %H:%M") if g["fecha_creacion"] else "",
            "concepto": g["concepto"],
            "descripcion": g["descripcion"] or "",
            "categoria": CATEGORIAS.get(g["categoria"], g["categoria"]),
            "monto": float(g["monto"]),
            "metodo": metodo,
            "origen": "Caja" if g["id_turno"] else ("Caja fuerte" if g["fuente_dinero"] == "Caja Fuerte" else "Banco"),
            "sede": g["sede"] or "",
            "usuario": g["usuario"] or "",
            "activo": bool(g["estado_activo"]),
            "motivo_anulacion": g["motivo_anulacion"] or "",
        })
    return {"gastos": lista, "total": float(tot["total"]), "paginacion": pag}


# --- cierres de caja ----------------------------------------------------------------

def cierres(id_tienda: int, ids: list[int], per: dict) -> dict:
    """Turnos de caja abiertos en el periodo, con su arqueo."""
    filtro, params = _en_sedes("t.id_sede", ids)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT t.id_turno, t.estado_turno, t.fecha_apertura, t.fecha_cierre, t.monto_inicial, "
            "t.monto_final_esperado, t.monto_final_real, t.observaciones, s.nombre AS sede, "
            "ua.nombre_completo AS abrio, uc.nombre_completo AS cerro, "
            "(SELECT COUNT(*) FROM ventas v WHERE v.id_turno = t.id_turno AND v.estado_venta = 'Pagada') AS ventas, "
            "(SELECT COALESCE(SUM(v.total_final), 0) FROM ventas v WHERE v.id_turno = t.id_turno "
            "   AND v.estado_venta = 'Pagada') AS total_ventas, "
            "(SELECT COALESCE(SUM(v.propina), 0) FROM ventas v WHERE v.id_turno = t.id_turno "
            "   AND v.estado_venta = 'Pagada') AS propinas, "
            "(SELECT COALESCE(SUM(g.monto), 0) FROM gastos_caja g WHERE g.id_turno = t.id_turno "
            "   AND g.estado_activo = 1) AS gastos "
            "FROM turnos_caja t LEFT JOIN sedes s ON s.id_sede = t.id_sede "
            "LEFT JOIN usuarios ua ON ua.id_usuario = t.id_usuario_apertura "
            "LEFT JOIN usuarios uc ON uc.id_usuario = t.id_usuario_cierre "
            "WHERE t.id_tienda = %s AND t.fecha_apertura >= %s AND t.fecha_apertura < %s" + filtro
            + " ORDER BY t.fecha_apertura DESC LIMIT 200",
            (id_tienda, per["inicio"], per["fin"]) + params,
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    turnos, faltante, sobrante = [], 0.0, 0.0
    for t in filas:
        esperado = float(t["monto_final_esperado"] if t["monto_final_esperado"] is not None else t["monto_inicial"])
        contado = None if t["monto_final_real"] is None else float(t["monto_final_real"])
        diferencia = None if contado is None or t["estado_turno"] != "Cerrado" else round(contado - esperado, 2)
        if diferencia is not None:
            if diferencia < 0:
                faltante += -diferencia
            else:
                sobrante += diferencia
        turnos.append({
            "id_turno": t["id_turno"],
            "abierto": t["estado_turno"] == "Abierto",
            "sede": t["sede"] or "",
            "apertura": t["fecha_apertura"].strftime("%Y-%m-%d %H:%M") if t["fecha_apertura"] else "",
            "cierre": t["fecha_cierre"].strftime("%Y-%m-%d %H:%M") if t["fecha_cierre"] else "",
            "abrio": t["abrio"] or "",
            "cerro": t["cerro"] or "",
            "base": float(t["monto_inicial"] or 0),
            "ventas": int(t["ventas"]),
            "total_ventas": float(t["total_ventas"]),
            "propinas": float(t["propinas"]),
            "gastos": float(t["gastos"]),
            "esperado": esperado,
            "contado": contado,
            "diferencia": diferencia,
            "observaciones": t["observaciones"] or "",
        })
    return {"turnos": turnos, "faltante": round(faltante, 2), "sobrante": round(sobrante, 2)}
