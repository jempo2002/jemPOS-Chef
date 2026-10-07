"""Pedidos de mesa: tomar, enviar a cocina, anular, mover, precuenta y cobro.

Ciclo (plan "Mesas, comandas y cuentas"):

  1. agregar_items: abre el pedido de la mesa si no tiene y suma lineas
     `pendiente`. Solo avisa si falta stock; todavia no descuenta nada.
  2. enviar_comanda: descuenta el inventario (regla de jempo: al enviar a
     cocina, porque desde ahi se empieza a cocinar) y crea una comanda por
     estacion. Lo de estacion `ninguna` (una gaseosa) queda entregado.
  3. precuenta: total + propina sugerida; el pedido pasa a `por_cobrar`.
  4. cobrar: crea UNA venta normal (ventas + detalle_ventas) con la propina
     aparte, suma el efectivo al turno y libera la mesa. No vuelve a tocar el
     inventario.

Las rutas trabajan por mesa, no por id de pedido: el dispositivo sin
conexion puede abrir una mesa y agregarle platos sin saber que id le va a dar
la base. Cada linea y cada cobro llevan un uuid del dispositivo
(uuid_cliente); reenviarlos desde la cola sin conexion no duplica nada.
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from mysql.connector import IntegrityError

from app.services import caja_service, inventario_service
from app.services.errores import Conflicto, ErrorServicio, NoEncontrado
from app.services.mesas_service import mesa_de_sede
from app.utils.helpers import ahora_local
from app.utils.validation import parse_bool, parse_float, parse_int, sanitize_optional_text, sanitize_text
from database import get_db

MAX_ITEMS_POR_ENVIO = 100
MAX_LINEAS_POR_PEDIDO = 400
CANTIDAD_MAX = 999
PROPINA_SUGERIDA = Decimal("0.10")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ACTIVOS = ("abierto", "por_cobrar")
# Lo que manda la pantalla -> ventas.metodo_pago
METODOS = {
    "efectivo": "Efectivo",
    "nequi": "Nequi/Daviplata",
    "transferencia": "Nequi/Daviplata",
    "tarjeta": "Tarjeta",
    "mixto": "Mixto",
}


def _pesos(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _uuid(valor) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    if not _UUID.match(texto):
        raise ValueError("Identificador del dispositivo invalido.")
    return texto.lower()


def _auditoria(cur, id_tienda: int, id_usuario: int, accion: str, detalles: str) -> None:
    cur.execute(
        "INSERT INTO auditoria (id_tienda, id_usuario, accion, detalles) VALUES (%s, %s, %s, %s)",
        (id_tienda, id_usuario, accion, detalles[:2000]),
    )


def _pedido_de_mesa(cur, id_sede: int, id_mesa: int, bloquear: bool = True) -> dict | None:
    cur.execute(
        "SELECT id_pedido, id_tienda, id_sede, id_mesa, id_mesero, comensales, estado, abierto_en "
        "FROM pedidos WHERE mesa_ocupada = %s AND id_sede = %s" + (" FOR UPDATE" if bloquear else ""),
        (id_mesa, id_sede),
    )
    return cur.fetchone()


def _lineas(cur, id_pedido: int) -> list[dict]:
    cur.execute(
        "SELECT i.id_item, i.id_producto, p.nombre, p.estacion, p.controla_stock, p.precio_costo, "
        "i.cantidad, i.precio_unitario, i.nota, i.estado, i.id_comanda, c.numero AS numero_comanda "
        "FROM pedido_items i "
        "JOIN productos p ON p.id_producto = i.id_producto "
        "LEFT JOIN comandas c ON c.id_comanda = i.id_comanda "
        "WHERE i.id_pedido = %s ORDER BY i.id_item",
        (id_pedido,),
    )
    return cur.fetchall()


def _detalle(cur, mesa: dict, pedido: dict | None) -> dict:
    """Lo que pinta la pantalla del pedido."""
    if not pedido:
        return {"mesa": mesa, "pedido": None, "items": [], "total": 0, "sin_enviar": 0}
    items = []
    total = Decimal(0)
    for linea in _lineas(cur, pedido["id_pedido"]):
        subtotal = _pesos(Decimal(linea["cantidad"]) * Decimal(linea["precio_unitario"]))
        if linea["estado"] != "anulado":
            total += subtotal
        items.append({
            "id_item": linea["id_item"],
            "id_producto": linea["id_producto"],
            "nombre": linea["nombre"],
            "cantidad": float(linea["cantidad"]),
            "precio_unitario": float(linea["precio_unitario"]),
            "subtotal": float(subtotal),
            "nota": linea["nota"],
            "estado": linea["estado"],
            "numero_comanda": linea["numero_comanda"],
        })
    cur.execute("SELECT nombre_completo FROM usuarios WHERE id_usuario = %s", (pedido["id_mesero"],))
    mesero = (cur.fetchone() or {}).get("nombre_completo")
    return {
        "mesa": mesa,
        "pedido": {
            "id_pedido": pedido["id_pedido"],
            "estado": pedido["estado"],
            "comensales": pedido["comensales"],
            "mesero": mesero,
            "abierto_en": pedido["abierto_en"].strftime("%H:%M") if pedido["abierto_en"] else None,
        },
        "items": items,
        "total": float(total),
        "sin_enviar": sum(1 for i in items if i["estado"] == "pendiente"),
        "propina_sugerida": float(_pesos(total * PROPINA_SUGERIDA / 100) * 100),
    }


def ver(id_sede: int, id_mesa: int) -> dict:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        mesa = mesa_de_sede(cur, id_sede, id_mesa)
        return _detalle(cur, mesa, _pedido_de_mesa(cur, id_sede, id_mesa, bloquear=False))
    finally:
        conn.close()


def _items_validos(data: dict) -> list[dict]:
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("No hay productos para agregar.")
    if len(items) > MAX_ITEMS_POR_ENVIO:
        raise ValueError(f"Máximo {MAX_ITEMS_POR_ENVIO} productos por envío.")
    limpios = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Producto invalido.")
        cantidad = Decimal(str(parse_float(item.get("cantidad"), "La cantidad", min_value=0, max_value=CANTIDAD_MAX)))
        cantidad = cantidad.quantize(Decimal("0.001"))
        if cantidad <= 0:
            raise ValueError("La cantidad debe ser mayor a cero.")
        limpios.append({
            "id_producto": parse_int(item.get("id_producto"), "Producto", min_value=1),
            "cantidad": cantidad,
            "nota": sanitize_optional_text(item.get("nota"), "La nota", max_len=150),
            "uuid": _uuid(item.get("uuid")),
        })
    return limpios


def agregar_items(id_tienda: int, id_sede: int, id_usuario: int, id_mesa: int, data: dict) -> dict:
    """Suma lineas al pedido de la mesa (lo abre si no hay). Devuelve el
    detalle y `avisos` si algo controlado no alcanza en el inventario."""
    items = _items_validos(data)
    comensales = data.get("comensales")
    comensales = parse_int(comensales, "Comensales", min_value=1, max_value=99) if comensales not in (None, "") else None
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        # Bloquear la mesa serializa a dos meseros abriendo la misma mesa.
        mesa = mesa_de_sede(cur, id_sede, id_mesa, bloquear=True)
        pedido = _pedido_de_mesa(cur, id_sede, id_mesa)
        if not pedido:
            cur.execute(
                "INSERT INTO pedidos (id_tienda, id_sede, id_mesa, id_mesero, comensales) VALUES (%s, %s, %s, %s, %s)",
                (id_tienda, id_sede, id_mesa, id_usuario, comensales),
            )
            pedido = _pedido_de_mesa(cur, id_sede, id_mesa)
        else:
            if pedido["estado"] == "por_cobrar":
                # Pidieron algo mas despues de la precuenta.
                cur.execute("UPDATE pedidos SET estado = 'abierto' WHERE id_pedido = %s", (pedido["id_pedido"],))
            if comensales:
                cur.execute("UPDATE pedidos SET comensales = %s WHERE id_pedido = %s", (comensales, pedido["id_pedido"]))
            pedido = _pedido_de_mesa(cur, id_sede, id_mesa)

        cur.execute("SELECT COUNT(*) AS n FROM pedido_items WHERE id_pedido = %s", (pedido["id_pedido"],))
        if cur.fetchone()["n"] + len(items) > MAX_LINEAS_POR_PEDIDO:
            raise Conflicto("La cuenta tiene demasiadas líneas. Cóbrala y abre otra.")

        ids = sorted({i["id_producto"] for i in items})
        marcadores = ", ".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT id_producto, nombre, precio_venta, controla_stock FROM productos "
            f"WHERE id_tienda = %s AND estado_activo = 1 AND id_producto IN ({marcadores})",
            (id_tienda, *ids),
        )
        productos = {p["id_producto"]: p for p in cur.fetchall()}
        if len(productos) != len(ids):
            raise NoEncontrado("Producto no encontrado.")

        agregados = 0
        for item in items:
            if item["uuid"]:
                # Reenvio desde la cola sin conexion: ya estaba.
                cur.execute("SELECT 1 FROM pedido_items WHERE uuid_cliente = %s", (item["uuid"],))
                if cur.fetchone():
                    continue
            producto = productos[item["id_producto"]]
            cur.execute(
                "INSERT INTO pedido_items (id_pedido, id_producto, cantidad, precio_unitario, nota, uuid_cliente, creado_por) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (pedido["id_pedido"], producto["id_producto"], item["cantidad"], producto["precio_venta"],
                 item["nota"], item["uuid"], id_usuario),
            )
            agregados += 1

        # Aviso (no bloqueo): lo pendiente de esta mesa contra el stock de la sede.
        controlados = [pid for pid in ids if productos[pid]["controla_stock"]]
        avisos = []
        if controlados:
            stock = inventario_service.stock_de_sede(cur, id_sede, controlados)
            marcadores = ", ".join(["%s"] * len(controlados))
            cur.execute(
                f"SELECT id_producto, SUM(cantidad) AS pedido FROM pedido_items "
                f"WHERE id_pedido = %s AND estado = 'pendiente' AND id_producto IN ({marcadores}) GROUP BY id_producto",
                (pedido["id_pedido"], *controlados),
            )
            for fila in cur.fetchall():
                quedan = stock.get(fila["id_producto"], Decimal(0))
                if Decimal(fila["pedido"]) > quedan:
                    avisos.append(f"{productos[fila['id_producto']]['nombre']}: quedan {quedan:g} en inventario.")
        detalle = _detalle(cur, mesa, pedido)
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("La mesa cambió mientras guardabas. Intenta de nuevo.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    detalle["avisos"] = avisos
    detalle["agregados"] = agregados
    return detalle


def enviar_comanda(id_tienda: int, id_sede: int, id_usuario: int, id_mesa: int) -> dict:
    """Envia lo pendiente: descuenta inventario y crea una comanda por
    estacion, todo o nada. Sin pendientes no hace nada (reenviar desde la
    cola sin conexion es seguro)."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        mesa = mesa_de_sede(cur, id_sede, id_mesa)
        pedido = _pedido_de_mesa(cur, id_sede, id_mesa)
        if not pedido:
            raise Conflicto("La mesa no tiene cuenta abierta.")
        pendientes = [l for l in _lineas(cur, pedido["id_pedido"]) if l["estado"] == "pendiente"]
        if not pendientes:
            conn.rollback()
            return {"comandas": [], "msg": "No hay nada nuevo para enviar."}

        consumo: dict[int, Decimal] = defaultdict(Decimal)
        for linea in pendientes:
            if linea["controla_stock"]:
                consumo[linea["id_producto"]] += Decimal(linea["cantidad"])
        inventario_service.descontar(cur, id_tienda, id_sede, id_usuario, dict(consumo), f"Comanda mesa {mesa['nombre']}")

        comandas = []
        for estacion in ("cocina", "bar"):
            lineas = [l for l in pendientes if l["estacion"] == estacion]
            if not lineas:
                continue
            numero = caja_service.siguiente_consecutivo(cur, id_tienda, f"comanda:{id_sede}")
            cur.execute(
                "INSERT INTO comandas (id_tienda, id_sede, id_pedido, numero, estacion, id_usuario) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (id_tienda, id_sede, pedido["id_pedido"], numero, estacion, id_usuario),
            )
            id_comanda = cur.lastrowid
            ids = [l["id_item"] for l in lineas]
            marcadores = ", ".join(["%s"] * len(ids))
            cur.execute(
                f"UPDATE pedido_items SET estado = 'enviado', id_comanda = %s WHERE id_item IN ({marcadores})",
                (id_comanda, *ids),
            )
            comandas.append({
                "numero": numero,
                "estacion": estacion,
                "mesa": mesa["nombre"],
                "items": [{"nombre": l["nombre"], "cantidad": float(l["cantidad"]), "nota": l["nota"]} for l in lineas],
            })
        directos = [l["id_item"] for l in pendientes if l["estacion"] == "ninguna"]
        if directos:
            marcadores = ", ".join(["%s"] * len(directos))
            cur.execute(f"UPDATE pedido_items SET estado = 'entregado' WHERE id_item IN ({marcadores})", tuple(directos))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"comandas": comandas, "msg": "Enviado a cocina." if comandas else "Listo, no lleva preparación."}


