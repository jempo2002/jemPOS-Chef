"""Domicilios: domiciliarios de la sede, despacho y recaudo del efectivo.

Ciclo de un domicilio (pedido_domicilios.estado):

  por_despachar  se toma como un para llevar, con direccion (pedidos_service).
  despachado     sale con un domiciliario y el metodo con que va a pagar el
                 cliente. Desde aqui no se le agregan platos ni se cobra en la
                 pantalla del pedido.
  entregado      el domiciliario se lo entrego al cliente.
  liquidado      el cajero recibio la plata: si el pedido no estaba cobrado se
                 crea la venta en la caja abierta (el efectivo entra al cajon
                 en ese momento), y el domicilio sale del panel.

Lo que un domiciliario debe entregar al regresar es la suma de sus domicilios
despachados o entregados, sin liquidar y sin cobrar, con metodo 'Efectivo'
(todo el domicilio) o 'Mixto' (solo su parte en efectivo).
Se calcula en cada consulta desde los platos del pedido (nada guardado que se
pueda desfasar si un Admin anula un plato en el camino). El indice
(id_sede, estado, id_domiciliario) deja fuera lo ya liquidado, asi que el
historico no la vuelve lenta.

Si el cliente pago antes de que saliera (se cobro en la pantalla del pedido),
el domiciliario no cobra nada: el domicilio se liquida sin crear venta.

Quien hace que (rutas en app/routes/domicilios.py):
  Mesero, Cajero, Admin  tomar, despachar, marcar entregado
  Cajero, Admin          recibir el recaudo, cambiar el metodo de pago,
                         regresar sin entregar, domiciliarios
  Admin                  tope de efectivo
"""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from mysql.connector import IntegrityError

from app.services import caja_service
from app.services.errores import Conflicto, ErrorServicio, NoEncontrado
from app.services.pedidos_service import (
    _auditoria,
    _cerrar_pedido,
    _lineas,
    _pesos,
    _registrar_venta,
    _telefono,
    fila_domicilio,
)
from app.utils.helpers import ahora_local
from app.utils.validation import parse_float, parse_int, sanitize_text
from database import get_db

MAX_DOMICILIARIOS = 50
MAX_POR_RECAUDO = 100
MONTO_MAX = 100_000_000
# Lo que manda la pantalla -> pedido_domicilios.metodo_pago
METODOS = {
    "efectivo": "Efectivo",
    "nequi": "Nequi/Daviplata",
    "transferencia": "Nequi/Daviplata",
    "tarjeta": "Tarjeta",
    "mixto": "Mixto",
}
EN_CALLE = ("despachado", "entregado")
ROLES_CAJA = ("Admin", "Cajero")
_SIN_COBRAR = ("abierto", "por_cobrar")


def _metodo(valor) -> str:
    metodo = METODOS.get(str(valor or "").strip().lower())
    if not metodo:
        raise ValueError("Método de pago invalido.")
    return metodo


def _monto(valor, etiqueta: str) -> Decimal:
    return _pesos(parse_float(valor, etiqueta, min_value=0, max_value=MONTO_MAX))


def _efectivo_mixto(data: dict, total: Decimal) -> Decimal:
    """En Mixto, cuanto paga el cliente en efectivo: mas de 0 y menos que el
    total (el resto va por transferencia)."""
    efectivo = _monto(data.get("monto_efectivo"), "El efectivo")
    if not 0 < efectivo < total:
        raise ValueError(f"En Mixto, el efectivo debe estar entre $1 y ${int(total) - 1:,}.".replace(",", "."))
    return efectivo


def _efectivo_de(metodo: str, cobrar: Decimal, monto_efectivo) -> Decimal:
    """Lo que el domiciliario recibe en billetes de un domicilio."""
    if metodo == "Efectivo":
        return cobrar
    if metodo == "Mixto" and cobrar:
        return min(Decimal(monto_efectivo or 0), cobrar)
    return Decimal(0)


# --- domiciliarios ----------------------------------------------------------

