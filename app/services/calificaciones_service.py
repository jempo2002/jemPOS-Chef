"""Calificacion del servicio: el cliente califica la comida y la atencion.

Flujo:
  1. Despues de cobrar, el equipo pide el QR del pedido (enlace()). Ahi nace
     la fila de `calificaciones` con un token al azar; pedirlo otra vez
     devuelve el mismo enlace.
  2. El cliente abre /calificar/<token> sin cuenta y pone de 1 a 5 estrellas
     a la comida y a la atencion, con un comentario opcional. Una sola vez
     por pedido y hasta VIGENCIA_DIAS despues de cerrado.
  3. El Admin ve promedios, reparto de estrellas, promedios por mesero y
     comentarios en Reportes > Calificaciones. Puede ocultar una calificacion
     (borrado suave, con motivo): deja de contar pero no se borra.

El token es lo unico que autoriza a calificar: 24 bytes al azar (192 bits),
imposible de adivinar. Un pedido de otra sede o de otra tienda nunca se
alcanza desde la sesion del equipo: enlace() filtra por la sede activa.
"""
from __future__ import annotations

import re
import secrets
from datetime import timedelta

from app.services.errores import Conflicto, NoEncontrado
from app.utils.helpers import ahora_local
from app.utils.validation import parse_int, sanitize_text
from database import get_db

VIGENCIA_DIAS = 3
COMENTARIO_MAX = 500
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32}$")
ESTRELLAS = (5, 4, 3, 2, 1)


def _lugar(f: dict) -> str:
    if f.get("tipo") in ("llevar", "domicilio"):
        nombre = "Domicilio" if f["tipo"] == "domicilio" else "Para llevar"
        return nombre + (f" #{f['numero_llevar']}" if f.get("numero_llevar") else "")
    mesa = f.get("mesa") or ""
    return mesa if mesa.lower().startswith("mesa") else f"Mesa {mesa}".strip()


def _vigente(cerrado_en) -> bool:
    return cerrado_en is not None and cerrado_en >= ahora_local() - timedelta(days=VIGENCIA_DIAS)


# --- equipo: pedir el enlace ------------------------------------------------------

def pedir_al_cobrar(id_tienda: int) -> bool:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT pedir_calificacion FROM tiendas WHERE id_tienda = %s", (id_tienda,))
        fila = cur.fetchone()
        return bool(fila and fila["pedir_calificacion"])
    finally:
        conn.close()


def cambiar_pedir_al_cobrar(id_tienda: int, activo: bool) -> None:
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE tiendas SET pedir_calificacion = %s WHERE id_tienda = %s", (int(bool(activo)), id_tienda))
        conn.commit()
    finally:
        conn.close()


def enlace(id_tienda: int, id_sede: int, id_pedido) -> dict:
    """Token del pedido (lo crea la primera vez). El pedido debe estar
    cobrado, ser de la sede activa y no tener mas de VIGENCIA_DIAS."""
    id_pedido = parse_int(id_pedido, "Pedido", min_value=1)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT p.id_pedido, p.id_mesero, p.estado, p.cerrado_en, p.tipo, p.numero_llevar, p.cliente_telefono, "
            "m.nombre AS mesa FROM pedidos p LEFT JOIN mesas m ON m.id_mesa = p.id_mesa "
            "WHERE p.id_pedido = %s AND p.id_tienda = %s AND p.id_sede = %s",
            (id_pedido, id_tienda, id_sede),
        )
        pedido = cur.fetchone()
        if not pedido:
            raise NoEncontrado("Pedido no encontrado.")
        if pedido["estado"] != "cerrado":
            raise Conflicto("El pedido todavía no está cobrado.")
        if not _vigente(pedido["cerrado_en"]):
            raise Conflicto(f"Ya pasaron más de {VIGENCIA_DIAS} días desde el cobro.")
        # Dos pantallas pidiendo el QR a la vez: el UNIQUE de id_pedido deja
        # una sola fila y las dos leen el mismo token.
        cur.execute(
            "INSERT INTO calificaciones (id_tienda, id_sede, id_pedido, id_mesero, token) VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE id_calificacion = id_calificacion",
            (id_tienda, id_sede, id_pedido, pedido["id_mesero"], secrets.token_urlsafe(24)),
        )
        cur.execute("SELECT token, calificada_en FROM calificaciones WHERE id_pedido = %s", (id_pedido,))
        fila = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    return {
        "token": fila["token"],
        "calificada": fila["calificada_en"] is not None,
        "lugar": _lugar(pedido),
        "telefono": pedido["cliente_telefono"] or "",
    }


