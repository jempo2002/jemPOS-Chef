"""Pedidos de mesa y para llevar: tomar, enviar a cocina, anular, mover,
precuenta, cobro y cuenta dividida.

Ciclo (plan "Mesas, comandas y cuentas"):

  1. agregar_items: abre el pedido de la mesa si no tiene y suma lineas
     `pendiente`. Solo avisa si falta stock; todavia no descuenta nada.
  2. enviar_comanda: descuenta el inventario (regla de jempo: al enviar a
     cocina, porque desde ahi se empieza a cocinar) y crea una comanda por
     estacion. Lo que lleva stock propio descuenta el producto; un plato con
     receta descuenta sus insumos (solo en planes con recetas). Lo de
     estacion `ninguna` (una gaseosa) queda entregado.
  3. precuenta: total + propina sugerida; el pedido pasa a `por_cobrar`.
  4. cobrar: crea UNA venta normal (ventas + detalle_ventas) con la propina
     aparte, suma el efectivo al turno y libera la mesa. No vuelve a tocar el
     inventario.

  5. cobrar_dividido (planes con "cuenta_dividida"): por items, una venta
     por persona con sus platos; en partes iguales, una sola venta con los
     platos reales y el pago de cada parte (Mixto si mezclan efectivo con
     otro metodo). Cada parte queda en pedido_cuentas.

Las rutas trabajan por lugar, no por id de pedido: una mesa (Mesa) o el uuid
que el dispositivo le pone a un pedido para llevar (Llevar). Asi el
dispositivo sin conexion puede abrir una mesa o un para llevar y agregarle
platos sin saber que id le va a dar la base. Cada linea y cada cobro llevan
un uuid del dispositivo (uuid_cliente); reenviarlos desde la cola sin
conexion no duplica nada.

Un para llevar vive igual que una mesa (pedidos.tipo = 'llevar', sin mesa).
Se puede cobrar antes de que la cocina termine; sale de la lista cuando se
marca entregado al cliente.

Un adicional (una salsa, un extra de queso: productos.es_adicional) se le
agrega a un plato al tomar el pedido. Es una linea mas (pedido_items con
id_item_padre = el plato), con su precio de adicional y su propia receta o
inventario: descuenta al enviar a cocina, sale en la comanda de su plato, se
cobra y entra en la venta como cualquier linea. Anular el plato anula sus
adicionales.

Un domicilio es un para llevar con direccion (pedidos.tipo = 'domicilio' y
su fila en pedido_domicilios): usa las mismas rutas por uuid. Despacharlo y
recibir la plata del domiciliario esta en domicilios_service. Mientras va en
camino no se le agregan platos ni se cobra aqui: se liquida en Caja.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from mysql.connector import IntegrityError

from app.services import caja_service, inventario_service, recetas_service
from app.services.errores import Conflicto, ErrorServicio, NoEncontrado
from app.services.mesas_service import mesa_de_sede
from app.utils.helpers import ahora_local
from app.utils.validation import parse_bool, parse_float, parse_int, sanitize_optional_text, sanitize_text
from database import get_db

MAX_ITEMS_POR_ENVIO = 100
MAX_ADICIONALES_POR_PLATO = 10
MAX_LINEAS_POR_PEDIDO = 400
CANTIDAD_MAX = 999
PROPINA_SUGERIDA = Decimal("0.10")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ACTIVOS = ("abierto", "por_cobrar")
MAX_PARTES = 20
# Metodos que puede usar cada parte de una cuenta dividida. En mixto la parte
# dice cuanto da en efectivo y el resto va por transferencia.
METODOS_PARTE = ("efectivo", "nequi", "transferencia", "tarjeta", "mixto")
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


@dataclass(frozen=True)
class Mesa:
    id_mesa: int


@dataclass(frozen=True)
class Llevar:
    uuid: str


def llevar(valor) -> Llevar:
    uuid = _uuid(valor)
    if not uuid:
        raise NoEncontrado("Pedido no encontrado.")
    return Llevar(uuid)


_COLUMNAS = (
    "SELECT id_pedido, id_tienda, id_sede, tipo, id_mesa, numero_llevar, cliente_nombre, cliente_telefono, "
    "uuid_cliente, id_mesero, comensales, estado, abierto_en, cerrado_en, entregado_en FROM pedidos "
)


def _pedido_de_mesa(cur, id_sede: int, id_mesa: int, bloquear: bool = True) -> dict | None:
    cur.execute(
        _COLUMNAS + "WHERE mesa_ocupada = %s AND id_sede = %s" + (" FOR UPDATE" if bloquear else ""),
        (id_mesa, id_sede),
    )
    return cur.fetchone()


def _pedido_llevar(cur, id_sede: int, uuid: str, bloquear: bool = True) -> dict | None:
    cur.execute(
        _COLUMNAS + "WHERE uuid_cliente = %s AND id_sede = %s AND tipo IN ('llevar', 'domicilio')"
        + (" FOR UPDATE" if bloquear else ""),
        (uuid, id_sede),
    )
    return cur.fetchone()


def fila_domicilio(cur, id_pedido: int, bloquear: bool = False) -> dict | None:
    cur.execute(
        "SELECT d.id_pedido, d.direccion, d.estado, d.metodo_pago, d.monto_efectivo, d.paga_con, d.id_domiciliario, "
        "m.nombre AS domiciliario, d.despachado_en, d.id_venta "
        "FROM pedido_domicilios d LEFT JOIN domiciliarios m ON m.id_domiciliario = d.id_domiciliario "
        "WHERE d.id_pedido = %s" + (" FOR UPDATE" if bloquear else ""),
        (id_pedido,),
    )
    return cur.fetchone()


def _sitio_llevar(cur, uuid: str, pedido: dict | None) -> dict:
    numero = pedido["numero_llevar"] if pedido else None
    cliente = pedido["cliente_nombre"] if pedido else None
    tipo = pedido["tipo"] if pedido else "llevar"
    nombre_tipo = "Domicilio" if tipo == "domicilio" else "Para llevar"
    titulo = nombre_tipo + (f" #{numero}" if numero else "") + (f" · {cliente}" if cliente else "")
    sitio = {
        "tipo": tipo,
        "uuid": uuid,
        "nombre": f"#{numero}" if numero else "nuevo",
        "cliente": cliente,
        "telefono": pedido["cliente_telefono"] if pedido else None,
        "titulo": titulo,
    }
    if tipo == "domicilio":
        dom = fila_domicilio(cur, pedido["id_pedido"]) or {}
        sitio["domicilio"] = {
            "direccion": dom.get("direccion"),
            "estado": dom.get("estado"),
            "metodo_pago": dom.get("metodo_pago"),
            "paga_con": float(dom["paga_con"]) if dom.get("paga_con") is not None else None,
            "domiciliario": dom.get("domiciliario"),
        }
    return sitio


def _en_camino(cur, pedido: dict) -> bool:
    """El domicilio ya salio con el domiciliario (y no se ha liquidado)."""
    if pedido["tipo"] != "domicilio":
        return False
    dom = fila_domicilio(cur, pedido["id_pedido"])
    return bool(dom) and dom["estado"] in ("despachado", "entregado")


def _ubicar(cur, id_sede: int, lugar: Mesa | Llevar, bloquear: bool = True) -> tuple[dict, dict | None]:
    """(sitio, pedido): lo que la pantalla muestra del lugar y su pedido.
    Una mesa da solo su pedido abierto; un para llevar, su pedido en
    cualquier estado (se sigue viendo despues de cobrado)."""
    if isinstance(lugar, Mesa):
        # Bloquear la mesa serializa a dos meseros abriendo la misma mesa.
        mesa = mesa_de_sede(cur, id_sede, lugar.id_mesa, bloquear=bloquear)
        nombre = mesa["nombre"]
        sitio = {"tipo": "mesa", **mesa, "titulo": nombre if nombre.lower().startswith("mesa") else f"Mesa {nombre}"}
        return sitio, _pedido_de_mesa(cur, id_sede, lugar.id_mesa, bloquear)
    pedido = _pedido_llevar(cur, id_sede, lugar.uuid, bloquear)
    return _sitio_llevar(cur, lugar.uuid, pedido), pedido


def _activo(sitio: dict, pedido: dict | None) -> dict:
    if not pedido:
        raise Conflicto("La mesa no tiene cuenta abierta." if sitio["tipo"] == "mesa" else "El pedido para llevar está vacío.")
    if pedido["estado"] not in _ACTIVOS:
        raise Conflicto("Este pedido ya está cerrado.")
    return pedido


def _lineas(cur, id_pedido: int) -> list[dict]:
    cur.execute(
        "SELECT i.id_item, i.id_producto, i.id_item_padre, p.nombre, p.estacion, p.controla_stock, p.es_preparado, "
        "p.precio_costo, i.cantidad, i.precio_unitario, i.nota, i.estado, i.id_comanda, c.numero AS numero_comanda "
        "FROM pedido_items i "
        "JOIN productos p ON p.id_producto = i.id_producto "
        "LEFT JOIN comandas c ON c.id_comanda = i.id_comanda "
        "WHERE i.id_pedido = %s ORDER BY i.id_item",
        (id_pedido,),
    )
    return cur.fetchall()


def _detalle(cur, sitio: dict, pedido: dict | None) -> dict:
    """Lo que pinta la pantalla del pedido."""
    base = {"lugar": sitio, "mesa": sitio if sitio["tipo"] == "mesa" else None}
    if not pedido:
        return {**base, "pedido": None, "items": [], "total": 0, "sin_enviar": 0}
    items = []
    total = Decimal(0)
    for linea in _lineas(cur, pedido["id_pedido"]):
        subtotal = _pesos(Decimal(linea["cantidad"]) * Decimal(linea["precio_unitario"]))
        if linea["estado"] != "anulado":
            total += subtotal
        items.append({
            "id_item": linea["id_item"],
            "id_producto": linea["id_producto"],
            "id_item_padre": linea["id_item_padre"],
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
        **base,
        "pedido": {
            "id_pedido": pedido["id_pedido"],
            "tipo": pedido["tipo"],
            "estado": pedido["estado"],
            "comensales": pedido["comensales"],
            "mesero": mesero,
            "abierto_en": pedido["abierto_en"].strftime("%H:%M") if pedido["abierto_en"] else None,
            "entregado": pedido["entregado_en"] is not None,
        },
        "items": items,
        "total": float(total),
        "sin_enviar": sum(1 for i in items if i["estado"] == "pendiente"),
        "propina_sugerida": float(_pesos(total * PROPINA_SUGERIDA / 100) * 100),
    }


def ver(id_sede: int, lugar: Mesa | Llevar) -> dict:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        return _detalle(cur, *_ubicar(cur, id_sede, lugar, bloquear=False))
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
            "adicionales": _adicionales_validos(item.get("adicionales")),
        })
    return limpios


def _adicionales_validos(adicionales) -> list[dict]:
    """[{id_producto, cantidad}]: cuantos de cada adicional lleva UN plato
    (2 platos con salsa x1 = 2 salsas)."""
    if adicionales in (None, ""):
        return []
    if not isinstance(adicionales, list):
        raise ValueError("Adicionales invalidos.")
    if len(adicionales) > MAX_ADICIONALES_POR_PLATO:
        raise ValueError(f"Máximo {MAX_ADICIONALES_POR_PLATO} adicionales por plato.")
    limpios: dict[int, int] = {}
    for ad in adicionales:
        if not isinstance(ad, dict):
            raise ValueError("Adicional invalido.")
        id_producto = parse_int(ad.get("id_producto"), "Adicional", min_value=1)
        cantidad = parse_int(ad.get("cantidad") or 1, "La cantidad del adicional", min_value=1, max_value=20)
        limpios[id_producto] = limpios.get(id_producto, 0) + cantidad
    return [{"id_producto": pid, "cantidad": cant} for pid, cant in limpios.items()]


def _telefono(valor) -> str | None:
    texto = re.sub(r"[\s-]", "", str(valor or ""))
    if not texto:
        return None
    if not re.fullmatch(r"\+?\d{7,15}", texto):
        raise ValueError("El teléfono no es válido.")
    return texto


def agregar_items(id_tienda: int, id_sede: int, id_usuario: int, lugar: Mesa | Llevar, data: dict) -> dict:
    """Suma lineas al pedido del lugar (lo abre si no hay). Devuelve el
    detalle y `avisos` si algo controlado no alcanza en el inventario. Un
    para llevar recibe aqui tambien el nombre y telefono del cliente."""
    items = _items_validos(data)
    comensales = data.get("comensales")
    comensales = parse_int(comensales, "Comensales", min_value=1, max_value=99) if comensales not in (None, "") else None
    cliente = sanitize_optional_text(data.get("cliente"), "El nombre del cliente", max_len=80)
    telefono = _telefono(data.get("telefono"))
    domicilio = isinstance(lugar, Llevar) and parse_bool(data.get("domicilio") or False)
    direccion = sanitize_optional_text(data.get("direccion"), "La dirección", max_len=160)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        sitio, pedido = _ubicar(cur, id_sede, lugar)
        if not pedido:
            if isinstance(lugar, Mesa):
                cur.execute(
                    "INSERT INTO pedidos (id_tienda, id_sede, tipo, id_mesa, id_mesero, comensales) "
                    "VALUES (%s, %s, 'mesa', %s, %s, %s)",
                    (id_tienda, id_sede, lugar.id_mesa, id_usuario, comensales),
                )
            else:
                if domicilio and not direccion:
                    raise ValueError("Escribe la dirección del domicilio.")
                tipo = "domicilio" if domicilio else "llevar"
                numero = caja_service.siguiente_consecutivo(cur, id_tienda, f"{tipo}:{id_sede}")
                cur.execute(
                    "INSERT INTO pedidos (id_tienda, id_sede, tipo, numero_llevar, cliente_nombre, cliente_telefono, "
                    "uuid_cliente, id_mesero, comensales) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (id_tienda, id_sede, tipo, numero, cliente, telefono, lugar.uuid, id_usuario, comensales),
                )
                if domicilio:
                    cur.execute(
                        "INSERT INTO pedido_domicilios (id_pedido, id_tienda, id_sede, direccion) VALUES (%s, %s, %s, %s)",
                        (cur.lastrowid, id_tienda, id_sede, direccion),
                    )
        else:
            if pedido["estado"] not in _ACTIVOS:
                raise Conflicto("Este pedido ya está cerrado. Abre uno nuevo.")
            if _en_camino(cur, pedido):
                raise Conflicto("Este domicilio ya salió. Para agregar algo, abre otro pedido.")
            if pedido["tipo"] == "domicilio" and direccion:
                cur.execute("UPDATE pedido_domicilios SET direccion = %s WHERE id_pedido = %s",
                            (direccion, pedido["id_pedido"]))
            cambios = {}
            if pedido["estado"] == "por_cobrar":
                cambios["estado"] = "abierto"  # pidieron algo mas despues de la precuenta
            if comensales:
                cambios["comensales"] = comensales
            if isinstance(lugar, Llevar):
                if cliente:
                    cambios["cliente_nombre"] = cliente
                if telefono:
                    cambios["cliente_telefono"] = telefono
            if cambios:
                cur.execute(
                    "UPDATE pedidos SET " + ", ".join(f"{c} = %s" for c in cambios) + " WHERE id_pedido = %s",
                    (*cambios.values(), pedido["id_pedido"]),
                )
        sitio, pedido = _ubicar(cur, id_sede, lugar)

        cur.execute("SELECT COUNT(*) AS n FROM pedido_items WHERE id_pedido = %s", (pedido["id_pedido"],))
        if cur.fetchone()["n"] + sum(1 + len(i["adicionales"]) for i in items) > MAX_LINEAS_POR_PEDIDO:
            raise Conflicto("La cuenta tiene demasiadas líneas. Cóbrala y abre otra.")

        ids = sorted({i["id_producto"] for i in items} | {a["id_producto"] for i in items for a in i["adicionales"]})
        marcadores = ", ".join(["%s"] * len(ids))
        cur.execute(
            f"SELECT id_producto, nombre, precio_venta, controla_stock, es_adicional FROM productos "
            f"WHERE id_tienda = %s AND estado_activo = 1 AND id_producto IN ({marcadores})",
            (id_tienda, *ids),
        )
        productos = {p["id_producto"]: p for p in cur.fetchall()}
        if len(productos) != len(ids):
            raise NoEncontrado("Producto no encontrado.")
        for item in items:
            if productos[item["id_producto"]]["es_adicional"]:
                raise ValueError(f"{productos[item['id_producto']]['nombre']} es un adicional: agrégaselo a un plato.")
            for ad in item["adicionales"]:
                if not productos[ad["id_producto"]]["es_adicional"]:
                    raise ValueError(f"{productos[ad['id_producto']]['nombre']} no es un adicional.")

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
            id_plato = cur.lastrowid
            for ad in item["adicionales"]:
                extra = productos[ad["id_producto"]]
                cur.execute(
                    "INSERT INTO pedido_items (id_pedido, id_producto, id_item_padre, cantidad, precio_unitario, creado_por) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (pedido["id_pedido"], extra["id_producto"], id_plato, item["cantidad"] * ad["cantidad"],
                     extra["precio_venta"], id_usuario),
                )
            agregados += 1

        # Aviso (no bloqueo): lo pendiente de este pedido contra el stock de la sede.
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
        detalle = _detalle(cur, sitio, pedido)
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("La cuenta cambió mientras guardabas. Intenta de nuevo.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    detalle["avisos"] = avisos
    detalle["agregados"] = agregados
    return detalle


def enviar_comanda(id_tienda: int, id_sede: int, id_usuario: int, lugar: Mesa | Llevar) -> dict:
    """Envia lo pendiente: descuenta inventario y crea una comanda por
    estacion, todo o nada. Sin pendientes no hace nada (reenviar desde la
    cola sin conexion es seguro)."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        sitio, pedido = _ubicar(cur, id_sede, lugar)
        _activo(sitio, pedido)
        todas = _lineas(cur, pedido["id_pedido"])
        pendientes = [l for l in todas if l["estado"] == "pendiente"]
        if not pendientes:
            conn.rollback()
            return {"comandas": [], "msg": "No hay nada nuevo para enviar."}
        # Un adicional va a la estacion de su plato (la salsa de la hamburguesa, a cocina).
        estacion_de = {l["id_item"]: l["estacion"] for l in todas}
        for linea in pendientes:
            if linea["id_item_padre"]:
                linea["estacion"] = estacion_de.get(linea["id_item_padre"], linea["estacion"])

        consumo: dict[int, Decimal] = defaultdict(Decimal)
        preparados: dict[int, Decimal] = defaultdict(Decimal)
        for linea in pendientes:
            if linea["controla_stock"]:
                consumo[linea["id_producto"]] += Decimal(linea["cantidad"])
            elif linea["es_preparado"]:
                preparados[linea["id_producto"]] += Decimal(linea["cantidad"])
        motivo = f"Comanda {sitio['titulo']}"
        inventario_service.descontar(cur, id_tienda, id_sede, id_usuario, dict(consumo), motivo,
                                     id_pedido=pedido["id_pedido"])
        if preparados and recetas_service.recetas_activas(cur, id_tienda):
            insumos = recetas_service.consumo_insumos(cur, dict(preparados))
            inventario_service.descontar(cur, id_tienda, id_sede, id_usuario, insumos, motivo, "insumo",
                                         pedido["id_pedido"])

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
                "mesa": sitio["nombre"],
                "lugar": sitio["titulo"],
                "items": [{"nombre": l["nombre"], "cantidad": float(l["cantidad"]), "nota": l["nota"],
                           "adicional": bool(l["id_item_padre"])} for l in lineas],
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


