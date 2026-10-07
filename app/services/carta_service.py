"""La carta: categorias y productos (platos y bebidas) del restaurante.

La carta es una sola por restaurante; el stock es por sede
(inventario_service). Soft delete: estado_activo = 0. Un producto eliminado
sigue en las ventas y pedidos viejos.
"""
from __future__ import annotations

from app.services.errores import NoEncontrado
from app.utils.validation import parse_bool, parse_float, sanitize_optional_text, sanitize_text
from database import get_db

ESTACIONES = ("cocina", "bar", "ninguna")
PRECIO_MAX = 10_000_000


def _campos(data: dict) -> dict:
    estacion = str(data.get("estacion") or "cocina")
    if estacion not in ESTACIONES:
        raise ValueError("Estacion invalida.")
    return {
        "nombre": sanitize_text(data.get("nombre"), "El nombre", max_len=150),
        "categoria": sanitize_optional_text(data.get("categoria"), "La categoria", max_len=120),
        "precio_venta": round(parse_float(data.get("precio_venta"), "El precio", min_value=0, max_value=PRECIO_MAX), 2),
        "precio_costo": round(parse_float(data.get("precio_costo") or 0, "El costo", min_value=0, max_value=PRECIO_MAX), 2),
        "estacion": estacion,
        "controla_stock": int(parse_bool(data.get("controla_stock") or False)),
    }


def _id_categoria(cur, id_tienda: int, nombre: str | None) -> int | None:
    """Categoria por nombre; la crea (o reactiva) si no existe."""
    if not nombre:
        return None
    cur.execute(
        "SELECT id_categoria, estado_activo FROM categorias WHERE id_tienda = %s AND nombre = %s "
        "ORDER BY estado_activo DESC LIMIT 1",
        (id_tienda, nombre),
    )
    fila = cur.fetchone()
    if fila:
        if not fila["estado_activo"]:
            cur.execute("UPDATE categorias SET estado_activo = 1 WHERE id_categoria = %s", (fila["id_categoria"],))
        return fila["id_categoria"]
    cur.execute("INSERT INTO categorias (id_tienda, nombre) VALUES (%s, %s)", (id_tienda, nombre))
    return cur.lastrowid


def listar(id_tienda: int, id_sede: int) -> list[dict]:
    """Productos activos con su categoria y el stock de la sede (None si no
    controlan stock). Ordenados como se muestran en la pantalla del pedido."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT p.id_producto, p.nombre, p.precio_venta, p.precio_costo, p.estacion, p.controla_stock, "
            "c.nombre AS categoria, s.stock_actual "
            "FROM productos p "
            "LEFT JOIN categorias c ON c.id_categoria = p.id_categoria "
            "LEFT JOIN stock_sedes s ON s.id_producto = p.id_producto AND s.id_sede = %s "
            "WHERE p.id_tienda = %s AND p.estado_activo = 1 "
            "ORDER BY c.nombre IS NULL, c.nombre, p.nombre",
            (id_sede, id_tienda),
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    for f in filas:
        f["precio_venta"] = float(f["precio_venta"])
        f["precio_costo"] = float(f["precio_costo"])
        f["controla_stock"] = bool(f["controla_stock"])
        f["stock_actual"] = float(f["stock_actual"] or 0) if f["controla_stock"] else None
    return filas


def crear(id_tienda: int, data: dict) -> int:
    c = _campos(data)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        id_categoria = _id_categoria(cur, id_tienda, c["categoria"])
        cur.execute(
            "INSERT INTO productos (id_tienda, id_categoria, nombre, precio_venta, precio_costo, estacion, controla_stock) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (id_tienda, id_categoria, c["nombre"], c["precio_venta"], c["precio_costo"], c["estacion"], c["controla_stock"]),
        )
        id_producto = cur.lastrowid
        conn.commit()
        return id_producto
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def actualizar(id_tienda: int, id_producto: int, data: dict) -> None:
    """El precio nuevo aplica a lo que se pida desde ahora: lo ya pedido
    guarda su precio en pedido_items."""
    c = _campos(data)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT 1 FROM productos WHERE id_producto = %s AND id_tienda = %s AND estado_activo = 1 FOR UPDATE",
            (id_producto, id_tienda),
        )
        if not cur.fetchone():
            raise NoEncontrado("Producto no encontrado.")
        id_categoria = _id_categoria(cur, id_tienda, c["categoria"])
        cur.execute(
            "UPDATE productos SET id_categoria = %s, nombre = %s, precio_venta = %s, precio_costo = %s, "
            "estacion = %s, controla_stock = %s WHERE id_producto = %s",
            (id_categoria, c["nombre"], c["precio_venta"], c["precio_costo"], c["estacion"], c["controla_stock"], id_producto),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def eliminar(id_tienda: int, id_producto: int) -> None:
    """Soft delete. Lo que ya se pidio de este producto se sigue cobrando."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE productos SET estado_activo = 0 WHERE id_producto = %s AND id_tienda = %s AND estado_activo = 1",
            (id_producto, id_tienda),
        )
        if cur.rowcount == 0:
            raise NoEncontrado("Producto no encontrado.")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
