"""Panel Master: alta de restaurantes, plan, suscripcion y baja.

Sin registro publico, igual que jemPOS: el Master crea el restaurante cuando
el cliente paga la implementacion, con un mes de prueba del plan elegido.
"""
from __future__ import annotations

import calendar
from datetime import date

from mysql.connector import IntegrityError
from werkzeug.security import generate_password_hash

from app.services import plan_service
from app.services.auth_service import (
    LIBERAR_USUARIO_SQL,
    first_password_policy_error,
    is_valid_email,
    liberar_datos_inactivos,
)
from app.services.usuario_service import _parse_cc
from app.utils.helpers import ahora_local, hoy_local, normalize_phone
from app.utils.validation import sanitize_optional_text, sanitize_text
from database import get_db

MESES_PRUEBA = 1
PERIODOS = (1, 3, 6, 12)


class MasterError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def sumar_meses(base: date, meses: int) -> date:
    m = base.month - 1 + meses
    anio = base.year + m // 12
    mes = m % 12 + 1
    return date(anio, mes, min(base.day, calendar.monthrange(anio, mes)[1]))


def _parse_plan(raw) -> str:
    plan = str(raw or "").strip()
    if plan not in plan_service.PLANES:
        raise ValueError("Plan invalido.")
    return plan


def listar_restaurantes() -> list[dict]:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT t.id_tienda, t.nombre_negocio, t.nit, t.telefono, t.plan_id, "
            "t.fecha_fin_suscripcion, t.trial_ends_at, "
            "(SELECT COUNT(*) FROM sedes s WHERE s.id_tienda = t.id_tienda AND s.estado = 'Activa') AS sedes, "
            "(SELECT COUNT(*) FROM usuarios u WHERE u.id_tienda = t.id_tienda AND u.estado_activo = 1) AS usuarios "
            "FROM tiendas t WHERE t.estado <> 'Eliminado' ORDER BY t.nombre_negocio"
        )
        filas = cur.fetchall()
    finally:
        conn.close()
    hoy = hoy_local()
    for f in filas:
        f["plan_nombre"] = plan_service.plan_de(f["plan_id"])["nombre"]
        f["mensualidad"] = plan_service.mensualidad(f["plan_id"], f["sedes"])
        f["en_prueba"] = f["fecha_fin_suscripcion"] is None and f["trial_ends_at"] is not None
        f["vence"] = f["fecha_fin_suscripcion"] or f["trial_ends_at"]
        f["dias"] = (f["vence"] - hoy).days if f["vence"] else None
    return filas