def _anular_linea(cur, id_tienda: int, id_sede: int, id_usuario: int, item: dict, motivo: str | None,
                  devolver: bool, recetas: bool) -> None:
    cur.execute(
        "UPDATE pedido_items SET estado = 'anulado', anulado_por = %s, anulado_en = %s, motivo_anulacion = %s "
        "WHERE id_item = %s",
        (id_usuario, ahora_local(), motivo, item["id_item"]),
    )
    if item["estado"] == "pendiente":
        return
    # La pantalla de cocina ve el cambio en su siguiente consulta.
    cur.execute("UPDATE comandas SET actualizada_en = NOW(3) WHERE id_comanda = %s", (item["id_comanda"],))
    cantidad = Decimal(item["cantidad"])
    if devolver:
        razon = f"Anulado sin preparar: {motivo}"
        if item["controla_stock"]:
            inventario_service.devolver(cur, id_tienda, id_sede, id_usuario, item["id_producto"], cantidad,
                                        razon, id_pedido=item["id_pedido"])
        elif item["es_preparado"] and recetas:
            insumos = recetas_service.consumo_insumos(cur, {item["id_producto"]: cantidad})
            for id_insumo in sorted(insumos):
                inventario_service.devolver(cur, id_tienda, id_sede, id_usuario, id_insumo, insumos[id_insumo],
                                            razon, "insumo", item["id_pedido"])
    else:
        cur.execute(
            "INSERT INTO mermas (id_tienda, id_sede, id_item, id_producto, cantidad, costo_unitario, motivo, id_usuario) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (id_tienda, id_sede, item["id_item"], item["id_producto"], cantidad, item["precio_costo"], motivo, id_usuario),
        )
    _auditoria(cur, id_tienda, id_usuario, "anular_item_enviado",
               f"{item['nombre']} x{cantidad:g} ({'devuelto' if devolver else 'merma'}): {motivo}")