# --- cliente: calificar --------------------------------------------------------------

def por_token(token: str) -> dict | None:
    """Lo que ve el cliente: negocio, sede, lugar y si ya califico o vencio."""
    if not _TOKEN.match(token or ""):
        return None
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT c.id_calificacion, c.comida, c.atencion, c.calificada_en, c.estado_activo, "
            "p.cerrado_en, p.tipo, p.numero_llevar, m.nombre AS mesa, t.nombre_negocio, s.nombre AS sede, "
            "(SELECT COUNT(*) FROM sedes x WHERE x.id_tienda = c.id_tienda AND x.estado = 'Activa') AS num_sedes "
            "FROM calificaciones c JOIN pedidos p ON p.id_pedido = c.id_pedido "
            "LEFT JOIN mesas m ON m.id_mesa = p.id_mesa "
            "JOIN tiendas t ON t.id_tienda = c.id_tienda JOIN sedes s ON s.id_sede = c.id_sede "
            "WHERE c.token = %s",
            (token,),
        )
        f = cur.fetchone()
    finally:
        conn.close()
    if not f:
        return None
    if f["calificada_en"] is not None:
        estado = "hecha"
    elif not f["estado_activo"] or not _vigente(f["cerrado_en"]):
        estado = "vencida"
    else:
        estado = "pendiente"
    return {
        "estado": estado,
        "negocio": f["nombre_negocio"],
        "sede": f["sede"] if f["num_sedes"] > 1 else "",
        "lugar": _lugar(f),
        "comida": f["comida"],
        "atencion": f["atencion"],
    }


def _estrellas(valor, campo: str) -> int:
    try:
        n = int(str(valor or "").strip())
    except ValueError:
        n = 0
    if not 1 <= n <= 5:
        raise ValueError(f"Elige de 1 a 5 estrellas para {campo}.")
    return n


def _comentario(valor) -> str | None:
    """Texto tal cual lo escribio el cliente (sin caracteres de control): se
    guarda sin escapar porque las plantillas escapan al mostrar."""
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(valor or "")).strip()
    if len(texto) > COMENTARIO_MAX:
        raise ValueError(f"El comentario no puede superar {COMENTARIO_MAX} caracteres.")
    return texto or None


def calificar(token: str, data) -> None:
    comida = _estrellas(data.get("comida"), "la comida")
    atencion = _estrellas(data.get("atencion"), "la atención")
    comentario = _comentario(data.get("comentario"))
    if not _TOKEN.match(token or ""):
        raise NoEncontrado("Este enlace no existe.")
    ahora = ahora_local()
    conn = get_db()
    try:
        cur = conn.cursor()
        # Todo en el WHERE: dos envios a la vez (doble toque) solo guardan uno.
        cur.execute(
            "UPDATE calificaciones c JOIN pedidos p ON p.id_pedido = c.id_pedido "
            "SET c.comida = %s, c.atencion = %s, c.comentario = %s, c.calificada_en = %s "
            "WHERE c.token = %s AND c.calificada_en IS NULL AND c.estado_activo = 1 AND p.cerrado_en >= %s",
            (comida, atencion, comentario, ahora, token, ahora - timedelta(days=VIGENCIA_DIAS)),
        )
        cambio = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if not cambio:
        info = por_token(token)
        if info is None:
            raise NoEncontrado("Este enlace no existe.")
        if info["estado"] == "hecha":
            raise Conflicto("Este pedido ya fue calificado. ¡Gracias!")
        raise Conflicto("Este enlace ya venció.")