def listar_domiciliarios(id_sede: int) -> list[dict]:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id_domiciliario, nombre, telefono FROM domiciliarios "
            "WHERE id_sede = %s AND estado_activo = 1 ORDER BY nombre",
            (id_sede,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def crear_domiciliario(id_tienda: int, id_sede: int, data: dict) -> int:
    nombre = sanitize_text(data.get("nombre"), "El nombre", max_len=60)
    telefono = _telefono(data.get("telefono"))
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS n FROM domiciliarios WHERE id_sede = %s AND estado_activo = 1", (id_sede,))
        if cur.fetchone()["n"] >= MAX_DOMICILIARIOS:
            raise Conflicto(f"Una sede puede tener hasta {MAX_DOMICILIARIOS} domiciliarios.")
        cur.execute(
            "INSERT INTO domiciliarios (id_tienda, id_sede, nombre, telefono) VALUES (%s, %s, %s, %s)",
            (id_tienda, id_sede, nombre, telefono),
        )
        id_domiciliario = cur.lastrowid
        conn.commit()
        return id_domiciliario
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("Ya hay un domiciliario con ese nombre.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def actualizar_domiciliario(id_sede: int, id_domiciliario: int, data: dict) -> None:
    nombre = sanitize_text(data.get("nombre"), "El nombre", max_len=60)
    telefono = _telefono(data.get("telefono"))
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE domiciliarios SET nombre = %s, telefono = %s "
            "WHERE id_domiciliario = %s AND id_sede = %s AND estado_activo = 1",
            (nombre, telefono, id_domiciliario, id_sede),
        )
        if cur.rowcount == 0:
            cur.execute(
                "SELECT 1 FROM domiciliarios WHERE id_domiciliario = %s AND id_sede = %s AND estado_activo = 1",
                (id_domiciliario, id_sede),
            )
            if not cur.fetchone():
                raise NoEncontrado("Domiciliario no encontrado.")
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("Ya hay un domiciliario con ese nombre.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def eliminar_domiciliario(id_sede: int, id_domiciliario: int) -> None:
    """Borrado suave. No se puede mientras tenga domicilios sin liquidar."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM domiciliarios WHERE id_domiciliario = %s AND id_sede = %s AND estado_activo = 1 FOR UPDATE",
            (id_domiciliario, id_sede),
        )
        if not cur.fetchone():
            raise NoEncontrado("Domiciliario no encontrado.")
        cur.execute(
            "SELECT 1 FROM pedido_domicilios WHERE id_sede = %s AND estado IN ('despachado', 'entregado') "
            "AND id_domiciliario = %s LIMIT 1",
            (id_sede, id_domiciliario),
        )
        if cur.fetchone():
            raise Conflicto("Tiene domicilios sin liquidar. Recibe su recaudo primero.")
        cur.execute("UPDATE domiciliarios SET estado_activo = 0 WHERE id_domiciliario = %s", (id_domiciliario,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _domiciliario_activo(cur, id_sede: int, id_domiciliario) -> dict:
    id_domiciliario = parse_int(id_domiciliario, "Domiciliario", min_value=1)
    cur.execute(
        "SELECT id_domiciliario, nombre, telefono FROM domiciliarios "
        "WHERE id_domiciliario = %s AND id_sede = %s AND estado_activo = 1",
        (id_domiciliario, id_sede),
    )
    fila = cur.fetchone()
    if not fila:
        raise NoEncontrado("Domiciliario no encontrado.")
    return fila


# --- consultas ---------------------------------------------------------------

# Una fila por domicilio con su total (mismo redondeo por linea que la cuenta).
# Siempre filtra por sede y estado: entra por idx_pedido_domicilios_recaudo.
_DOMICILIOS = (
    "SELECT d.id_pedido, p.uuid_cliente AS uuid, p.numero_llevar AS numero, p.cliente_nombre AS cliente, "
    "p.cliente_telefono AS telefono, p.estado AS estado_pedido, d.direccion, d.estado, d.metodo_pago, "
    "d.monto_efectivo, d.paga_con, "
    "d.id_domiciliario, m.nombre AS domiciliario, d.despachado_en, "
    "TIMESTAMPDIFF(MINUTE, p.abierto_en, NOW()) AS minutos, "
    "TIMESTAMPDIFF(MINUTE, d.despachado_en, NOW()) AS minutos_en_calle, "
    "COALESCE(SUM(ROUND(i.cantidad * i.precio_unitario)), 0) AS total, "
    "COALESCE(SUM(i.estado = 'pendiente'), 0) AS sin_enviar, "
    "COALESCE(SUM(i.estado = 'listo'), 0) AS listos, "
    "COALESCE(SUM(i.estado IN ('enviado', 'listo')), 0) AS en_cocina "
    "FROM pedido_domicilios d "
    "JOIN pedidos p ON p.id_pedido = d.id_pedido "
    "LEFT JOIN domiciliarios m ON m.id_domiciliario = d.id_domiciliario "
    "LEFT JOIN pedido_items i ON i.id_pedido = d.id_pedido AND i.estado <> 'anulado' "
)


def _json_domicilio(f: dict) -> dict:
    total = float(f["total"])
    pagado = f["estado_pedido"] not in _SIN_COBRAR
    cobrar = 0.0 if pagado else total
    paga_con = float(f["paga_con"]) if f["paga_con"] is not None else None
    efectivo = float(_efectivo_de(f["metodo_pago"], Decimal(str(cobrar)), f["monto_efectivo"]))
    return {
        "id_pedido": f["id_pedido"],
        "uuid": f["uuid"],
        "numero": f["numero"],
        "cliente": f["cliente"],
        "telefono": f["telefono"],
        "direccion": f["direccion"],
        "estado": f["estado"],
        "metodo_pago": f["metodo_pago"],
        "pagado": pagado,
        "total": total,
        "cobrar": cobrar,
        "efectivo": efectivo,
        "transferencia": cobrar - efectivo,
        "paga_con": paga_con,
        "vuelto": max(0.0, paga_con - efectivo) if paga_con and efectivo else 0.0,
        "id_domiciliario": f["id_domiciliario"],
        "domiciliario": f["domiciliario"],
        "despachado_a_las": f["despachado_en"].strftime("%H:%M") if f["despachado_en"] else None,
        "minutos": int(f["minutos"] or 0),
        "minutos_en_calle": int(f["minutos_en_calle"]) if f["minutos_en_calle"] is not None else None,
        "sin_enviar": int(f["sin_enviar"]),
        "listos": int(f["listos"]),
        "en_cocina": int(f["en_cocina"]),
    }


def listar(id_sede: int) -> dict:
    """Domicilios sin liquidar de la sede (por despachar y en la calle) y los
    domiciliarios activos. Pantalla de despachos."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            _DOMICILIOS + "WHERE d.id_sede = %s AND d.estado IN ('por_despachar', 'despachado', 'entregado') "
            "AND p.estado <> 'anulado' GROUP BY d.id_pedido ORDER BY d.id_pedido",
            (id_sede,),
        )
        domicilios = [_json_domicilio(f) for f in cur.fetchall()]
        cur.execute(
            "SELECT id_domiciliario, nombre, telefono FROM domiciliarios "
            "WHERE id_sede = %s AND estado_activo = 1 ORDER BY nombre",
            (id_sede,),
        )
        domiciliarios = cur.fetchall()
    finally:
        conn.close()
    return {"domicilios": domicilios, "domiciliarios": domiciliarios}


def efectivo_por_entregar(cur, id_sede: int, id_domiciliario: int) -> dict:
    """Lo que un domiciliario debe entregar en efectivo ahora mismo y cuantos
    domicilios lleva. Solo cuenta lo despachado/entregado sin liquidar y sin
    cobrar: en 'Efectivo' todo el domicilio, en 'Mixto' su parte en efectivo."""
    cur.execute(
        "SELECT d.metodo_pago, d.monto_efectivo, COALESCE(SUM(ROUND(i.cantidad * i.precio_unitario)), 0) AS total "
        "FROM pedido_domicilios d "
        "JOIN pedidos p ON p.id_pedido = d.id_pedido "
        "LEFT JOIN pedido_items i ON i.id_pedido = d.id_pedido AND i.estado <> 'anulado' "
        "WHERE d.id_sede = %s AND d.estado IN ('despachado', 'entregado') AND d.id_domiciliario = %s "
        "AND d.metodo_pago IN ('Efectivo', 'Mixto') AND p.estado IN ('abierto', 'por_cobrar') "
        "GROUP BY d.id_pedido",
        (id_sede, id_domiciliario),
    )
    filas = cur.fetchall()
    efectivo = sum((_efectivo_de(f["metodo_pago"], Decimal(f["total"]), f["monto_efectivo"]) for f in filas), Decimal(0))
    return {"pedidos": len(filas), "efectivo": float(efectivo)}


def recaudo(id_tienda: int, id_sede: int) -> dict:
    """Panel de Caja: una tarjeta por domiciliario activo (y por cualquiera
    que todavia tenga domicilios en la calle) con sus domicilios, el efectivo
    que debe entregar y si pasa el tope. Dos consultas en total."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT tope_efectivo_domiciliario AS tope FROM tiendas WHERE id_tienda = %s", (id_tienda,))
        tope = float((cur.fetchone() or {}).get("tope") or 0)
        cur.execute(
            _DOMICILIOS + "WHERE d.id_sede = %s AND d.estado IN ('despachado', 'entregado') "
            "GROUP BY d.id_pedido ORDER BY d.despachado_en, d.id_pedido",
            (id_sede,),
        )
        en_calle = [_json_domicilio(f) for f in cur.fetchall()]
        cur.execute(
            "SELECT id_domiciliario, nombre, telefono FROM domiciliarios "
            "WHERE id_sede = %s AND estado_activo = 1 ORDER BY nombre",
            (id_sede,),
        )
        activos = cur.fetchall()
    finally:
        conn.close()

    tarjetas: OrderedDict[int, dict] = OrderedDict()
    for d in activos:
        tarjetas[d["id_domiciliario"]] = {**d, "domicilios": []}
    for dom in en_calle:
        tarjeta = tarjetas.setdefault(dom["id_domiciliario"], {
            "id_domiciliario": dom["id_domiciliario"], "nombre": dom["domiciliario"], "telefono": None, "domicilios": [],
        })
        tarjeta["domicilios"].append(dom)
    for t in tarjetas.values():
        doms = t["domicilios"]
        t["en_camino"] = sum(1 for d in doms if d["estado"] == "despachado")
        t["entregados"] = sum(1 for d in doms if d["estado"] == "entregado")
        t["efectivo"] = sum(d["efectivo"] for d in doms)
        t["otros_medios"] = sum(d["transferencia"] for d in doms)
        t["alerta"] = bool(tope) and t["efectivo"] >= tope
    lista = sorted(tarjetas.values(), key=lambda t: (-len(t["domicilios"]), t["nombre"] or ""))
    return {
        "tope": tope,
        "domiciliarios": lista,
        "efectivo_en_calle": sum(t["efectivo"] for t in lista),
        "domicilios_en_calle": len(en_calle),
    }


# --- acciones ----------------------------------------------------------------

def _domicilio_para_cambiar(cur, id_sede: int, id_pedido: int) -> tuple[dict, dict]:
    """(pedido, domicilio) bloqueados, de la sede y sin anular."""
    cur.execute(
        "SELECT id_pedido, id_tienda, id_sede, tipo, numero_llevar, cliente_nombre, cliente_telefono, id_mesero, "
        "estado, entregado_en FROM pedidos WHERE id_pedido = %s AND id_sede = %s AND tipo = 'domicilio' FOR UPDATE",
        (id_pedido, id_sede),
    )
    pedido = cur.fetchone()
    if not pedido or pedido["estado"] == "anulado":
        raise NoEncontrado("Domicilio no encontrado.")
    dom = fila_domicilio(cur, id_pedido, bloquear=True)
    if not dom:
        raise NoEncontrado("Domicilio no encontrado.")
    return pedido, dom


def despachar(id_tienda: int, id_sede: int, id_usuario: int, rol: str, id_pedido: int, data: dict) -> dict:
    """Asigna el domiciliario y lo saca a la calle. Exige todo enviado a
    cocina. Repetirlo con otro domiciliario lo reasigna: eso pasa la deuda
    de un domiciliario a otro, asi que solo lo hace Cajero o Admin y queda en
    auditoria.
    Devuelve lo que se le muestra/imprime al domiciliario: cuanto cobrar, con
    que y el vuelto que debe llevar."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        pedido, dom = _domicilio_para_cambiar(cur, id_sede, id_pedido)
        if dom["estado"] not in ("por_despachar", "despachado"):
            raise Conflicto("Este domicilio ya fue entregado.")
        domiciliario = _domiciliario_activo(cur, id_sede, data.get("id_domiciliario"))
        reasigna = dom["estado"] == "despachado" and dom["id_domiciliario"] != domiciliario["id_domiciliario"]
        if reasigna and rol not in ROLES_CAJA:
            raise ErrorServicio("Solo el cajero o el administrador cambian el domiciliario de un domicilio en camino.", 403)
        lineas = [l for l in _lineas(cur, id_pedido) if l["estado"] != "anulado"]
        if not lineas:
            raise Conflicto("El domicilio está vacío.")
        if any(l["estado"] == "pendiente" for l in lineas):
            raise Conflicto("Hay productos sin enviar a cocina. Envíalos antes de despachar.")
        total = sum((_pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"])) for l in lineas), Decimal(0))
        pagado = pedido["estado"] not in _SIN_COBRAR
        metodo = dom["metodo_pago"] if pagado or data.get("metodo") in (None, "") else _metodo(data.get("metodo"))
        monto_efectivo = _efectivo_mixto(data, total) if not pagado and metodo == "Mixto" else None
        cobrar = Decimal(0) if pagado else total
        en_billetes = _efectivo_de(metodo, cobrar, monto_efectivo)
        paga_con = None
        if en_billetes and data.get("paga_con") not in (None, "", 0, "0"):
            paga_con = _monto(data.get("paga_con"), "Con cuánto paga")
            if paga_con < en_billetes:
                raise ValueError(f"Con cuánto paga debe ser al menos ${int(en_billetes):,}.".replace(",", "."))
        ahora = ahora_local()
        cur.execute(
            "UPDATE pedido_domicilios SET estado = 'despachado', id_domiciliario = %s, metodo_pago = %s, "
            "monto_efectivo = %s, paga_con = %s, despachado_en = COALESCE(despachado_en, %s), id_usuario_despacha = %s "
            "WHERE id_pedido = %s",
            (domiciliario["id_domiciliario"], metodo, monto_efectivo, paga_con, ahora, id_usuario, id_pedido),
        )
        # Ya salio de la cocina: sus comandas se cierran (como al entregar un para llevar).
        cur.execute("UPDATE comandas SET estado = 'entregada' WHERE id_pedido = %s AND estado <> 'entregada'", (id_pedido,))
        cur.execute(
            "UPDATE pedido_items SET estado = 'entregado' WHERE id_pedido = %s AND estado IN ('enviado', 'listo')",
            (id_pedido,),
        )
        if reasigna:
            _auditoria(cur, id_tienda, id_usuario, "domicilio_reasignado",
                       f"Domicilio #{pedido['numero_llevar']}: {dom['domiciliario']} -> {domiciliario['nombre']}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "numero": pedido["numero_llevar"],
        "cliente": pedido["cliente_nombre"],
        "telefono": pedido["cliente_telefono"],
        "direccion": dom["direccion"],
        "domiciliario": domiciliario["nombre"],
        "items": [{"nombre": l["nombre"], "cantidad": float(l["cantidad"]),
                   "subtotal": float(_pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"])))} for l in lineas],
        "total": float(total),
        "pagado": pagado,
        "metodo_pago": metodo,
        "cobrar": float(cobrar),
        "efectivo": float(en_billetes),
        "transferencia": float(cobrar - en_billetes),
        "paga_con": float(paga_con) if paga_con is not None else None,
        "vuelto": float(paga_con - en_billetes) if paga_con is not None else 0.0,
    }


def marcar_entregado(id_sede: int, id_pedido: int) -> None:
    """El cliente ya lo recibio. Repetirlo no hace nada."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        pedido, dom = _domicilio_para_cambiar(cur, id_sede, id_pedido)
        if dom["estado"] == "por_despachar":
            raise Conflicto("Primero despacha el domicilio con un domiciliario.")
        if dom["estado"] == "despachado":
            ahora = ahora_local()
            cur.execute("UPDATE pedido_domicilios SET estado = 'entregado', entregado_en = %s WHERE id_pedido = %s",
                        (ahora, id_pedido))
            cur.execute("UPDATE pedidos SET entregado_en = COALESCE(entregado_en, %s) WHERE id_pedido = %s",
                        (ahora, id_pedido))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def regresar(id_tienda: int, id_sede: int, id_usuario: int, id_pedido: int, data: dict) -> None:
    """El domiciliario volvio sin entregarlo: deja de contar en su recaudo y
    queda por despachar otra vez (o para anularlo)."""
    motivo = sanitize_text(data.get("motivo"), "El motivo", max_len=200)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        pedido, dom = _domicilio_para_cambiar(cur, id_sede, id_pedido)
        if dom["estado"] not in ("despachado", "entregado"):
            raise Conflicto("Este domicilio no está en la calle.")
        cur.execute(
            "UPDATE pedido_domicilios SET estado = 'por_despachar', id_domiciliario = NULL, despachado_en = NULL, "
            "entregado_en = NULL, paga_con = NULL WHERE id_pedido = %s",
            (id_pedido,),
        )
        cur.execute("UPDATE pedidos SET entregado_en = NULL WHERE id_pedido = %s", (id_pedido,))
        _auditoria(cur, id_tienda, id_usuario, "domicilio_regresado",
                   f"Domicilio #{pedido['numero_llevar']} volvió con {dom['domiciliario']}: {motivo}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cambiar_metodo(id_tienda: int, id_sede: int, id_usuario: int, id_pedido: int, data: dict) -> dict:
    """El cliente cambia como paga (p. ej. pasa a Nequi mientras el
    domiciliario va en camino). Solo si no esta cobrado ni liquidado."""
    metodo = _metodo(data.get("metodo"))
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        pedido, dom = _domicilio_para_cambiar(cur, id_sede, id_pedido)
        if dom["estado"] == "liquidado":
            raise Conflicto("Este domicilio ya se liquidó.")
        if pedido["estado"] not in _SIN_COBRAR:
            raise Conflicto("Este domicilio ya está pagado.")
        monto_efectivo = None
        if metodo == "Mixto":
            lineas = [l for l in _lineas(cur, id_pedido) if l["estado"] != "anulado"]
            total = sum((_pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"])) for l in lineas), Decimal(0))
            monto_efectivo = _efectivo_mixto(data, total)
        if metodo != dom["metodo_pago"] or monto_efectivo != dom["monto_efectivo"]:
            cur.execute(
                "UPDATE pedido_domicilios SET metodo_pago = %s, monto_efectivo = %s, "
                "paga_con = IF(%s = 'Efectivo', paga_con, NULL) WHERE id_pedido = %s",
                (metodo, monto_efectivo, metodo, id_pedido),
            )
            detalle = f" (efectivo ${int(monto_efectivo):,})".replace(",", ".") if monto_efectivo else ""
            _auditoria(cur, id_tienda, id_usuario, "domicilio_metodo",
                       f"Domicilio #{pedido['numero_llevar']}: {dom['metodo_pago']} -> {metodo}{detalle}")
        efectivo = efectivo_por_entregar(cur, id_sede, dom["id_domiciliario"]) if dom["id_domiciliario"] else None
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"metodo_pago": metodo, "domiciliario": efectivo}


def liquidar(id_tienda: int, id_sede: int, id_usuario: int, data: dict) -> dict:
    """El domiciliario regreso y entrega la plata de los domicilios dados.

    data: id_domiciliario, pedidos (ids que el cajero tiene en pantalla) y
    efectivo (lo que la pantalla le dijo que debia recibir). Si algo cambio
    mientras tanto (otro cajero liquido, cambiaron el metodo, anularon un
    plato) no se registra nada y se avisa la cifra nueva. Todo o nada.

    Cada domicilio sin cobrar se vuelve una venta en la caja abierta, con su
    metodo; el efectivo entra al cajon (turnos_caja). Los ya pagados solo se
    marcan liquidados."""
    ids = data.get("pedidos")
    if not isinstance(ids, list) or not ids:
        raise ValueError("Elige los domicilios a recibir.")
    if len(ids) > MAX_POR_RECAUDO:
        raise ValueError(f"Máximo {MAX_POR_RECAUDO} domicilios por recaudo.")
    ids = sorted({parse_int(i, "Domicilio", min_value=1) for i in ids})
    esperado = _monto(data.get("efectivo") or 0, "El efectivo")
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        domiciliario = parse_int(data.get("id_domiciliario"), "Domiciliario", min_value=1)
        turno = caja_service.turno_abierto(cur, id_sede, bloquear=True)
        marcadores = ", ".join(["%s"] * len(ids))
        # Bloqueo en orden de id: dos cajeros recibiendo a la vez no se cruzan.
        cur.execute(
            f"SELECT id_pedido, id_tienda, id_sede, tipo, numero_llevar, id_mesero, estado FROM pedidos "
            f"WHERE id_sede = %s AND tipo = 'domicilio' AND id_pedido IN ({marcadores}) ORDER BY id_pedido FOR UPDATE",
            (id_sede, *ids),
        )
        pedidos = {p["id_pedido"]: p for p in cur.fetchall()}
        cur.execute(
            f"SELECT d.id_pedido, d.estado, d.metodo_pago, d.monto_efectivo, d.id_domiciliario, m.nombre AS domiciliario "
            f"FROM pedido_domicilios d LEFT JOIN domiciliarios m ON m.id_domiciliario = d.id_domiciliario "
            f"WHERE d.id_sede = %s AND d.id_pedido IN ({marcadores}) ORDER BY d.id_pedido FOR UPDATE",
            (id_sede, *ids),
        )
        doms = {d["id_pedido"]: d for d in cur.fetchall()}
        validos = [i for i in ids if i in pedidos and i in doms and doms[i]["estado"] in EN_CALLE
                   and doms[i]["id_domiciliario"] == domiciliario and pedidos[i]["estado"] != "anulado"]
        if len(validos) != len(ids):
            raise Conflicto("Algún domicilio ya se recibió o cambió. Actualiza la pantalla y revisa.")

        por_cobrar = []
        efectivo = Decimal(0)
        for i in ids:
            if pedidos[i]["estado"] not in _SIN_COBRAR:
                continue
            lineas = [l for l in _lineas(cur, i) if l["estado"] != "anulado"]
            if not lineas:
                raise Conflicto(f"El domicilio #{pedidos[i]['numero_llevar']} no tiene productos. Anúlalo o regrésalo.")
            total = sum((_pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"])) for l in lineas), Decimal(0))
            en_billetes = _efectivo_de(doms[i]["metodo_pago"], total, doms[i]["monto_efectivo"])
            if doms[i]["metodo_pago"] == "Mixto" and en_billetes >= total:
                # Anularon platos en el camino y el efectivo ya cubre todo.
                raise Conflicto(f"El domicilio #{pedidos[i]['numero_llevar']} bajó de valor: corrige su pago Mixto.")
            efectivo += en_billetes
            por_cobrar.append((i, lineas, total, en_billetes))
        if efectivo != esperado:
            raise Conflicto(
                f"El efectivo a recibir cambió: ahora son ${int(efectivo):,}. Revisa antes de confirmar.".replace(",", ".")
            )
        if por_cobrar and not turno:
            raise Conflicto("Abre la caja antes de recibir el recaudo.")

        ahora = ahora_local()
        nombre = doms[ids[0]]["domiciliario"]
        ventas = []
        for i, lineas, total, en_billetes in por_cobrar:
            pedido = pedidos[i]
            metodo = doms[i]["metodo_pago"]
            mixto = (en_billetes, total - en_billetes) if metodo == "Mixto" else (None, None)
            venta = _registrar_venta(
                cur, id_tienda, id_sede, id_usuario, turno, pedido,
                [(l["id_producto"], l["cantidad"], l["precio_unitario"]) for l in lineas],
                Decimal(0), metodo, *mixto, None, f"Domicilio #{pedido['numero_llevar']} · {nombre}",
            )
            _cerrar_pedido(cur, id_usuario, pedido)
            cur.execute(
                "UPDATE pedido_domicilios SET id_venta = %s, efectivo_recibido = %s WHERE id_pedido = %s",
                (venta["id_venta"], en_billetes, i),
            )
            ventas.append(venta["numero_venta"])
        cur.execute(
            f"UPDATE pedido_domicilios SET estado = 'liquidado', liquidado_en = %s, id_usuario_liquida = %s, "
            f"entregado_en = COALESCE(entregado_en, %s) WHERE id_pedido IN ({marcadores})",
            (ahora, id_usuario, ahora, *ids),
        )
        cur.execute(
            f"UPDATE pedidos SET entregado_en = COALESCE(entregado_en, %s) WHERE id_pedido IN ({marcadores})",
            (ahora, *ids),
        )
        _auditoria(cur, id_tienda, id_usuario, "recaudo_domiciliario",
                   f"{nombre}: {len(ids)} domicilio(s), efectivo ${int(efectivo):,}".replace(",", "."))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"domiciliario": nombre, "domicilios": len(ids), "efectivo": float(efectivo), "ventas": ventas}


def cambiar_tope(id_tienda: int, data: dict) -> float:
    tope = _monto(data.get("tope"), "El tope")
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tiendas SET tope_efectivo_domiciliario = %s WHERE id_tienda = %s", (tope, id_tienda))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return float(tope)