def anular_item(id_tienda: int, id_sede: int, id_usuario: int, rol: str, id_item: int, data: dict) -> None:
    """Pendiente: se quita sin costo. Ya enviado: solo un Admin, con motivo.
    Si no se alcanzo a preparar (`devolver`), el inventario vuelve; si no,
    queda como merma (lo cocinado ya se gasto)."""
    motivo = sanitize_optional_text(data.get("motivo"), "El motivo", max_len=200)
    devolver = parse_bool(data.get("devolver") or False)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT i.id_item, i.id_producto, i.cantidad, i.estado, i.id_comanda, p.estado AS estado_pedido, "
            "pr.nombre, pr.controla_stock, pr.precio_costo "
            "FROM pedido_items i JOIN pedidos p ON p.id_pedido = i.id_pedido "
            "JOIN productos pr ON pr.id_producto = i.id_producto "
            "WHERE i.id_item = %s AND p.id_sede = %s FOR UPDATE",
            (id_item, id_sede),
        )
        item = cur.fetchone()
        if not item or item["estado"] == "anulado":
            raise NoEncontrado("Producto no encontrado en la cuenta.")
        if item["estado_pedido"] not in _ACTIVOS:
            raise Conflicto("La cuenta ya está cerrada.")
        enviado = item["estado"] != "pendiente"
        if enviado:
            if rol != "Admin":
                raise ErrorServicio("Lo que ya fue a cocina solo lo anula un administrador.", 403)
            if not motivo:
                raise ValueError("Escribe el motivo de la anulación.")
        cur.execute(
            "UPDATE pedido_items SET estado = 'anulado', anulado_por = %s, anulado_en = %s, motivo_anulacion = %s "
            "WHERE id_item = %s",
            (id_usuario, ahora_local(), motivo, id_item),
        )
        if enviado:
            # La pantalla de cocina ve el cambio en su siguiente consulta.
            cur.execute("UPDATE comandas SET actualizada_en = NOW(3) WHERE id_comanda = %s", (item["id_comanda"],))
            cantidad = Decimal(item["cantidad"])
            if devolver:
                if item["controla_stock"]:
                    inventario_service.devolver(cur, id_tienda, id_sede, id_usuario, item["id_producto"], cantidad,
                                                f"Anulado sin preparar: {motivo}")
            else:
                cur.execute(
                    "INSERT INTO mermas (id_tienda, id_sede, id_item, id_producto, cantidad, costo_unitario, motivo, id_usuario) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (id_tienda, id_sede, id_item, item["id_producto"], cantidad, item["precio_costo"], motivo, id_usuario),
                )
            _auditoria(cur, id_tienda, id_usuario, "anular_item_enviado",
                       f"{item['nombre']} x{cantidad:g} ({'devuelto' if devolver else 'merma'}): {motivo}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def anular_pedido(id_tienda: int, id_sede: int, id_usuario: int, id_mesa: int, data: dict) -> None:
    """Libera la mesa sin cobrar. Solo si nada de la cuenta fue a cocina (o
    ya se anulo): lo enviado lo anula un Admin item por item."""
    motivo = sanitize_text(data.get("motivo"), "El motivo", max_len=200)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        mesa_de_sede(cur, id_sede, id_mesa)
        pedido = _pedido_de_mesa(cur, id_sede, id_mesa)
        if not pedido:
            raise Conflicto("La mesa no tiene cuenta abierta.")
        cur.execute(
            "SELECT COUNT(*) AS n FROM pedido_items WHERE id_pedido = %s AND estado NOT IN ('pendiente', 'anulado')",
            (pedido["id_pedido"],),
        )
        if cur.fetchone()["n"]:
            raise Conflicto("La cuenta tiene productos enviados a cocina. Un administrador debe anularlos primero.")
        ahora = ahora_local()
        cur.execute(
            "UPDATE pedido_items SET estado = 'anulado', anulado_por = %s, anulado_en = %s, motivo_anulacion = %s "
            "WHERE id_pedido = %s AND estado = 'pendiente'",
            (id_usuario, ahora, motivo, pedido["id_pedido"]),
        )
        cur.execute(
            "UPDATE pedidos SET estado = 'anulado', cerrado_en = %s, id_usuario_cierre = %s, motivo_anulacion = %s "
            "WHERE id_pedido = %s",
            (ahora, id_usuario, motivo, pedido["id_pedido"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def precuenta(id_sede: int, id_mesa: int) -> dict:
    """Total y propina sugerida (10 %, voluntaria). El pedido pasa a
    `por_cobrar`; si piden algo mas vuelve a `abierto` solo."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        mesa = mesa_de_sede(cur, id_sede, id_mesa)
        pedido = _pedido_de_mesa(cur, id_sede, id_mesa)
        if not pedido:
            raise Conflicto("La mesa no tiene cuenta abierta.")
        cur.execute("UPDATE pedidos SET estado = 'por_cobrar' WHERE id_pedido = %s", (pedido["id_pedido"],))
        pedido["estado"] = "por_cobrar"
        detalle = _detalle(cur, mesa, pedido)
        conn.commit()
        return detalle
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mover(id_tienda: int, id_sede: int, id_usuario: int, id_mesa: int, data: dict) -> str:
    """Pasa la cuenta a otra mesa. Si la otra tiene cuenta, las une."""
    destino_id = parse_int(data.get("id_mesa_destino"), "Mesa destino", min_value=1)
    if destino_id == id_mesa:
        raise ValueError("Elige otra mesa.")
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        # Bloqueo en orden de id: mover A->B y B->A a la vez no se cruzan.
        mesas = {m: mesa_de_sede(cur, id_sede, m, bloquear=True) for m in sorted((id_mesa, destino_id))}
        origen = _pedido_de_mesa(cur, id_sede, id_mesa)
        if not origen:
            raise Conflicto("La mesa no tiene cuenta abierta.")
        destino = _pedido_de_mesa(cur, id_sede, destino_id)
        nombre_destino = mesas[destino_id]["nombre"]
        if not destino:
            cur.execute("UPDATE pedidos SET id_mesa = %s WHERE id_pedido = %s", (destino_id, origen["id_pedido"]))
            msg = f"Cuenta pasada a la mesa {nombre_destino}."
        else:
            cur.execute("UPDATE pedido_items SET id_pedido = %s WHERE id_pedido = %s", (destino["id_pedido"], origen["id_pedido"]))
            cur.execute("UPDATE comandas SET id_pedido = %s WHERE id_pedido = %s", (destino["id_pedido"], origen["id_pedido"]))
            cur.execute("UPDATE pedidos SET estado = 'abierto' WHERE id_pedido = %s", (destino["id_pedido"],))
            cur.execute(
                "UPDATE pedidos SET estado = 'anulado', cerrado_en = %s, id_usuario_cierre = %s, motivo_anulacion = %s "
                "WHERE id_pedido = %s",
                (ahora_local(), id_usuario, f"Unida a la mesa {nombre_destino}", origen["id_pedido"]),
            )
            msg = f"Cuentas unidas en la mesa {nombre_destino}."
        conn.commit()
        return msg
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _venta_por_uuid(cur, id_tienda: int, uuid: str) -> dict | None:
    cur.execute(
        "SELECT id_venta, numero_venta, total_final, propina, metodo_pago FROM ventas "
        "WHERE uuid_cliente = %s AND id_tienda = %s",
        (uuid, id_tienda),
    )
    venta = cur.fetchone()
    if not venta:
        return None
    return {
        "id_venta": venta["id_venta"],
        "numero_venta": venta["numero_venta"],
        "total": float(venta["total_final"]),
        "propina": float(venta["propina"]),
        "metodo_pago": venta["metodo_pago"],
        "repetido": True,
    }


def cobrar(id_tienda: int, id_sede: int, id_usuario: int, id_mesa: int, data: dict) -> dict:
    """Cobra la cuenta de la mesa en una venta. Exige caja abierta en la sede
    y nada pendiente de enviar (lo no enviado no descontó inventario)."""
    uuid = _uuid(data.get("uuid"))
    metodo = METODOS.get(str(data.get("metodo") or "").strip().lower())
    if not metodo:
        raise ValueError("Método de pago invalido.")
    propina = _pesos(parse_float(data.get("propina") or 0, "La propina", min_value=0, max_value=100_000_000))
    id_pedido_visto = data.get("id_pedido")

    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        if uuid:
            repetida = _venta_por_uuid(cur, id_tienda, uuid)
            if repetida:
                return repetida
        turno = caja_service.turno_abierto(cur, id_sede, bloquear=True)
        if not turno:
            raise Conflicto("Abre la caja antes de cobrar.")
        mesa = mesa_de_sede(cur, id_sede, id_mesa)
        pedido = _pedido_de_mesa(cur, id_sede, id_mesa)
        if not pedido:
            raise Conflicto("La mesa no tiene cuenta abierta.")
        if id_pedido_visto not in (None, "") and parse_int(id_pedido_visto, "Pedido") != pedido["id_pedido"]:
            raise Conflicto("La cuenta de esta mesa cambió. Revisa antes de cobrar.")
        lineas = [l for l in _lineas(cur, pedido["id_pedido"]) if l["estado"] != "anulado"]
        if not lineas:
            raise Conflicto("La cuenta está vacía.")
        sin_enviar = sum(1 for l in lineas if l["estado"] == "pendiente")
        if sin_enviar:
            raise Conflicto(f"Hay {sin_enviar} producto(s) sin enviar a cocina. Envíalos o quítalos antes de cobrar.")

        total = sum((_pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"])) for l in lineas), Decimal(0))
        if propina > total:
            raise ValueError("La propina no puede ser mayor que la cuenta.")
        a_pagar = total + propina
        monto_efectivo = monto_transferencia = None
        if metodo == "Mixto":
            monto_efectivo = _pesos(parse_float(data.get("monto_efectivo"), "El efectivo", min_value=0, allow_zero=False))
            monto_transferencia = _pesos(parse_float(data.get("monto_transferencia"), "La transferencia", min_value=0, allow_zero=False))
            if monto_efectivo + monto_transferencia != a_pagar:
                raise ValueError(f"El efectivo y la transferencia deben sumar exactamente ${int(a_pagar):,}.".replace(",", "."))

        numero = caja_service.siguiente_consecutivo(cur, id_tienda, "venta")
        numero_venta = f"V{id_tienda:04d}-{numero:06d}"
        cur.execute(
            "INSERT INTO ventas (id_tienda, id_sede, id_turno, id_cajero, id_pedido, id_mesero, numero_venta, "
            "subtotal, total_final, propina, metodo_pago, monto_efectivo, monto_transferencia, estado_venta, "
            "observaciones, uuid_cliente) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pagada', %s, %s)",
            (id_tienda, id_sede, turno["id_turno"], id_usuario, pedido["id_pedido"], pedido["id_mesero"], numero_venta,
             total, total, propina, metodo, monto_efectivo, monto_transferencia, f"Mesa {mesa['nombre']}", uuid),
        )
        id_venta = cur.lastrowid
        cur.executemany(
            "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, unidad_venta, precio_unitario_historico, subtotal_linea) "
            "VALUES (%s, %s, %s, 'Unidad', %s, %s)",
            [(id_venta, l["id_producto"], l["cantidad"], l["precio_unitario"],
              _pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"]))) for l in lineas],
        )
        al_cajon = a_pagar if metodo == "Efectivo" else (monto_efectivo or Decimal(0))
        if al_cajon:
            cur.execute(
                "UPDATE turnos_caja SET monto_final_esperado = COALESCE(monto_final_esperado, monto_inicial, 0) + %s "
                "WHERE id_turno = %s",
                (al_cajon, turno["id_turno"]),
            )
        cur.execute(
            "UPDATE pedidos SET estado = 'cerrado', cerrado_en = %s, id_usuario_cierre = %s WHERE id_pedido = %s",
            (ahora_local(), id_usuario, pedido["id_pedido"]),
        )
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        # El mismo cobro llego dos veces a la vez: gano el otro.
        if uuid:
            cur = conn.cursor(dictionary=True)
            repetida = _venta_por_uuid(cur, id_tienda, uuid)
            if repetida:
                return repetida
        raise Conflicto("No se pudo cobrar. Intenta de nuevo.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "id_venta": id_venta,
        "numero_venta": numero_venta,
        "total": float(total),
        "propina": float(propina),
        "a_pagar": float(a_pagar),
        "metodo_pago": metodo,
        "mesa": mesa["nombre"],
    }