def crear_restaurante(data: dict) -> int:
    """Tienda + sede principal + Admin dueno, en una sola transaccion."""
    nombre = sanitize_text(data.get("nombre_negocio"), "El nombre del restaurante", max_len=150)
    nit = sanitize_optional_text(data.get("nit"), "NIT", max_len=30)
    telefono = normalize_phone(data.get("telefono"), max_len=20)
    plan_id = _parse_plan(data.get("plan_id"))
    sede_nombre = sanitize_text(data.get("sede_nombre") or "Principal", "El nombre de la sede", max_len=120)
    sede_direccion = sanitize_optional_text(data.get("sede_direccion"), "La direccion", max_len=200)
    admin_nombre = sanitize_text(data.get("admin_nombre"), "El nombre del administrador", max_len=150)
    admin_cc = _parse_cc(data.get("admin_cc"))
    admin_correo = str(data.get("admin_correo", "")).strip().lower()
    password = str(data.get("admin_password", ""))
    if not admin_correo or len(admin_correo) > 150 or not is_valid_email(admin_correo):
        raise ValueError("El correo del administrador no es valido.")
    if len(password) > 128:
        raise ValueError("La contrasena supera el maximo permitido.")
    pwd_error = first_password_policy_error(password)
    if pwd_error:
        raise ValueError(pwd_error)

    fin_prueba = sumar_meses(hoy_local(), MESES_PRUEBA)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        liberar_datos_inactivos(cur, admin_correo, admin_cc)
        cur.execute(
            "SELECT 1 FROM usuarios WHERE correo = %s OR cc = %s LIMIT 1", (admin_correo, admin_cc)
        )
        if cur.fetchone():
            raise MasterError("Ya existe un usuario con ese correo o cedula.", 409)
        cur.execute(
            "INSERT INTO tiendas (nombre_negocio, nit, telefono, es_restaurante, plan_id, trial_ends_at, estado_suscripcion) "
            "VALUES (%s, %s, %s, 1, %s, %s, 'activa')",
            (nombre, nit or admin_cc, telefono, plan_id, fin_prueba),
        )
        id_tienda = cur.lastrowid
        cur.execute(
            "INSERT INTO sedes (id_tienda, nombre, direccion, telefono, es_principal) VALUES (%s, %s, %s, %s, 1)",
            (id_tienda, sede_nombre, sede_direccion, telefono),
        )
        cur.execute(
            # Admin sin sede fija (NULL): ve todas las sedes del restaurante.
            "INSERT INTO usuarios (id_tienda, id_sede, nombre_completo, correo, clave_hash, rol, cc) "
            "VALUES (%s, NULL, %s, %s, %s, 'Admin', %s)",
            (id_tienda, admin_nombre, admin_correo, generate_password_hash(password), admin_cc),
        )
        conn.commit()
        return id_tienda
    except IntegrityError as exc:
        conn.rollback()
        raise MasterError("El NIT, el correo o la cedula ya estan registrados.", 409) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cambiar_plan(id_tienda: int, raw_plan) -> str:
    plan_id = _parse_plan(raw_plan)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT 1 FROM tiendas WHERE id_tienda = %s AND estado <> 'Eliminado' LIMIT 1", (id_tienda,)
        )
        if not cur.fetchone():
            raise MasterError("Restaurante no encontrado.", 404)
        plan_service.verificar_cambio_plan(cur, id_tienda, plan_id)
        cur.execute("UPDATE tiendas SET plan_id = %s WHERE id_tienda = %s", (plan_id, id_tienda))
        conn.commit()
        return plan_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def renovar(id_tienda: int, raw_meses) -> date:
    """Registra un pago de `meses`. Corre desde hoy o desde el vencimiento
    vigente, lo que sea mas tarde: pagar antes de tiempo no pierde dias."""
    try:
        meses = int(raw_meses)
    except (TypeError, ValueError) as exc:
        raise ValueError("Selecciona un periodo valido.") from exc
    if meses not in PERIODOS:
        raise ValueError("Periodo de suscripcion no permitido.")
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT COALESCE(fecha_fin_suscripcion, trial_ends_at) AS vence FROM tiendas "
            "WHERE id_tienda = %s AND estado <> 'Eliminado' FOR UPDATE",
            (id_tienda,),
        )
        tienda = cur.fetchone()
        if not tienda:
            raise MasterError("Restaurante no encontrado.", 404)
        hoy = hoy_local()
        desde = max(hoy, tienda["vence"] or hoy)
        fin = sumar_meses(desde, meses)
        cur.execute(
            "UPDATE tiendas SET fecha_inicio_suscripcion = %s, fecha_fin_suscripcion = %s, "
            "estado_suscripcion = 'activa' WHERE id_tienda = %s",
            (hoy, fin, id_tienda),
        )
        conn.commit()
        return fin
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def eliminar_restaurante(id_tienda: int) -> None:
    """Soft delete: un DELETE real borraria el historial contable. Se liberan
    el NIT y los correos para poder registrarlos de nuevo."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tiendas SET estado = 'Eliminado', estado_suscripcion = 'suspendida', "
            "fecha_fin_suscripcion = CURDATE(), nit = CONCAT('deleted_', UNIX_TIMESTAMP(), '_', nit) "
            "WHERE id_tienda = %s AND estado <> 'Eliminado'",
            (id_tienda,),
        )
        if cur.rowcount == 0:
            raise MasterError("Restaurante no encontrado.", 404)
        cur.execute(
            "UPDATE sedes SET estado = 'Eliminada', fecha_eliminacion = %s "
            "WHERE id_tienda = %s AND estado = 'Activa'",
            (ahora_local(), id_tienda),
        )
        cur.execute(
            "UPDATE usuarios SET " + LIBERAR_USUARIO_SQL +
            " WHERE id_tienda = %s AND rol <> 'Master' AND estado_activo = 1",
            (id_tienda,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
