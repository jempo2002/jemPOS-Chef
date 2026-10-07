"""Pantalla de cocina y bar: las comandas de la sede y su avance.

La pantalla consulta cada pocos segundos con `desde` (la marca que le dio la
consulta anterior) y recibe solo lo que cambio: comandas nuevas, las que
avanzaron, las que perdieron un item anulado y las entregadas (para
quitarlas). comandas.actualizada_en cambia sola en cada UPDATE (ON UPDATE
current_timestamp(3)); anular un item la toca a proposito.
"""
from __future__ import annotations

from datetime import datetime

from app.services.errores import Conflicto, NoEncontrado
from app.utils.helpers import ahora_local
from database import get_db

ESTACIONES = ("cocina", "bar")
# Solo hacia adelante.
SIGUIENTE = {"nueva": "preparando", "preparando": "lista", "lista": "entregada"}
_ESTADO_ITEMS = {"lista": "listo", "entregada": "entregado"}
_FORMATO = "%Y-%m-%d %H:%M:%S.%f"


def _desde(valor) -> datetime | None:
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor), _FORMATO)
    except ValueError as exc:
        raise ValueError("Marca de tiempo invalida.") from exc


def _llevar(fila: dict) -> str:
    texto = f"Llevar #{fila['numero_llevar']}" if fila["numero_llevar"] else "Para llevar"
    return texto + (f" · {fila['cliente_nombre']}" if fila["cliente_nombre"] else "")


def comandas(id_sede: int, estacion: str, desde=None) -> dict:
    if estacion not in ESTACIONES:
        raise ValueError("Estacion invalida.")
    desde = _desde(desde)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        if desde is None:
            cur.execute(
                "SELECT c.id_comanda, c.numero, c.estado, c.creada_en, c.actualizada_en, m.nombre AS mesa, "
                "p.numero_llevar, p.cliente_nombre "
                "FROM comandas c JOIN pedidos p ON p.id_pedido = c.id_pedido "
                "LEFT JOIN mesas m ON m.id_mesa = p.id_mesa "
                "WHERE c.id_sede = %s AND c.estacion = %s AND c.estado <> 'entregada' ORDER BY c.id_comanda",
                (id_sede, estacion),
            )
        else:
            cur.execute(
                "SELECT c.id_comanda, c.numero, c.estado, c.creada_en, c.actualizada_en, m.nombre AS mesa, "
                "p.numero_llevar, p.cliente_nombre "
                "FROM comandas c JOIN pedidos p ON p.id_pedido = c.id_pedido "
                "LEFT JOIN mesas m ON m.id_mesa = p.id_mesa "
                "WHERE c.id_sede = %s AND c.estacion = %s AND c.actualizada_en > %s ORDER BY c.id_comanda",
                (id_sede, estacion, desde),
            )
        filas = cur.fetchall()
        items: dict[int, list] = {}
        if filas:
            ids = [f["id_comanda"] for f in filas]
            marcadores = ", ".join(["%s"] * len(ids))
            cur.execute(
                "SELECT i.id_comanda, pr.nombre, i.cantidad, i.nota, i.estado = 'anulado' AS anulado "
                "FROM pedido_items i JOIN productos pr ON pr.id_producto = i.id_producto "
                f"WHERE i.id_comanda IN ({marcadores}) ORDER BY i.id_item",
                tuple(ids),
            )
            for i in cur.fetchall():
                items.setdefault(i["id_comanda"], []).append(
                    {"nombre": i["nombre"], "cantidad": float(i["cantidad"]), "nota": i["nota"], "anulado": bool(i["anulado"])}
                )
        cur.execute("SELECT NOW(3) AS ahora")
        ahora = cur.fetchone()["ahora"]
    finally:
        conn.close()
    hora = ahora_local()
    return {
        "desde": ahora.strftime(_FORMATO),
        "comandas": [
            {
                "id_comanda": f["id_comanda"],
                "numero": f["numero"],
                "estado": f["estado"],
                "mesa": f["mesa"] or _llevar(f),
                "llevar": f["mesa"] is None,
                "minutos": max(0, int((hora - f["creada_en"]).total_seconds() // 60)),
                "items": items.get(f["id_comanda"], []),
            }
            for f in filas
        ],
    }


def avanzar(id_sede: int, id_comanda: int, estado: str) -> str:
    """Pasa la comanda al estado pedido si es el siguiente. Al marcarla lista
    o entregada, sus items (no anulados) cambian igual: el plano avisa al
    mesero que hay algo para recoger."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT estado FROM comandas WHERE id_comanda = %s AND id_sede = %s FOR UPDATE",
            (id_comanda, id_sede),
        )
        fila = cur.fetchone()
        if not fila:
            raise NoEncontrado("Comanda no encontrada.")
        if SIGUIENTE.get(fila["estado"]) != estado:
            raise Conflicto("La comanda ya cambió de estado. Actualiza la pantalla.")
        cur.execute(
            "UPDATE comandas SET estado = %s" + (", lista_en = %s" if estado == "lista" else "") + " WHERE id_comanda = %s",
            (estado, ahora_local(), id_comanda) if estado == "lista" else (estado, id_comanda),
        )
        if estado in _ESTADO_ITEMS:
            cur.execute(
                "UPDATE pedido_items SET estado = %s WHERE id_comanda = %s AND estado <> 'anulado'",
                (_ESTADO_ITEMS[estado], id_comanda),
            )
        conn.commit()
        return estado
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