def anular_item(id_tienda: int, id_sede: int, id_usuario: int, rol: str, id_item: int, data: dict) -> None:
    """Pendiente: se quita sin costo. Ya enviado: solo un Admin, con motivo.
    Si no se alcanzo a preparar (`devolver`), el inventario vuelve; si no,
    queda como merma (lo cocinado ya se gasto). Un plato con receta devuelve
    sus insumos segun la receta de hoy. Los adicionales del plato se anulan
    con el."""
    motivo = sanitize_optional_text(data.get("motivo"), "El motivo", max_len=200)
    devolver = parse_bool(data.get("devolver") or False)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        columnas = (
            "SELECT i.id_item, i.id_pedido, i.id_producto, i.cantidad, i.estado, i.id_comanda, p.estado AS estado_pedido, "
            "pr.nombre, pr.controla_stock, pr.es_preparado, pr.precio_costo "
            "FROM pedido_items i JOIN pedidos p ON p.id_pedido = i.id_pedido "
            "JOIN productos pr ON pr.id_producto = i.id_producto "
        )
        cur.execute(columnas + "WHERE i.id_item = %s AND p.id_sede = %s FOR UPDATE", (id_item, id_sede))
        item = cur.fetchone()
        if not item or item["estado"] == "anulado":
            raise NoEncontrado("Producto no encontrado en la cuenta.")
        if item["estado_pedido"] not in _ACTIVOS:
            raise Conflicto("La cuenta ya está cerrada.")
        # Quitar el plato quita tambien sus adicionales.
        cur.execute(columnas + "WHERE i.id_item_padre = %s AND i.estado <> 'anulado' ORDER BY i.id_item FOR UPDATE",
                    (id_item,))
        lineas = [item] + cur.fetchall()
        if any(l["estado"] != "pendiente" for l in lineas):
            if rol != "Admin":
                raise ErrorServicio("Lo que ya fue a cocina solo lo anula un administrador.", 403)
            if not motivo:
                raise ValueError("Escribe el motivo de la anulación.")
        recetas = recetas_service.recetas_activas(cur, id_tienda)
        for linea in lineas:
            _anular_linea(cur, id_tienda, id_sede, id_usuario, linea, motivo, devolver, recetas)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def anular_pedido(id_tienda: int, id_sede: int, id_usuario: int, lugar: Mesa | Llevar, data: dict) -> None:
    """Cierra la cuenta sin cobrar (la mesa queda libre). Solo si nada fue a
    cocina (o ya se anulo): lo enviado lo anula un Admin item por item."""
    motivo = sanitize_text(data.get("motivo"), "El motivo", max_len=200)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        pedido = _activo(*_ubicar(cur, id_sede, lugar))
        if _en_camino(cur, pedido):
            # Si no, seguiria contando en el recaudo del domiciliario.
            raise Conflicto("Este domicilio va en camino. Si volvió sin entregarse, regrésalo desde Caja y luego anúlalo.")
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


