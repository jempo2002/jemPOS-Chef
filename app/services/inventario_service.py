"""Stock por sede y su kardex (movimientos_inventario).

Dos clases de articulo llevan stock por sede, con las mismas reglas:
  producto  lo que se vende hecho (gaseosas, cervezas): `controla_stock = 1`,
            tabla stock_sedes.
  insumo    ingredientes de las recetas (recetas_service), tabla
            stock_insumos_sedes. Un plato con receta no tiene stock propio.

El kardex nunca se borra ni se edita (la base lo impide con triggers): toda
correccion es un movimiento nuevo (Entrada, Salida o Ajuste) con el stock de
antes y de despues.

Las funciones con `cur` trabajan dentro de la transaccion del llamador y no
hacen commit: enviar una comanda descuenta el stock y crea la comanda juntos,
o no hace ninguna de las dos cosas.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.errores import Conflicto, NoEncontrado
from app.utils.validation import parse_float, sanitize_optional_text
from database import get_db

TIPOS_MOVIMIENTO = ("Entrada", "Salida", "Ajuste")
CANTIDAD_MAX = 100000
# clase -> (tabla de stock, columna del id, tabla del articulo)
_CLASES = {
    "producto": ("stock_sedes", "id_producto", "productos"),
    "insumo": ("stock_insumos_sedes", "id_insumo", "insumos"),
}


def _d(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.001"))


def _bloquear_stock(cur, id_sede: int, id_articulo: int, clase: str = "producto") -> Decimal:
    """Stock actual de la sede con la fila bloqueada hasta el commit. Si el
    articulo nunca tuvo stock en la sede, crea la fila en 0."""
    tabla, columna, _ = _CLASES[clase]
    cur.execute(
        f"INSERT IGNORE INTO {tabla} (id_sede, {columna}, stock_actual) VALUES (%s, %s, 0)",
        (id_sede, id_articulo),
    )
    cur.execute(
        f"SELECT stock_actual FROM {tabla} WHERE id_sede = %s AND {columna} = %s FOR UPDATE",
        (id_sede, id_articulo),
    )
    return _d(cur.fetchone()["stock_actual"])


def _movimiento(cur, id_tienda, id_sede, id_articulo, id_usuario, tipo, cantidad, antes, despues, motivo,
                clase: str = "producto", id_pedido: int | None = None):
    tabla, columna, _ = _CLASES[clase]
    cur.execute(
        f"UPDATE {tabla} SET stock_actual = %s WHERE id_sede = %s AND {columna} = %s",
        (despues, id_sede, id_articulo),
    )
    cur.execute(
        f"INSERT INTO movimientos_inventario "
        f"(id_tienda, id_sede, {columna}, id_pedido, id_usuario, tipo_movimiento, motivo, cantidad, stock_anterior, stock_posterior) "
        f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (id_tienda, id_sede, id_articulo, id_pedido, id_usuario, tipo, (motivo or "")[:255] or None,
         cantidad, antes, despues),
    )


def descontar(cur, id_tienda: int, id_sede: int, id_usuario: int, consumo: dict[int, Decimal], motivo: str,
              clase: str = "producto", id_pedido: int | None = None) -> None:
    """Saca del stock de la sede {id_articulo: cantidad}. `consumo` ya viene
    sumado por articulo (dos platos con el mismo insumo se validan juntos).
    Revisa todo antes de tocar nada: si falta uno, lanza Conflicto y la
    transaccion del llamador se deshace entera.

    Bloquea en orden de id: dos comandas simultaneas con los mismos
    articulos no se cruzan en un deadlock."""
    if not consumo:
        return
    _, columna, tabla_articulo = _CLASES[clase]
    stock = {aid: _bloquear_stock(cur, id_sede, aid, clase) for aid in sorted(consumo)}
    faltan = [aid for aid in sorted(consumo) if stock[aid] < consumo[aid]]
    if faltan:
        marcadores = ", ".join(["%s"] * len(faltan))
        cur.execute(f"SELECT {columna} AS id, nombre FROM {tabla_articulo} WHERE {columna} IN ({marcadores})", tuple(faltan))
        nombres = {f["id"]: f["nombre"] for f in cur.fetchall()}
        detalle = "; ".join(
            f"{nombres.get(aid, 'Producto')} (quedan {stock[aid]:g}, se necesitan {consumo[aid]:g})" for aid in faltan
        )
        raise Conflicto(f"No hay suficiente inventario: {detalle}.")
    for aid in sorted(consumo):
        _movimiento(cur, id_tienda, id_sede, aid, id_usuario, "Salida", consumo[aid], stock[aid],
                    stock[aid] - consumo[aid], motivo, clase, id_pedido)


def devolver(cur, id_tienda: int, id_sede: int, id_usuario: int, id_articulo: int, cantidad: Decimal, motivo: str,
             clase: str = "producto", id_pedido: int | None = None) -> None:
    antes = _bloquear_stock(cur, id_sede, id_articulo, clase)
    _movimiento(cur, id_tienda, id_sede, id_articulo, id_usuario, "Entrada", cantidad, antes, antes + cantidad, motivo,
                clase, id_pedido)


def stock_de_sede(cur, id_sede: int, ids: list[int]) -> dict[int, Decimal]:
    """Stock sin bloquear, para avisar al pedir (el descuento real es al enviar)."""
    if not ids:
        return {}
    marcadores = ", ".join(["%s"] * len(ids))
    cur.execute(
        f"SELECT id_producto, stock_actual FROM stock_sedes WHERE id_sede = %s AND id_producto IN ({marcadores})",
        (id_sede, *ids),
    )
    return {f["id_producto"]: _d(f["stock_actual"]) for f in cur.fetchall()}


def registrar_movimiento(id_tienda: int, id_sede: int, id_usuario: int, id_producto: int, data: dict) -> Decimal:
    """Entrada (compra), Salida (consumo interno) o Ajuste (conteo fisico:
    `cantidad` es el stock contado). Devuelve el stock nuevo."""
    tipo = str(data.get("tipo") or "")
    if tipo not in TIPOS_MOVIMIENTO:
        raise ValueError("Tipo de movimiento invalido.")
    cantidad = _d(parse_float(data.get("cantidad"), "La cantidad", min_value=0, max_value=CANTIDAD_MAX))
    if tipo != "Ajuste" and cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor a cero.")
    motivo = sanitize_optional_text(data.get("motivo"), "El motivo", max_len=200)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT controla_stock FROM productos WHERE id_producto = %s AND id_tienda = %s AND estado_activo = 1",
            (id_producto, id_tienda),
        )
        producto = cur.fetchone()
        if not producto:
            raise NoEncontrado("Producto no encontrado.")
        if not producto["controla_stock"]:
            raise Conflicto("Este producto no lleva inventario. Actívalo en la carta primero.")
        antes = _bloquear_stock(cur, id_sede, id_producto)
        if tipo == "Entrada":
            despues = antes + cantidad
        elif tipo == "Salida":
            if cantidad > antes:
                raise Conflicto(f"Solo hay {antes:g} en esta sede.")
            despues = antes - cantidad
        else:
            despues = cantidad
            cantidad = abs(despues - antes)
        _movimiento(cur, id_tienda, id_sede, id_producto, id_usuario, tipo, cantidad, antes, despues, motivo or tipo)
        conn.commit()
        return despues
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
