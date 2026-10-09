"""Sedes de un restaurante: alta con tope del plan, edicion y soft delete."""
from __future__ import annotations

from mysql.connector import Error as MySQLError, IntegrityError

from app.services import plan_service
from app.utils.helpers import ahora_local, normalize_phone
from app.utils.validation import sanitize_optional_text, sanitize_text
from database import get_db

_NOMBRE_DUPLICADO = "Ya existe una sede activa con ese nombre."
# SIGNAL de los triggers de migrations/2026-10-07_sedes_planes_roles.sql.
ERRNO_TRIGGER = 1644
_LIMITE_BASE = "El plan no admite más sedes."


class SedeError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _campos(data: dict) -> tuple[str, str | None, str | None]:
    nombre = sanitize_text(data.get("nombre"), "El nombre de la sede", max_len=120)
    direccion = sanitize_optional_text(data.get("direccion"), "La direccion", max_len=200)
    telefono = normalize_phone(data.get("telefono"), max_len=20)
    return nombre, direccion, telefono


def resumen_sedes(id_tienda: int) -> dict:
    """Sedes activas con su numero de usuarios, y lo que cuestan al mes."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT plan_id FROM tiendas WHERE id_tienda = %s", (id_tienda,))
        plan_id = plan_service.normalizar_plan((cur.fetchone() or {}).get("plan_id"))
        cur.execute(
            "SELECT s.id_sede, s.nombre, s.direccion, s.telefono, s.es_principal, "
            "s.costo_montaje, s.montaje_pagado, "
            "(SELECT COUNT(*) FROM usuarios u WHERE u.id_sede = s.id_sede AND u.estado_activo = 1) AS usuarios "
            "FROM sedes s WHERE s.id_tienda = %s AND s.estado = 'Activa' "
            "ORDER BY s.es_principal DESC, s.nombre",
            (id_tienda,),
        )
        sedes = cur.fetchall()
    finally:
        conn.close()
    plan = plan_service.plan_de(plan_id)
    n = len(sedes)
    tope = plan_service.tope_sedes(plan_id)
    return {
        "sedes": sedes,
        "plan_id": plan_id,
        "plan_nombre": plan["nombre"],
        # Mensualidad que sumaria la proxima sede (0 si va incluida).
        "sede_extra": plan_service.precio_sede(n + 1),
        "max_sedes": tope,
        "puede_crear": tope is None or n < tope,
        "sedes_incluidas": plan_service.SEDES_INCLUIDAS,
        "nueva_adicional": plan_service.sede_nueva_es_adicional(plan_id, n),
        "multisede": plan_service.tiene_funcion(plan_id, "multisede"),
        "costo_montaje": plan_service.COSTO_MONTAJE_SEDE,
        "sedes_extra": plan_service.sedes_extra(plan_id, n),
        "mensualidad": plan_service.mensualidad(plan_id, n),
    }


def crear_sede(id_tienda: int, data: dict) -> int:
    """Lanza ValueError (datos), LimitePlanError (tope) o SedeError.

    Una sede adicional (por encima de las incluidas) nace con su montaje
    pendiente (COSTO_MONTAJE_SEDE): el Master lo marca pagado en su panel
    cuando lo recibe. Las incluidas no pagan montaje."""
    nombre, direccion, telefono = _campos(data)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        plan_service.verificar_limite(cur, id_tienda, "sedes")
        cur.execute(
            "SELECT COUNT(*) AS n FROM sedes WHERE id_tienda = %s AND estado = 'Activa'", (id_tienda,)
        )
        adicional = plan_service.sede_nueva_es_adicional(None, cur.fetchone()["n"])
        cur.execute(
            "INSERT INTO sedes (id_tienda, nombre, direccion, telefono, costo_montaje) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id_tienda, nombre, direccion, telefono, plan_service.COSTO_MONTAJE_SEDE if adicional else 0),
        )
        id_sede = cur.lastrowid
        conn.commit()
        return id_sede
    except IntegrityError as exc:
        conn.rollback()
        raise SedeError(_NOMBRE_DUPLICADO, 409) from exc
    except MySQLError as exc:
        conn.rollback()
        if exc.errno == ERRNO_TRIGGER:
            raise SedeError(_LIMITE_BASE, 409) from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def actualizar_sede(id_tienda: int, id_sede: int, data: dict) -> str:
    """Devuelve el nombre ya limpio (la sesion lo muestra si es la sede activa)."""
    nombre, direccion, telefono = _campos(data)
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE sedes SET nombre = %s, direccion = %s, telefono = %s "
            "WHERE id_sede = %s AND id_tienda = %s AND estado = 'Activa'",
            (nombre, direccion, telefono, id_sede, id_tienda),
        )
        # rowcount 0 tambien cuando no cambio nada: confirmar que existe.
        if cur.rowcount == 0:
            cur.execute(
                "SELECT 1 FROM sedes WHERE id_sede = %s AND id_tienda = %s AND estado = 'Activa'",
                (id_sede, id_tienda),
            )
            if not cur.fetchone():
                raise SedeError("Sede no encontrada.", 404)
        conn.commit()
        return nombre
    except IntegrityError as exc:
        conn.rollback()
        raise SedeError(_NOMBRE_DUPLICADO, 409) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def eliminar_sede(id_tienda: int, id_sede: int) -> None:
    """Soft delete. La principal no se elimina, y una sede con usuarios
    activos tampoco: primero se mueven o se desactivan (si no, quedarian
    atados a una sede muerta y sin poder entrar)."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT es_principal FROM sedes WHERE id_sede = %s AND id_tienda = %s AND estado = 'Activa' FOR UPDATE",
            (id_sede, id_tienda),
        )
        sede = cur.fetchone()
        if not sede:
            raise SedeError("Sede no encontrada.", 404)
        if sede["es_principal"]:
            raise SedeError("La sede principal no se puede eliminar.")
        cur.execute(
            "SELECT COUNT(*) AS n FROM usuarios WHERE id_sede = %s AND estado_activo = 1",
            (id_sede,),
        )
        if cur.fetchone()["n"]:
            raise SedeError("La sede tiene usuarios activos. Muévelos a otra sede o desactívalos primero.", 409)
        cur.execute(
            "UPDATE sedes SET estado = 'Eliminada', fecha_eliminacion = %s WHERE id_sede = %s",
            (ahora_local(), id_sede),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sede_de_tienda(cur, id_tienda: int, id_sede) -> int:
    """id de una sede activa del restaurante, o ValueError."""
    try:
        id_sede = int(id_sede)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sede invalida.") from exc
    cur.execute(
        "SELECT 1 FROM sedes WHERE id_sede = %s AND id_tienda = %s AND estado = 'Activa' LIMIT 1",
        (id_sede, id_tienda),
    )
    if not cur.fetchone():
        raise ValueError("La sede elegida no existe.")
    return id_sede