# --- reportes (Admin) ------------------------------------------------------------------

def _en_sedes(columna: str, ids: list[int]) -> tuple[str, tuple]:
    return f" AND {columna} IN ({', '.join(['%s'] * len(ids))})", tuple(ids)


def _promedio(valor) -> float | None:
    return None if valor is None else round(float(valor), 1)


def resumen(id_tienda: int, ids: list[int], per: dict) -> dict:
    """Promedios, reparto de estrellas y promedios por mesero y por sede de
    las calificaciones hechas en el periodo (sin las ocultas)."""
    filtro, params = _en_sedes("c.id_sede", ids)
    donde = ("WHERE c.id_tienda = %s AND c.estado_activo = 1 AND c.calificada_en >= %s AND c.calificada_en < %s"
             + filtro)
    base = (id_tienda, per["inicio"], per["fin"]) + params
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"SELECT COUNT(*) AS n, AVG(c.comida) AS comida, AVG(c.atencion) AS atencion, "
            f"SUM(c.comentario IS NOT NULL) AS comentarios FROM calificaciones c {donde}",
            base,
        )
        tot = cur.fetchone()
        reparto = {"comida": {e: 0 for e in ESTRELLAS}, "atencion": {e: 0 for e in ESTRELLAS}}
        for campo in reparto:
            cur.execute(f"SELECT c.{campo} AS e, COUNT(*) AS n FROM calificaciones c {donde} GROUP BY c.{campo}", base)
            for f in cur.fetchall():
                reparto[campo][int(f["e"])] = int(f["n"])
        cur.execute(
            "SELECT COALESCE(u.nombre_completo, 'Sin mesero') AS nombre, COUNT(*) AS n, "
            f"AVG(c.comida) AS comida, AVG(c.atencion) AS atencion FROM calificaciones c "
            f"LEFT JOIN usuarios u ON u.id_usuario = c.id_mesero {donde} "
            "GROUP BY c.id_mesero, u.nombre_completo ORDER BY atencion DESC, n DESC",
            base,
        )
        meseros = cur.fetchall()
        sedes = []
        if len(ids) > 1:
            cur.execute(
                "SELECT s.nombre, COUNT(*) AS n, AVG(c.comida) AS comida, AVG(c.atencion) AS atencion "
                f"FROM calificaciones c JOIN sedes s ON s.id_sede = c.id_sede {donde} "
                "GROUP BY c.id_sede, s.nombre ORDER BY s.nombre",
                base,
            )
            sedes = cur.fetchall()
        # Que tanto califican: pedidos cobrados en el periodo y cuantos de
        # esos ya tienen calificacion.
        filtro_p, params_p = _en_sedes("p.id_sede", ids)
        cur.execute(
            "SELECT COUNT(*) AS cobrados, COUNT(c.calificada_en) AS calificados FROM pedidos p "
            "LEFT JOIN calificaciones c ON c.id_pedido = p.id_pedido AND c.estado_activo = 1 "
            "WHERE p.id_tienda = %s AND p.estado = 'cerrado' AND p.cerrado_en >= %s AND p.cerrado_en < %s" + filtro_p,
            (id_tienda, per["inicio"], per["fin"]) + params_p,
        )
        tasa = cur.fetchone()
    finally:
        conn.close()
    n = int(tot["n"])

    def _barras(conteo: dict) -> list[dict]:
        return [{"estrellas": e, "n": conteo[e], "pct": round(conteo[e] / n * 100, 1) if n else 0} for e in ESTRELLAS]

    def _fila(f: dict) -> dict:
        return {"nombre": f["nombre"], "n": int(f["n"]), "comida": _promedio(f["comida"]),
                "atencion": _promedio(f["atencion"])}

    cobrados = int(tasa["cobrados"])
    return {
        "n": n,
        "comida": _promedio(tot["comida"]),
        "atencion": _promedio(tot["atencion"]),
        "comentarios": int(tot["comentarios"] or 0),
        "reparto_comida": _barras(reparto["comida"]),
        "reparto_atencion": _barras(reparto["atencion"]),
        "meseros": [_fila(f) for f in meseros],
        "sedes": [_fila(f) for f in sedes],
        "cobrados": cobrados,
        "calificados": int(tasa["calificados"]),
        "tasa": round(int(tasa["calificados"]) / cobrados * 100, 1) if cobrados else None,
    }