def precuenta(id_sede: int, lugar: Mesa | Llevar) -> dict:
    """Total y propina sugerida (10 %, voluntaria). El pedido pasa a
    `por_cobrar`; si piden algo mas vuelve a `abierto` solo."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        sitio, pedido = _ubicar(cur, id_sede, lugar)
        _activo(sitio, pedido)
        cur.execute("UPDATE pedidos SET estado = 'por_cobrar' WHERE id_pedido = %s", (pedido["id_pedido"],))
        pedido["estado"] = "por_cobrar"
        detalle = _detalle(cur, sitio, pedido)
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


def entregar_llevar(id_sede: int, lugar: Llevar) -> None:
    """Marca el para llevar como entregado al cliente: sale de la lista y sus
    comandas quedan entregadas (en Basico no hay pantalla de cocina que las
    cierre). Se puede repetir sin efecto."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        sitio, pedido = _ubicar(cur, id_sede, lugar)
        if not pedido or pedido["estado"] == "anulado":
            raise NoEncontrado("Pedido no encontrado.")
        if pedido["tipo"] == "domicilio":
            # Un domicilio se entrega cuando el domiciliario llega donde el cliente.
            if not _en_camino(cur, pedido):
                raise Conflicto("Primero despacha el domicilio con un domiciliario.")
            cur.execute(
                "UPDATE pedido_domicilios SET estado = 'entregado', entregado_en = %s "
                "WHERE id_pedido = %s AND estado = 'despachado'",
                (ahora_local(), pedido["id_pedido"]),
            )
        if pedido["entregado_en"] is None:
            cur.execute("UPDATE pedidos SET entregado_en = %s WHERE id_pedido = %s", (ahora_local(), pedido["id_pedido"]))
            cur.execute(
                "UPDATE comandas SET estado = 'entregada' WHERE id_pedido = %s AND estado <> 'entregada'",
                (pedido["id_pedido"],),
            )
            cur.execute(
                "UPDATE pedido_items SET estado = 'entregado' WHERE id_pedido = %s AND estado IN ('enviado', 'listo')",
                (pedido["id_pedido"],),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- cobro ---------------------------------------------------------------------

def _venta_por_uuid(cur, id_tienda: int, uuid: str) -> dict | None:
    cur.execute(
        "SELECT id_venta, id_pedido, numero_venta, total_final, propina, metodo_pago FROM ventas "
        "WHERE uuid_cliente = %s AND id_tienda = %s",
        (uuid, id_tienda),
    )
    return cur.fetchone()


def _repetida(venta: dict) -> dict:
    return {
        "id_venta": venta["id_venta"],
        "numero_venta": venta["numero_venta"],
        "total": float(venta["total_final"]),
        "propina": float(venta["propina"]),
        "metodo_pago": venta["metodo_pago"],
        "repetido": True,
    }


def _propina(valor) -> Decimal:
    return _pesos(parse_float(valor or 0, "La propina", min_value=0, max_value=100_000_000))


def _pago(data: dict, a_pagar: Decimal) -> tuple[str, Decimal | None, Decimal | None]:
    """(metodo de ventas, efectivo, transferencia). En Mixto las dos partes
    deben sumar exacto lo que se paga."""
    metodo = METODOS.get(str(data.get("metodo") or "").strip().lower())
    if not metodo:
        raise ValueError("Método de pago invalido.")
    if metodo != "Mixto":
        return metodo, None, None
    efectivo = _pesos(parse_float(data.get("monto_efectivo"), "El efectivo", min_value=0, allow_zero=False))
    transferencia = _pesos(parse_float(data.get("monto_transferencia"), "La transferencia", min_value=0, allow_zero=False))
    if efectivo + transferencia != a_pagar:
        raise ValueError(f"El efectivo y la transferencia deben sumar exactamente ${int(a_pagar):,}.".replace(",", "."))
    return metodo, efectivo, transferencia


def _preparar_cobro(cur, id_sede: int, lugar: Mesa | Llevar, id_pedido_visto) -> tuple[dict, dict, dict, list[dict]]:
    """Turno abierto (bloqueado), sitio, pedido y lineas a cobrar. Exige caja
    abierta en la sede y nada pendiente de enviar (lo no enviado no
    descontó inventario)."""
    turno = caja_service.turno_abierto(cur, id_sede, bloquear=True)
    if not turno:
        raise Conflicto("Abre la caja antes de cobrar.")
    sitio, pedido = _ubicar(cur, id_sede, lugar)
    _activo(sitio, pedido)
    if id_pedido_visto not in (None, "") and parse_int(id_pedido_visto, "Pedido") != pedido["id_pedido"]:
        raise Conflicto("La cuenta cambió. Revisa antes de cobrar.")
    lineas = [l for l in _lineas(cur, pedido["id_pedido"]) if l["estado"] != "anulado"]
    if not lineas:
        raise Conflicto("La cuenta está vacía.")
    sin_enviar = sum(1 for l in lineas if l["estado"] == "pendiente")
    if sin_enviar:
        raise Conflicto(f"Hay {sin_enviar} producto(s) sin enviar a cocina. Envíalos o quítalos antes de cobrar.")
    if _en_camino(cur, pedido):
        raise Conflicto("Este domicilio va en camino: el pago se recibe en Caja, en Domiciliarios, cuando regrese.")
    return turno, sitio, pedido, lineas


def _registrar_venta(cur, id_tienda: int, id_sede: int, id_usuario: int, turno: dict, pedido: dict,
                     lineas: list[tuple], propina: Decimal, metodo: str, efectivo, transferencia,
                     uuid: str | None, observaciones: str) -> dict:
    """Una venta normal (ventas + detalle_ventas) con la propina aparte, y el
    efectivo al turno. lineas: (id_producto, cantidad, precio_unitario).
    No toca el inventario: ya se descontó al enviar a cocina."""
    detalle = [(pid, cant, precio, _pesos(Decimal(cant) * Decimal(precio))) for pid, cant, precio in lineas]
    total = sum((d[3] for d in detalle), Decimal(0))
    a_pagar = total + propina
    numero = caja_service.siguiente_consecutivo(cur, id_tienda, "venta")
    numero_venta = f"V{id_tienda:04d}-{numero:06d}"
    cur.execute(
        "INSERT INTO ventas (id_tienda, id_sede, id_turno, id_cajero, id_pedido, id_mesero, numero_venta, "
        "subtotal, total_final, propina, metodo_pago, monto_efectivo, monto_transferencia, estado_venta, "
        "observaciones, uuid_cliente) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pagada', %s, %s)",
        (id_tienda, id_sede, turno["id_turno"], id_usuario, pedido["id_pedido"], pedido["id_mesero"], numero_venta,
         total, total, propina, metodo, efectivo, transferencia, observaciones[:255], uuid),
    )
    id_venta = cur.lastrowid
    cur.executemany(
        # El costo de hoy (receta o carta) queda fijo en la venta: la utilidad
        # de un mes cerrado no cambia si despues sube un insumo.
        "INSERT INTO detalle_ventas (id_venta, id_producto, cantidad, unidad_venta, precio_unitario_historico, "
        "costo_unitario_historico, subtotal_linea) "
        "SELECT %s, p.id_producto, %s, 'Unidad', %s, p.precio_costo, %s FROM productos p WHERE p.id_producto = %s",
        [(id_venta, cant, precio, sub, pid) for pid, cant, precio, sub in detalle],
    )
    al_cajon = a_pagar if metodo == "Efectivo" else (efectivo or Decimal(0))
    if al_cajon:
        cur.execute(
            "UPDATE turnos_caja SET monto_final_esperado = COALESCE(monto_final_esperado, monto_inicial, 0) + %s "
            "WHERE id_turno = %s",
            (al_cajon, turno["id_turno"]),
        )
    return {
        "id_venta": id_venta,
        "numero_venta": numero_venta,
        "total": float(total),
        "propina": float(propina),
        "a_pagar": float(a_pagar),
        "metodo_pago": metodo,
    }


def _cerrar_pedido(cur, id_usuario: int, pedido: dict) -> None:
    cur.execute(
        "UPDATE pedidos SET estado = 'cerrado', cerrado_en = %s, id_usuario_cierre = %s WHERE id_pedido = %s",
        (ahora_local(), id_usuario, pedido["id_pedido"]),
    )


def cobrar(id_tienda: int, id_sede: int, id_usuario: int, lugar: Mesa | Llevar, data: dict) -> dict:
    """Cobra la cuenta completa en una venta."""
    uuid = _uuid(data.get("uuid"))
    propina = _propina(data.get("propina"))
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        if uuid:
            repetida = _venta_por_uuid(cur, id_tienda, uuid)
            if repetida:
                return _repetida(repetida)
        turno, sitio, pedido, lineas = _preparar_cobro(cur, id_sede, lugar, data.get("id_pedido"))
        total = sum((_pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"])) for l in lineas), Decimal(0))
        if propina > total:
            raise ValueError("La propina no puede ser mayor que la cuenta.")
        metodo, efectivo, transferencia = _pago(data, total + propina)
        venta = _registrar_venta(
            cur, id_tienda, id_sede, id_usuario, turno, pedido,
            [(l["id_producto"], l["cantidad"], l["precio_unitario"]) for l in lineas],
            propina, metodo, efectivo, transferencia, uuid, sitio["titulo"],
        )
        _cerrar_pedido(cur, id_usuario, pedido)
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        # El mismo cobro llego dos veces a la vez: gano el otro.
        if uuid:
            repetida = _venta_por_uuid(conn.cursor(dictionary=True), id_tienda, uuid)
            if repetida:
                return _repetida(repetida)
        raise Conflicto("No se pudo cobrar. Intenta de nuevo.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    venta["mesa"] = sitio["nombre"]
    venta["lugar"] = sitio["titulo"]
    return venta


# --- cuenta dividida -------------------------------------------------------------

def _partes_validas(data: dict) -> tuple[str, list[dict]]:
    modo = str(data.get("modo") or "").strip()
    if modo not in ("items", "iguales"):
        raise ValueError("Elige cómo dividir: por productos o en partes iguales.")
    partes = data.get("partes")
    if not isinstance(partes, list) or not 2 <= len(partes) <= MAX_PARTES:
        raise ValueError(f"Divide la cuenta entre 2 y {MAX_PARTES} personas.")
    limpias = []
    for n, parte in enumerate(partes, start=1):
        if not isinstance(parte, dict):
            raise ValueError("Parte invalida.")
        metodo = str(parte.get("metodo") or "").strip().lower()
        if metodo not in METODOS_PARTE:
            raise ValueError("Método de pago invalido.")
        limpia = {
            "numero": n,
            "etiqueta": sanitize_optional_text(parte.get("etiqueta"), "El nombre de la cuenta", max_len=40) or f"Cuenta {n}",
            "metodo": METODOS[metodo],
            "propina": _propina(parte.get("propina")),
            "efectivo_mixto": (
                _pesos(parse_float(parte.get("monto_efectivo"), "El efectivo", min_value=0, allow_zero=False))
                if metodo == "mixto" else None
            ),
            "items": {},
        }
        if modo == "items":
            items = parte.get("items")
            if not isinstance(items, list) or not items:
                raise ValueError(f"{limpia['etiqueta']} no tiene productos.")
            for item in items[:MAX_LINEAS_POR_PEDIDO]:
                if not isinstance(item, dict):
                    raise ValueError("Producto invalido.")
                id_item = parse_int(item.get("id_item"), "Producto", min_value=1)
                cantidad = Decimal(str(parse_float(item.get("cantidad"), "La cantidad", min_value=0, max_value=CANTIDAD_MAX)))
                cantidad = cantidad.quantize(Decimal("0.001"))
                if cantidad <= 0:
                    raise ValueError("La cantidad debe ser mayor a cero.")
                limpia["items"][id_item] = limpia["items"].get(id_item, Decimal(0)) + cantidad
        limpias.append(limpia)
    return modo, limpias


def _pago_parte(parte: dict, a_pagar: Decimal) -> tuple[Decimal | None, Decimal | None]:
    """(efectivo, transferencia) de una parte Mixto; (None, None) si no lo es.
    El efectivo tiene que dejar algo por transferencia."""
    if parte["metodo"] != "Mixto":
        return None, None
    efectivo = parte["efectivo_mixto"]
    if efectivo >= a_pagar:
        raise ValueError(
            f"En {parte['etiqueta']} el efectivo debe ser menor que ${int(a_pagar):,}; "
            "si paga todo en efectivo elige Efectivo.".replace(",", ".")
        )
    return efectivo, a_pagar - efectivo


def _cobro_dividido_repetido(cur, id_tienda: int, uuid: str) -> dict | None:
    venta = _venta_por_uuid(cur, id_tienda, uuid)
    if not venta:
        return None
    cur.execute(
        "SELECT c.numero, c.etiqueta, c.monto, c.propina, c.metodo_pago, c.monto_efectivo, c.monto_transferencia, "
        "c.id_venta, v.numero_venta "
        "FROM pedido_cuentas c JOIN ventas v ON v.id_venta = c.id_venta WHERE c.id_pedido = %s ORDER BY c.numero",
        (venta["id_pedido"],),
    )
    partes = [_parte_json(p) for p in cur.fetchall()]
    return {"partes": partes, "ventas": sorted({p["numero_venta"] for p in partes}), "repetido": True}


def _parte_json(parte: dict) -> dict:
    return {
        "numero": parte["numero"],
        "etiqueta": parte["etiqueta"],
        "monto": float(parte["monto"]),
        "propina": float(parte["propina"]),
        "a_pagar": float(Decimal(parte["monto"]) + Decimal(parte["propina"])),
        "metodo_pago": parte["metodo_pago"],
        "monto_efectivo": None if parte.get("monto_efectivo") is None else float(parte["monto_efectivo"]),
        "monto_transferencia": None if parte.get("monto_transferencia") is None else float(parte["monto_transferencia"]),
        "id_venta": parte["id_venta"],
        "numero_venta": parte["numero_venta"],
    }


def cobrar_dividido(id_tienda: int, id_sede: int, id_usuario: int, lugar: Mesa | Llevar, data: dict) -> dict:
    """Cobra la cuenta entre varias personas, todo o nada.

    modo 'items': cada parte trae los items que paga ({id_item, cantidad};
    una linea de 2 se puede partir 1 y 1) y se cobra como una venta propia,
    con su metodo y su propina. Entre todas deben cubrir cada linea exacto.

    modo 'iguales': el total se parte en N montos (los pesos que sobran van a
    las primeras partes) y se registra UNA venta con los items reales. Si
    todas pagan igual, ese es el metodo; si mezclan (o alguna paga Mixto), la
    venta queda Mixto: efectivo lo de las partes en efectivo mas la parte en
    efectivo de las Mixto, y transferencia el resto (Nequi, Daviplata o
    tarjeta). Cada parte queda en pedido_cuentas con su metodo y, si es Mixto,
    con lo que dio en efectivo y por transferencia.
    """
    uuid = _uuid(data.get("uuid"))
    modo, partes = _partes_validas(data)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        if uuid:
            repetido = _cobro_dividido_repetido(cur, id_tienda, uuid)
            if repetido:
                return repetido
        turno, sitio, pedido, lineas = _preparar_cobro(cur, id_sede, lugar, data.get("id_pedido"))
        titulo = sitio["titulo"]
        por_item = {l["id_item"]: l for l in lineas}
        filas = []  # (parte, monto, id_venta, numero_venta)
        pagos = {}  # numero de parte -> (efectivo, transferencia) si es Mixto

        if modo == "items":
            repartido: dict[int, Decimal] = defaultdict(Decimal)
            for parte in partes:
                for id_item, cantidad in parte["items"].items():
                    if id_item not in por_item:
                        raise Conflicto("Un producto de la división ya no está en la cuenta. Revisa y vuelve a dividir.")
                    repartido[id_item] += cantidad
            for id_item, linea in por_item.items():
                diferencia = Decimal(linea["cantidad"]) - repartido[id_item]
                if diferencia > 0:
                    raise ValueError(f"Falta repartir {diferencia:g} de {linea['nombre']}.")
                if diferencia < 0:
                    raise ValueError(f"{linea['nombre']} está repartido de más.")
            for parte in partes:
                items = [(por_item[i]["id_producto"], c, por_item[i]["precio_unitario"]) for i, c in parte["items"].items()]
                monto = sum((_pesos(Decimal(c) * Decimal(p)) for _, c, p in items), Decimal(0))
                if parte["propina"] > monto:
                    raise ValueError(f"La propina de {parte['etiqueta']} no puede ser mayor que su cuenta.")
                efectivo, transferencia = pagos[parte["numero"]] = _pago_parte(parte, monto + parte["propina"])
                venta = _registrar_venta(
                    cur, id_tienda, id_sede, id_usuario, turno, pedido, items, parte["propina"], parte["metodo"],
                    efectivo, transferencia, uuid if parte["numero"] == 1 else None, f"{titulo} · {parte['etiqueta']}",
                )
                filas.append((parte, monto, venta["id_venta"], venta["numero_venta"]))
        else:
            total = sum((_pesos(Decimal(l["cantidad"]) * Decimal(l["precio_unitario"])) for l in lineas), Decimal(0))
            base, sobra = divmod(int(total), len(partes))
            montos = [Decimal(base + (1 if i < sobra else 0)) for i in range(len(partes))]
            for parte, monto in zip(partes, montos):
                if parte["propina"] > monto:
                    raise ValueError(f"La propina de {parte['etiqueta']} no puede ser mayor que su parte.")
                pagos[parte["numero"]] = _pago_parte(parte, monto + parte["propina"])
            propina = sum((p["propina"] for p in partes), Decimal(0))
            metodos = {p["metodo"] for p in partes}
            if len(metodos) == 1 and "Mixto" not in metodos:
                metodo, efectivo, transferencia = metodos.pop(), None, None
            else:
                metodo = "Mixto"
                efectivo = Decimal(0)
                for p, m in zip(partes, montos):
                    if p["metodo"] == "Efectivo":
                        efectivo += m + p["propina"]
                    elif p["metodo"] == "Mixto":
                        efectivo += pagos[p["numero"]][0]
                transferencia = total + propina - efectivo
            venta = _registrar_venta(
                cur, id_tienda, id_sede, id_usuario, turno, pedido,
                [(l["id_producto"], l["cantidad"], l["precio_unitario"]) for l in lineas],
                propina, metodo, efectivo, transferencia, uuid, f"{titulo} · dividida en {len(partes)}",
            )
            filas = [(p, m, venta["id_venta"], venta["numero_venta"]) for p, m in zip(partes, montos)]

        resultado = []
        for parte, monto, id_venta, numero_venta in filas:
            efectivo, transferencia = pagos.get(parte["numero"], (None, None))
            cur.execute(
                "INSERT INTO pedido_cuentas (id_tienda, id_sede, id_pedido, numero, etiqueta, modo, monto, propina, "
                "metodo_pago, monto_efectivo, monto_transferencia, id_venta, id_usuario) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (id_tienda, id_sede, pedido["id_pedido"], parte["numero"], parte["etiqueta"], modo, monto,
                 parte["propina"], parte["metodo"], efectivo, transferencia, id_venta, id_usuario),
            )
            id_cuenta = cur.lastrowid
            if parte["items"]:
                cur.executemany(
                    "INSERT INTO pedido_cuenta_items (id_cuenta, id_item, cantidad) VALUES (%s, %s, %s)",
                    [(id_cuenta, i, c) for i, c in parte["items"].items()],
                )
            resultado.append(_parte_json({
                "numero": parte["numero"], "etiqueta": parte["etiqueta"], "monto": monto, "propina": parte["propina"],
                "metodo_pago": parte["metodo"], "monto_efectivo": efectivo, "monto_transferencia": transferencia,
                "id_venta": id_venta, "numero_venta": numero_venta,
            }))
        _cerrar_pedido(cur, id_usuario, pedido)
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        if uuid:
            repetido = _cobro_dividido_repetido(conn.cursor(dictionary=True), id_tienda, uuid)
            if repetido:
                return repetido
        raise Conflicto("No se pudo cobrar. Intenta de nuevo.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {
        "partes": resultado,
        "ventas": sorted({p["numero_venta"] for p in resultado}),
        "lugar": sitio["titulo"],
        "total": sum(p["monto"] for p in resultado),
        "propina": sum(p["propina"] for p in resultado),
    }
