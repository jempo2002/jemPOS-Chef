"""Zonas y mesas de una sede, y el plano con el estado de cada mesa.

El estado de la mesa (libre, ocupada, por cobrar) no se guarda: sale del
pedido abierto que tenga. Asi nunca queda "ocupada" por error despues de
cobrar. Soft delete en zonas y mesas (estado_activo); el nombre de una
eliminada se puede volver a usar (nombre_vivo).
"""
from __future__ import annotations

from mysql.connector import IntegrityError

from app.services.errores import Conflicto, NoEncontrado
from app.utils.validation import parse_int, sanitize_text
from database import get_db

MAX_MESAS_POR_SEDE = 150


def _zona_de_sede(cur, id_sede: int, id_zona) -> int | None:
    if id_zona in (None, "", 0, "0"):
        return None
    id_zona = parse_int(id_zona, "Zona", min_value=1)
    cur.execute("SELECT 1 FROM zonas WHERE id_zona = %s AND id_sede = %s AND estado_activo = 1", (id_zona, id_sede))
    if not cur.fetchone():
        raise NoEncontrado("Zona no encontrada.")
    return id_zona


def _ejecutar(sql_y_params: list[tuple], duplicado: str) -> int | None:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        ultimo = None
        for sql, params in sql_y_params:
            cur.execute(sql, params)
            ultimo = cur.lastrowid
        conn.commit()
        return ultimo
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto(duplicado) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- zonas -----------------------------------------------------------------

def crear_zona(id_tienda: int, id_sede: int, data: dict) -> int:
    nombre = sanitize_text(data.get("nombre"), "El nombre de la zona", max_len=60)
    return _ejecutar(
        [("INSERT INTO zonas (id_tienda, id_sede, nombre, orden) "
          "SELECT %s, %s, %s, COALESCE(MAX(orden), 0) + 1 FROM zonas WHERE id_sede = %s",
          (id_tienda, id_sede, nombre, id_sede))],
        "Ya existe una zona con ese nombre.",
    )