def lista(id_tienda: int, ids: list[int], per: dict, pagina, por_pagina: int = 30, filtro_estrellas=None) -> dict:
    """Calificaciones del periodo, la mas nueva primero, ocultas incluidas.
    filtro_estrellas 'bajas' deja solo las que tienen 1 o 2 en algo."""
    from app.services.reportes_service import _paginar

    filtro, params = _en_sedes("c.id_sede", ids)
    donde = "WHERE c.id_tienda = %s AND c.calificada_en >= %s AND c.calificada_en < %s" + filtro
    base = (id_tienda, per["inicio"], per["fin"]) + params
    if filtro_estrellas == "bajas":
        donde += " AND (c.comida <= 2 OR c.atencion <= 2)"
    elif filtro_estrellas == "comentarios":
        donde += " AND c.comentario IS NOT NULL"
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT COUNT(*) AS n FROM calificaciones c {donde}", base)
        pag = _paginar(pagina, int(cur.fetchone()["n"]), por_pagina)
        cur.execute(
            "SELECT c.id_calificacion, c.comida, c.atencion, c.comentario, c.calificada_en, c.estado_activo, "
            "c.motivo_oculta, p.tipo, p.numero_llevar, m.nombre AS mesa, s.nombre AS sede, "
            "u.nombre_completo AS mesero FROM calificaciones c JOIN pedidos p ON p.id_pedido = c.id_pedido "
            "LEFT JOIN mesas m ON m.id_mesa = p.id_mesa LEFT JOIN sedes s ON s.id_sede = c.id_sede "
            f"LEFT JOIN usuarios u ON u.id_usuario = c.id_mesero {donde} "
            "ORDER BY c.calificada_en DESC, c.id_calificacion DESC LIMIT %s OFFSET %s",
            base + (pag["por_pagina"], pag["offset"]),
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    return {
        "calificaciones": [{
            "id_calificacion": f["id_calificacion"],
            "fecha": f["calificada_en"].strftime("%Y-%m-%d %H:%M"),
            "comida": int(f["comida"]),
            "atencion": int(f["atencion"]),
            "comentario": f["comentario"] or "",
            "lugar": _lugar(f),
            "sede": f["sede"] or "",
            "mesero": f["mesero"] or "",
            "activa": bool(f["estado_activo"]),
            "motivo_oculta": f["motivo_oculta"] or "",
        } for f in filas],
        "paginacion": pag,
    }


def ocultar(id_tienda: int, id_usuario: int, id_calificacion: int, data: dict, sedes_permitidas=None) -> None:
    motivo = sanitize_text(data.get("motivo"), "El motivo", max_len=255)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id_sede, estado_activo FROM calificaciones WHERE id_calificacion = %s AND id_tienda = %s FOR UPDATE",
            (id_calificacion, id_tienda),
        )
        f = cur.fetchone()
        if not f or (sedes_permitidas is not None and f["id_sede"] not in sedes_permitidas):
            raise NoEncontrado("Calificación no encontrada.")
        if not f["estado_activo"]:
            raise Conflicto("Esta calificación ya está oculta.")
        cur.execute(
            "UPDATE calificaciones SET estado_activo = 0, oculta_por = %s, oculta_en = %s, motivo_oculta = %s "
            "WHERE id_calificacion = %s",
            (id_usuario, ahora_local(), motivo, id_calificacion),
        )
        cur.execute(
            "INSERT INTO auditoria (id_tienda, id_usuario, accion, detalles) VALUES (%s, %s, %s, %s)",
            (id_tienda, id_usuario, "ocultar_calificacion", f"Calificacion {id_calificacion}: {motivo}"[:255]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
