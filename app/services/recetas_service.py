"""Insumos, recetas y costo por plato (planes Completo y Cadena).

Plan "Recetas, insumos y costo por plato":

  - Un insumo se guarda en su unidad base (Gramo, Mililitro o Unidad). La
    compra puede ser en kilo, libra o litro y se convierte al guardar, asi la
    receta y el descuento nunca convierten.
  - El stock de insumos es por sede (stock_insumos_sedes) y se mueve con
    inventario_service, clase "insumo": mismo kardex inmutable que productos.
  - Costo por plato = suma de cantidad_requerida x costo_unitario de sus
    insumos activos. Se calcula al leer; ademas se copia a
    productos.precio_costo al guardar la receta y cada vez que cambia el costo
    de un insumo, para que las mermas y la utilidad usen el costo real.
  - Una compra con precio actualiza el costo del insumo con promedio
    ponderado sobre el stock de todas las sedes (el costo es uno por
    restaurante).
  - El inventario de un plato con receta se descuenta al enviar la comanda a
    cocina (pedidos_service), sumando el consumo por insumo antes de validar.
  - Soft delete en insumos y lineas de receta (estado_activo). Quitar y
    volver a poner un insumo en una receta reactiva la misma fila.
  - Un plato con receta (es_preparado) no lleva stock propio: es excluyente
    con productos.controla_stock.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from mysql.connector import IntegrityError

from app.services import inventario_service
from app.services.errores import Conflicto, NoEncontrado
from app.services.plan_service import tiene_funcion
from app.utils.validation import parse_float, parse_int, sanitize_optional_text, sanitize_text
from database import get_db

UNIDADES_BASE = ("Gramo", "Mililitro", "Unidad")
# Unidad de compra -> cuantas unidades base trae.
CONVERSIONES = {
    "Gramo": {"g": Decimal(1), "kg": Decimal(1000), "lb": Decimal("453.592")},
    "Mililitro": {"ml": Decimal(1), "l": Decimal(1000)},
    "Unidad": {"unidad": Decimal(1)},
}
NOMBRE_UNIDAD = {"g": "g", "kg": "kg", "lb": "lb", "ml": "ml", "l": "L", "unidad": "und"}
ABREVIATURA = {"Gramo": "g", "Mililitro": "ml", "Unidad": "und"}
MARGEN_ALERTA_DEFECTO = Decimal(35)
MAX_LINEAS_RECETA = 60
CANTIDAD_MAX = 1_000_000
COSTO_MAX = 10_000_000
MAX_MOVIMIENTOS = 200


def _texto_a_numero(valor, pesos: bool = False):
    """Lo que escribe la gente en Colombia: "1,5" kg, "2.000" g y "$20.000".
    La coma es decimal; el punto es de miles en pesos, cuando hay coma, o
    cuando le siguen exactamente tres digitos ("1.5" sigue siendo 1,5)."""
    if not isinstance(valor, str):
        return valor
    texto = valor.strip().replace("$", "").replace(" ", "")
    if pesos or "," in texto or re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "")
    return texto.replace(",", ".")


def _d3(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.001"))


def _d4(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _pesos(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def recetas_activas(cur, id_tienda: int) -> bool:
    """El plan del restaurante incluye recetas. Sin el, los platos se venden
    sin descontar insumos."""
    cur.execute("SELECT plan_id FROM tiendas WHERE id_tienda = %s", (id_tienda,))
    return tiene_funcion((cur.fetchone() or {}).get("plan_id"), "recetas")


# --- insumos -----------------------------------------------------------------

def _insumo(cur, id_tienda: int, id_insumo: int, bloquear: bool = False) -> dict:
    cur.execute(
        "SELECT id_insumo, nombre, unidad_medida, costo_unitario, stock_minimo_alerta FROM insumos "
        "WHERE id_insumo = %s AND id_tienda = %s AND estado_activo = 1" + (" FOR UPDATE" if bloquear else ""),
        (id_insumo, id_tienda),
    )
    fila = cur.fetchone()
    if not fila:
        raise NoEncontrado("Insumo no encontrado.")
    return fila


def _estado(stock: Decimal, minimo: Decimal | None) -> str:
    if stock <= 0:
        return "agotado"
    if minimo is not None and stock <= minimo:
        return "bajo"
    return "ok"


def listar_insumos(id_tienda: int, id_sede: int) -> list[dict]:
    """Insumos activos con el stock de la sede, el minimo, el costo y en
    cuantas recetas activas estan."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT i.id_insumo, i.nombre, i.unidad_medida, i.costo_unitario, i.stock_minimo_alerta, "
            "COALESCE(s.stock_actual, 0) AS stock_actual, "
            "(SELECT COUNT(*) FROM recetas_productos r JOIN productos p ON p.id_producto = r.id_producto "
            " WHERE r.id_insumo = i.id_insumo AND r.estado_activo = 1 AND p.estado_activo = 1) AS en_recetas "
            "FROM insumos i "
            "LEFT JOIN stock_insumos_sedes s ON s.id_insumo = i.id_insumo AND s.id_sede = %s "
            "WHERE i.id_tienda = %s AND i.estado_activo = 1 ORDER BY i.nombre",
            (id_sede, id_tienda),
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    for f in filas:
        stock = _d3(f["stock_actual"])
        minimo = _d3(f["stock_minimo_alerta"]) if f["stock_minimo_alerta"] is not None else None
        f["estado"] = _estado(stock, minimo)
        f["stock_actual"] = float(stock)
        f["stock_minimo_alerta"] = float(minimo) if minimo is not None else None
        f["costo_unitario"] = float(f["costo_unitario"])
        f["abreviatura"] = ABREVIATURA[f["unidad_medida"]]
        f["unidades_compra"] = list(CONVERSIONES[f["unidad_medida"]])
    return filas