def actualizar_zona(id_sede: int, id_zona: int, data: dict) -> None:
    nombre = sanitize_text(data.get("nombre"), "El nombre de la zona", max_len=60)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM zonas WHERE id_zona = %s AND id_sede = %s AND estado_activo = 1", (id_zona, id_sede))
        if not cur.fetchone():
            raise NoEncontrado("Zona no encontrada.")
        cur.execute("UPDATE zonas SET nombre = %s WHERE id_zona = %s", (nombre, id_zona))
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("Ya existe una zona con ese nombre.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def eliminar_zona(id_sede: int, id_zona: int) -> None:
    """Soft delete. Sus mesas quedan sin zona, no se eliminan."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE zonas SET estado_activo = 0 WHERE id_zona = %s AND id_sede = %s AND estado_activo = 1",
            (id_zona, id_sede),
        )
        if cur.rowcount == 0:
            raise NoEncontrado("Zona no encontrada.")
        cur.execute("UPDATE mesas SET id_zona = NULL WHERE id_zona = %s", (id_zona,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- mesas -----------------------------------------------------------------

def _campos_mesa(cur, id_sede: int, data: dict) -> tuple[str, int, int | None]:
    nombre = sanitize_text(data.get("nombre"), "El nombre de la mesa", max_len=30)
    capacidad = parse_int(data.get("capacidad") or 4, "La capacidad", min_value=1, max_value=99)
    return nombre, capacidad, _zona_de_sede(cur, id_sede, data.get("id_zona"))


def crear_mesa(id_tienda: int, id_sede: int, data: dict) -> int:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        nombre, capacidad, id_zona = _campos_mesa(cur, id_sede, data)
        cur.execute("SELECT COUNT(*) AS n FROM mesas WHERE id_sede = %s AND estado_activo = 1", (id_sede,))
        if cur.fetchone()["n"] >= MAX_MESAS_POR_SEDE:
            raise Conflicto(f"Una sede puede tener hasta {MAX_MESAS_POR_SEDE} mesas.")
        cur.execute(
            "INSERT INTO mesas (id_tienda, id_sede, id_zona, nombre, capacidad, orden) "
            "SELECT %s, %s, %s, %s, %s, COALESCE(MAX(orden), 0) + 1 FROM mesas WHERE id_sede = %s",
            (id_tienda, id_sede, id_zona, nombre, capacidad, id_sede),
        )
        id_mesa = cur.lastrowid
        conn.commit()
        return id_mesa
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("Ya existe una mesa con ese nombre.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def actualizar_mesa(id_sede: int, id_mesa: int, data: dict) -> None:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT 1 FROM mesas WHERE id_mesa = %s AND id_sede = %s AND estado_activo = 1", (id_mesa, id_sede))
        if not cur.fetchone():
            raise NoEncontrado("Mesa no encontrada.")
        nombre, capacidad, id_zona = _campos_mesa(cur, id_sede, data)
        cur.execute(
            "UPDATE mesas SET nombre = %s, capacidad = %s, id_zona = %s WHERE id_mesa = %s",
            (nombre, capacidad, id_zona, id_mesa),
        )
        conn.commit()
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("Ya existe una mesa con ese nombre.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def eliminar_mesa(id_sede: int, id_mesa: int) -> None:
    """Soft delete. Una mesa con cuenta abierta no se elimina."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM mesas WHERE id_mesa = %s AND id_sede = %s AND estado_activo = 1 FOR UPDATE",
            (id_mesa, id_sede),
        )
        if not cur.fetchone():
            raise NoEncontrado("Mesa no encontrada.")
        cur.execute("SELECT 1 FROM pedidos WHERE mesa_ocupada = %s", (id_mesa,))
        if cur.fetchone():
            raise Conflicto("La mesa tiene una cuenta abierta. Cóbrala o anúlala primero.")
        cur.execute("UPDATE mesas SET estado_activo = 0 WHERE id_mesa = %s", (id_mesa,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mesa_de_sede(cur, id_sede: int, id_mesa: int, bloquear: bool = False) -> dict:
    cur.execute(
        "SELECT id_mesa, nombre, capacidad, id_zona FROM mesas "
        "WHERE id_mesa = %s AND id_sede = %s AND estado_activo = 1" + (" FOR UPDATE" if bloquear else ""),
        (id_mesa, id_sede),
    )
    mesa = cur.fetchone()
    if not mesa:
        raise NoEncontrado("Mesa no encontrada.")
    return mesa


# --- plano -----------------------------------------------------------------

def plano(id_sede: int) -> dict:
    """Zonas y mesas de la sede con el pedido abierto de cada una: total,
    mesero, minutos abierta, items sin enviar y comandas listas para
    recoger. Tres consultas, sin una por mesa."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id_zona, nombre FROM zonas WHERE id_sede = %s AND estado_activo = 1 ORDER BY orden, nombre",
            (id_sede,),
        )
        zonas = cur.fetchall()
        cur.execute(
            "SELECT m.id_mesa, m.nombre, m.capacidad, m.id_zona, "
            "p.id_pedido, p.estado, p.comensales, u.nombre_completo AS mesero, "
            "TIMESTAMPDIFF(MINUTE, p.abierto_en, NOW()) AS minutos "
            "FROM mesas m "
            "LEFT JOIN pedidos p ON p.mesa_ocupada = m.id_mesa "
            "LEFT JOIN usuarios u ON u.id_usuario = p.id_mesero "
            "WHERE m.id_sede = %s AND m.estado_activo = 1 ORDER BY m.orden, m.nombre",
            (id_sede,),
        )
        mesas = cur.fetchall()
        pedidos = [m["id_pedido"] for m in mesas if m["id_pedido"]]
        totales: dict[int, dict] = {}
        if pedidos:
            marcadores = ", ".join(["%s"] * len(pedidos))
            cur.execute(
                "SELECT id_pedido, SUM(cantidad * precio_unitario) AS total, "
                "SUM(estado = 'pendiente') AS sin_enviar, SUM(estado = 'listo') AS listos "
                f"FROM pedido_items WHERE estado <> 'anulado' AND id_pedido IN ({marcadores}) GROUP BY id_pedido",
                tuple(pedidos),
            )
            totales = {f["id_pedido"]: f for f in cur.fetchall()}
    finally:
        conn.close()
    for m in mesas:
        t = totales.get(m["id_pedido"]) or {}
        m["total"] = float(t.get("total") or 0)
        m["sin_enviar"] = int(t.get("sin_enviar") or 0)
        m["listos"] = int(t.get("listos") or 0)
        m["estado"] = m["estado"] or "libre"
    return {"zonas": zonas, "mesas": mesas}