def _campos_insumo(data: dict, crear: bool) -> dict:
    campos = {
        "nombre": sanitize_text(data.get("nombre"), "El nombre", max_len=150),
        "costo_unitario": _d4(parse_float(_texto_a_numero(data.get("costo_unitario")) or 0, "El costo",
                                          min_value=0, max_value=COSTO_MAX)),
    }
    minimo = data.get("stock_minimo_alerta")
    campos["stock_minimo_alerta"] = (
        _d3(parse_float(_texto_a_numero(minimo), "El mínimo", min_value=0, max_value=CANTIDAD_MAX)) if minimo not in (None, "") else None
    )
    if crear:
        unidad = str(data.get("unidad_medida") or "")
        if unidad not in UNIDADES_BASE:
            raise ValueError("Unidad invalida: Gramo, Mililitro o Unidad.")
        campos["unidad_medida"] = unidad
    return campos


def crear_insumo(id_tienda: int, data: dict) -> int:
    c = _campos_insumo(data, crear=True)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO insumos (id_tienda, nombre, unidad_medida, costo_unitario, stock_minimo_alerta) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id_tienda, c["nombre"], c["unidad_medida"], c["costo_unitario"], c["stock_minimo_alerta"]),
        )
        id_insumo = cur.lastrowid
        conn.commit()
        return id_insumo
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto(f"Ya existe un insumo llamado {c['nombre']}.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def actualizar_insumo(id_tienda: int, id_insumo: int, data: dict) -> None:
    """La unidad base no cambia: el stock y las recetas estan en ella."""
    c = _campos_insumo(data, crear=False)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        anterior = _insumo(cur, id_tienda, id_insumo, bloquear=True)
        cur.execute(
            "UPDATE insumos SET nombre = %s, costo_unitario = %s, stock_minimo_alerta = %s WHERE id_insumo = %s",
            (c["nombre"], c["costo_unitario"], c["stock_minimo_alerta"], id_insumo),
        )
        if _d4(anterior["costo_unitario"]) != c["costo_unitario"]:
            _refrescar_costos(cur, id_tienda, id_insumo)
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto(f"Ya existe un insumo llamado {c['nombre']}.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def eliminar_insumo(id_tienda: int, id_insumo: int) -> None:
    """Soft delete. Si un plato activo lo usa, primero hay que quitarlo de
    esas recetas: un plato no puede quedar con un ingrediente fantasma."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        _insumo(cur, id_tienda, id_insumo, bloquear=True)
        cur.execute(
            "SELECT p.nombre FROM recetas_productos r JOIN productos p ON p.id_producto = r.id_producto "
            "WHERE r.id_insumo = %s AND r.estado_activo = 1 AND p.estado_activo = 1 ORDER BY p.nombre",
            (id_insumo,),
        )
        platos = [f["nombre"] for f in cur.fetchall()]
        if platos:
            raise Conflicto("Quítalo primero de la receta de: " + ", ".join(platos) + ".")
        cur.execute("UPDATE insumos SET estado_activo = 0 WHERE id_insumo = %s", (id_insumo,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def a_unidad_base(unidad_base: str, cantidad, unidad_compra: str | None) -> Decimal:
    """Cantidad en la unidad de compra -> unidad base. 2 kg -> 2000 g."""
    unidad_compra = (unidad_compra or "").strip().lower() or next(iter(CONVERSIONES[unidad_base]))
    factor = CONVERSIONES[unidad_base].get(unidad_compra)
    if factor is None:
        raise ValueError(f"No se puede comprar {unidad_base.lower()} por {unidad_compra}.")
    return _d3(Decimal(str(cantidad)) * factor)


def registrar_movimiento_insumo(id_tienda: int, id_sede: int, id_usuario: int, id_insumo: int, data: dict) -> dict:
    """Entrada (compra), Salida (merma, consumo interno) o Ajuste (conteo:
    `cantidad` es lo contado). Una Entrada con `precio_total` recalcula el
    costo con promedio ponderado. Devuelve el stock y el costo nuevos."""
    tipo = str(data.get("tipo") or "")
    if tipo not in inventario_service.TIPOS_MOVIMIENTO:
        raise ValueError("Tipo de movimiento invalido.")
    cantidad_compra = parse_float(_texto_a_numero(data.get("cantidad")), "La cantidad", min_value=0, max_value=CANTIDAD_MAX)
    precio_total = _texto_a_numero(data.get("precio_total"), pesos=True)
    precio_total = (
        _pesos(parse_float(precio_total, "El precio", min_value=0, max_value=COSTO_MAX * 10))
        if tipo == "Entrada" and precio_total not in (None, "") else None
    )
    motivo = sanitize_optional_text(data.get("motivo"), "El motivo", max_len=200)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        insumo = _insumo(cur, id_tienda, id_insumo, bloquear=True)
        cantidad = a_unidad_base(insumo["unidad_medida"], cantidad_compra, data.get("unidad"))
        if tipo != "Ajuste" and cantidad <= 0:
            raise ValueError("La cantidad debe ser mayor a cero.")
        antes = inventario_service._bloquear_stock(cur, id_sede, id_insumo, "insumo")
        costo = _d4(insumo["costo_unitario"])
        if tipo == "Entrada":
            despues = antes + cantidad
            if precio_total is not None:
                costo = _costo_promedio(cur, id_insumo, costo, cantidad, precio_total)
                if costo != _d4(insumo["costo_unitario"]):
                    cur.execute("UPDATE insumos SET costo_unitario = %s WHERE id_insumo = %s", (costo, id_insumo))
                    _refrescar_costos(cur, id_tienda, id_insumo)
        elif tipo == "Salida":
            if cantidad > antes:
                raise Conflicto(f"Solo hay {antes:g} {ABREVIATURA[insumo['unidad_medida']]} en esta sede.")
            despues = antes - cantidad
        else:
            despues = cantidad
            cantidad = abs(despues - antes)
        if precio_total is not None and not motivo:
            motivo = f"Compra por ${int(precio_total):,}".replace(",", ".")
        inventario_service._movimiento(cur, id_tienda, id_sede, id_insumo, id_usuario, tipo, cantidad, antes, despues,
                                       motivo or tipo, "insumo")
        conn.commit()
        return {"stock": float(despues), "costo_unitario": float(costo), "unidad": ABREVIATURA[insumo["unidad_medida"]]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _costo_promedio(cur, id_insumo: int, costo: Decimal, cantidad: Decimal, precio_total: Decimal) -> Decimal:
    """(stock x costo + precio pagado) / (stock + cantidad), con el stock de
    todas las sedes (el costo es uno por restaurante). Lo negativo cuenta
    como cero. 500 g a $16 + 1 kg por $20.000 -> $18,6667/g."""
    cur.execute(
        "SELECT COALESCE(SUM(GREATEST(stock_actual, 0)), 0) AS total FROM stock_insumos_sedes WHERE id_insumo = %s",
        (id_insumo,),
    )
    existente = _d3(cur.fetchone()["total"])
    if existente + cantidad <= 0:
        return costo
    return _d4((existente * costo + precio_total) / (existente + cantidad))


def movimientos_insumo(id_tienda: int, id_sede: int, id_insumo: int) -> dict:
    """Kardex del insumo en la sede, lo mas reciente primero."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        insumo = _insumo(cur, id_tienda, id_insumo)
        cur.execute(
            "SELECT m.fecha_creacion, m.tipo_movimiento, m.motivo, m.cantidad, m.stock_anterior, m.stock_posterior, "
            "u.nombre_completo AS usuario "
            "FROM movimientos_inventario m LEFT JOIN usuarios u ON u.id_usuario = m.id_usuario "
            "WHERE m.id_insumo = %s AND m.id_sede = %s ORDER BY m.id_movimiento DESC LIMIT %s",
            (id_insumo, id_sede, MAX_MOVIMIENTOS),
        )
        movimientos = cur.fetchall()
    finally:
        conn.close()
    for m in movimientos:
        for campo in ("cantidad", "stock_anterior", "stock_posterior"):
            m[campo] = float(m[campo] or 0)
    insumo["abreviatura"] = ABREVIATURA[insumo["unidad_medida"]]
    return {"insumo": insumo, "movimientos": movimientos}


# --- recetas -----------------------------------------------------------------

def _lineas_receta(cur, id_producto: int) -> list[dict]:
    cur.execute(
        "SELECT r.id_insumo, r.cantidad_requerida, i.nombre, i.unidad_medida, i.costo_unitario "
        "FROM recetas_productos r JOIN insumos i ON i.id_insumo = r.id_insumo "
        "WHERE r.id_producto = %s AND r.estado_activo = 1 AND i.estado_activo = 1 ORDER BY i.nombre",
        (id_producto,),
    )
    return cur.fetchall()


def _costo(lineas: list[dict]) -> Decimal:
    return _pesos(sum((Decimal(l["cantidad_requerida"]) * Decimal(l["costo_unitario"]) for l in lineas), Decimal(0)))


def _refrescar_costos(cur, id_tienda: int, id_insumo: int) -> None:
    """Copia el costo de receta nuevo a productos.precio_costo de los platos
    que usan el insumo."""
    cur.execute(
        "SELECT DISTINCT p.id_producto FROM recetas_productos r JOIN productos p ON p.id_producto = r.id_producto "
        "WHERE r.id_insumo = %s AND r.estado_activo = 1 AND p.id_tienda = %s AND p.es_preparado = 1",
        (id_insumo, id_tienda),
    )
    for fila in cur.fetchall():
        costo = _costo(_lineas_receta(cur, fila["id_producto"]))
        cur.execute("UPDATE productos SET precio_costo = %s WHERE id_producto = %s", (costo, fila["id_producto"]))


def _producto(cur, id_tienda: int, id_producto: int, bloquear: bool = False) -> dict:
    cur.execute(
        "SELECT id_producto, nombre, precio_venta, precio_costo, controla_stock, es_preparado FROM productos "
        "WHERE id_producto = %s AND id_tienda = %s AND estado_activo = 1" + (" FOR UPDATE" if bloquear else ""),
        (id_producto, id_tienda),
    )
    fila = cur.fetchone()
    if not fila:
        raise NoEncontrado("Plato no encontrado.")
    return fila


def _margen_alerta(cur, id_tienda: int) -> Decimal:
    cur.execute("SELECT margen_alerta_costo FROM tiendas WHERE id_tienda = %s", (id_tienda,))
    fila = cur.fetchone()
    return Decimal(fila["margen_alerta_costo"]) if fila else MARGEN_ALERTA_DEFECTO


def _resumen_costo(costo: Decimal, precio: Decimal, margen: Decimal) -> dict:
    pct = (costo * 100 / precio) if precio > 0 else None
    return {
        "costo": float(costo),
        "precio_venta": float(precio),
        "utilidad": float(precio - costo),
        "pct_costo": round(float(pct), 1) if pct is not None else None,
        # Sin precio no hay % que calcular, pero un plato con costo y sin precio igual pierde.
        "alerta": (pct > margen) if pct is not None else costo > 0,
    }


def ver_receta(id_tienda: int, id_sede: int, id_producto: int) -> dict:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        producto = _producto(cur, id_tienda, id_producto)
        lineas = _lineas_receta(cur, id_producto)
        margen = _margen_alerta(cur, id_tienda)
        disponibles = disponibles_por_plato(cur, id_sede, [id_producto]).get(id_producto)
    finally:
        conn.close()
    costo = _costo(lineas)
    return {
        "producto": {"id_producto": producto["id_producto"], "nombre": producto["nombre"],
                     "controla_stock": bool(producto["controla_stock"])},
        "lineas": [{
            "id_insumo": l["id_insumo"],
            "nombre": l["nombre"],
            "cantidad": float(l["cantidad_requerida"]),
            "abreviatura": ABREVIATURA[l["unidad_medida"]],
            "costo_unitario": float(l["costo_unitario"]),
            "costo_linea": float(_pesos(Decimal(l["cantidad_requerida"]) * Decimal(l["costo_unitario"]))),
        } for l in lineas],
        "disponibles": disponibles,
        "margen_alerta": float(margen),
        **_resumen_costo(costo, Decimal(producto["precio_venta"]), margen),
    }


def _lineas_validas(data: dict) -> dict[int, Decimal]:
    lineas = data.get("lineas")
    if not isinstance(lineas, list):
        raise ValueError("Receta invalida.")
    if len(lineas) > MAX_LINEAS_RECETA:
        raise ValueError(f"Máximo {MAX_LINEAS_RECETA} ingredientes por receta.")
    limpias: dict[int, Decimal] = defaultdict(Decimal)
    for linea in lineas:
        if not isinstance(linea, dict):
            raise ValueError("Ingrediente invalido.")
        id_insumo = parse_int(linea.get("id_insumo"), "Insumo", min_value=1)
        cantidad = _d3(parse_float(_texto_a_numero(linea.get("cantidad")), "La cantidad", min_value=0, max_value=CANTIDAD_MAX))
        if cantidad <= 0:
            raise ValueError("Cada ingrediente necesita una cantidad mayor a cero.")
        # El mismo insumo dos veces (ej. queso arriba y adentro) se suma.
        limpias[id_insumo] += cantidad
    return dict(limpias)


def guardar_receta(id_tienda: int, id_producto: int, data: dict) -> dict:
    """Reemplaza la receta completa. Con ingredientes el plato queda
    `es_preparado` y su costo se copia a precio_costo; sin ingredientes deja
    de ser preparado (y conserva su ultimo costo)."""
    nuevas = _lineas_validas(data)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        producto = _producto(cur, id_tienda, id_producto, bloquear=True)
        if nuevas and producto["controla_stock"]:
            raise Conflicto(
                f"{producto['nombre']} lleva inventario propio. Quítale \"Lleva inventario\" en la carta "
                "para descontar sus ingredientes."
            )
        if nuevas:
            marcadores = ", ".join(["%s"] * len(nuevas))
            cur.execute(
                f"SELECT id_insumo FROM insumos WHERE id_tienda = %s AND estado_activo = 1 AND id_insumo IN ({marcadores})",
                (id_tienda, *nuevas),
            )
            if len(cur.fetchall()) != len(nuevas):
                raise NoEncontrado("Insumo no encontrado.")
        cur.execute(
            "UPDATE recetas_productos SET estado_activo = 0 WHERE id_producto = %s AND estado_activo = 1",
            (id_producto,),
        )
        for id_insumo, cantidad in sorted(nuevas.items()):
            cur.execute(
                "INSERT INTO recetas_productos (id_producto, id_insumo, cantidad_requerida, estado_activo) "
                "VALUES (%s, %s, %s, 1) "
                "ON DUPLICATE KEY UPDATE cantidad_requerida = VALUES(cantidad_requerida), estado_activo = 1",
                (id_producto, id_insumo, cantidad),
            )
        costo = _costo(_lineas_receta(cur, id_producto))
        if nuevas:
            cur.execute("UPDATE productos SET es_preparado = 1, precio_costo = %s WHERE id_producto = %s",
                        (costo, id_producto))
        else:
            cur.execute("UPDATE productos SET es_preparado = 0 WHERE id_producto = %s", (id_producto,))
        margen = _margen_alerta(cur, id_tienda)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ingredientes": len(nuevas), **_resumen_costo(costo, Decimal(producto["precio_venta"]), margen)}


def platos(id_tienda: int, id_sede: int) -> list[dict]:
    """Platos activos que no llevan inventario propio, con su receta
    resumida, costo, margen y cuantos alcanzan en la sede."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        margen = _margen_alerta(cur, id_tienda)
        cur.execute(
            "SELECT p.id_producto, p.nombre, p.precio_venta, p.es_preparado, c.nombre AS categoria "
            "FROM productos p LEFT JOIN categorias c ON c.id_categoria = p.id_categoria "
            "WHERE p.id_tienda = %s AND p.estado_activo = 1 AND p.controla_stock = 0 "
            "ORDER BY p.es_preparado DESC, c.nombre IS NULL, c.nombre, p.nombre",
            (id_tienda,),
        )
        filas = cur.fetchall()
        ids = [f["id_producto"] for f in filas if f["es_preparado"]]
        costos: dict[int, Decimal] = defaultdict(Decimal)
        ingredientes: dict[int, int] = defaultdict(int)
        if ids:
            marcadores = ", ".join(["%s"] * len(ids))
            cur.execute(
                f"SELECT r.id_producto, r.cantidad_requerida, i.costo_unitario FROM recetas_productos r "
                f"JOIN insumos i ON i.id_insumo = r.id_insumo "
                f"WHERE r.estado_activo = 1 AND i.estado_activo = 1 AND r.id_producto IN ({marcadores})",
                tuple(ids),
            )
            for l in cur.fetchall():
                costos[l["id_producto"]] += Decimal(l["cantidad_requerida"]) * Decimal(l["costo_unitario"])
                ingredientes[l["id_producto"]] += 1
        disponibles = disponibles_por_plato(cur, id_sede, ids)
    finally:
        conn.close()
    resultado = []
    for f in filas:
        pid = f["id_producto"]
        fila = {
            "id_producto": pid,
            "nombre": f["nombre"],
            "categoria": f["categoria"],
            "es_preparado": bool(f["es_preparado"]),
            "ingredientes": ingredientes.get(pid, 0),
            "disponibles": disponibles.get(pid),
        }
        fila.update(_resumen_costo(_pesos(costos.get(pid, 0)), Decimal(f["precio_venta"]), margen))
        resultado.append(fila)
    return resultado


def costos(id_tienda: int, id_sede: int) -> dict:
    """Solo los platos con receta, del que mas pesa su costo al que menos."""
    lista = [p for p in platos(id_tienda, id_sede) if p["es_preparado"]]
    lista.sort(key=lambda p: (p["pct_costo"] is not None, p["pct_costo"] or 0), reverse=True)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        margen = _margen_alerta(cur, id_tienda)
    finally:
        conn.close()
    return {"platos": lista, "margen_alerta": float(margen), "en_alerta": sum(1 for p in lista if p["alerta"])}


def guardar_margen(id_tienda: int, data: dict) -> float:
    margen = Decimal(str(parse_float(_texto_a_numero(data.get("margen_alerta")), "El porcentaje", min_value=1, max_value=100)))
    margen = margen.quantize(Decimal("0.01"))
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tiendas SET margen_alerta_costo = %s WHERE id_tienda = %s", (margen, id_tienda))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return float(margen)


# --- descuento al enviar a cocina --------------------------------------------

def consumo_insumos(cur, cantidades: dict[int, Decimal]) -> dict[int, Decimal]:
    """{id_producto: cantidad pedida} -> {id_insumo: cantidad a descontar},
    sumado por insumo (dos platos con el mismo queso se validan juntos).
    Solo cuentan los platos con receta y los insumos activos."""
    if not cantidades:
        return {}
    marcadores = ", ".join(["%s"] * len(cantidades))
    cur.execute(
        f"SELECT r.id_producto, r.id_insumo, r.cantidad_requerida FROM recetas_productos r "
        f"JOIN productos p ON p.id_producto = r.id_producto "
        f"JOIN insumos i ON i.id_insumo = r.id_insumo "
        f"WHERE r.estado_activo = 1 AND i.estado_activo = 1 AND p.es_preparado = 1 "
        f"AND r.id_producto IN ({marcadores})",
        tuple(cantidades),
    )
    consumo: dict[int, Decimal] = defaultdict(Decimal)
    for fila in cur.fetchall():
        consumo[fila["id_insumo"]] += _d3(Decimal(fila["cantidad_requerida"]) * cantidades[fila["id_producto"]])
    return dict(consumo)


def disponibles_por_plato(cur, id_sede: int, ids: list[int]) -> dict[int, int]:
    """Cuantos platos alcanzan con el stock de la sede: el minimo de
    stock / cantidad_requerida entre sus insumos. Un plato sin receta no
    aparece."""
    if not ids:
        return {}
    marcadores = ", ".join(["%s"] * len(ids))
    cur.execute(
        f"SELECT r.id_producto, r.cantidad_requerida, COALESCE(s.stock_actual, 0) AS stock "
        f"FROM recetas_productos r JOIN insumos i ON i.id_insumo = r.id_insumo "
        f"LEFT JOIN stock_insumos_sedes s ON s.id_insumo = r.id_insumo AND s.id_sede = %s "
        f"WHERE r.estado_activo = 1 AND i.estado_activo = 1 AND r.id_producto IN ({marcadores})",
        (id_sede, *ids),
    )
    alcanza: dict[int, int] = {}
    for fila in cur.fetchall():
        n = max(0, int(Decimal(fila["stock"]) // Decimal(fila["cantidad_requerida"])))
        alcanza[fila["id_producto"]] = min(n, alcanza.get(fila["id_producto"], n))
    return alcanza


def alertas(id_tienda: int, id_sede: int) -> dict:
    """La franja de alertas: insumos en o bajo su minimo (o agotados) y
    platos que no alcanzan para uno solo en la sede."""
    insumos = listar_insumos(id_tienda, id_sede)
    lista_platos = [p for p in platos(id_tienda, id_sede) if p["es_preparado"]]
    return {
        "insumos_bajos": sum(1 for i in insumos if i["estado"] != "ok"),
        "platos_agotados": [p["nombre"] for p in lista_platos if p["disponibles"] == 0],
    }
