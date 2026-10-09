"""Equipo de un restaurante: el Admin crea, edita y desactiva usuarios.

Soft delete: estado_activo = 0 y el correo y la cedula quedan liberados con el
prefijo deleted_<ts>_ (LIBERAR_USUARIO_SQL), asi se pueden volver a usar.
"""
from __future__ import annotations

from mysql.connector import IntegrityError
from werkzeug.security import generate_password_hash

from app.services import plan_service
from app.services.auth_service import (
    LIBERAR_USUARIO_SQL,
    first_password_policy_error,
    is_valid_email,
    liberar_datos_inactivos,
)
from app.services.sede_service import sede_de_tienda
from app.utils.helpers import only_digits
from app.utils.validation import sanitize_text
from database import get_db

# Roles que un Admin puede dar. Master solo existe fuera de los restaurantes.
ROLES_RESTAURANTE = ("Admin", "Cajero", "Mesero", "Cocina")


class UsuarioError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _parse_cc(raw) -> str:
    cc = only_digits(raw)
    if not cc:
        raise ValueError("La cedula es requerida.")
    if not 5 <= len(cc) <= 15:
        raise ValueError("La cedula debe tener entre 5 y 15 digitos.")
    return cc


def _parse_rol(raw) -> str:
    rol = str(raw or "").strip().capitalize()
    if rol not in ROLES_RESTAURANTE:
        raise ValueError("Rol invalido.")
    return rol


def _parse_sede(cur, id_tienda: int, rol: str, raw) -> int | None:
    """Admin: NULL = todas las sedes. Los demas roles siempre tienen una; si
    el restaurante tiene una sola sede se usa esa."""
    if raw not in (None, ""):
        return sede_de_tienda(cur, id_tienda, raw)
    if rol == "Admin":
        return None
    cur.execute(
        "SELECT id_sede FROM sedes WHERE id_tienda = %s AND estado = 'Activa'",
        (id_tienda,),
    )
    sedes = cur.fetchall()
    if len(sedes) != 1:
        raise ValueError("Elige la sede donde trabaja.")
    return sedes[0]["id_sede"]


def listar_usuarios(id_tienda: int) -> list[dict]:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT u.id_usuario, u.nombre_completo, u.correo, u.cc, u.rol, u.id_sede, s.nombre AS sede "
            "FROM usuarios u LEFT JOIN sedes s ON s.id_sede = u.id_sede "
            "WHERE u.id_tienda = %s AND u.estado_activo = 1 AND u.rol <> 'Master' "
            "ORDER BY FIELD(u.rol, 'Admin', 'Cajero', 'Mesero', 'Cocina'), u.nombre_completo",
            (id_tienda,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def crear_usuario(id_tienda: int, data: dict) -> int:
    """Lanza ValueError, LimitePlanError o UsuarioError."""
    nombre = sanitize_text(data.get("nombre"), "El nombre completo", max_len=150)
    cc = _parse_cc(data.get("cc"))
    rol = _parse_rol(data.get("rol"))
    correo = str(data.get("correo", "")).strip().lower()
    password = str(data.get("password", ""))
    if not correo or len(correo) > 150 or not is_valid_email(correo):
        raise ValueError("El correo no es valido.")
    if len(password) > 128:
        raise ValueError("La contrasena supera el maximo permitido.")
    if password != str(data.get("confirm_password", "")):
        raise ValueError("Las contrasenas no coinciden.")
    pwd_error = first_password_policy_error(password)
    if pwd_error:
        raise ValueError(pwd_error)

    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        liberar_datos_inactivos(cur, correo, cc)
        cur.execute("SELECT 1 FROM usuarios WHERE correo = %s OR cc = %s LIMIT 1", (correo, cc))
        if cur.fetchone():
            raise UsuarioError("Ya existe un usuario con ese correo o cedula.", 409)
        id_sede = _parse_sede(cur, id_tienda, rol, data.get("id_sede"))
        plan_service.verificar_limite(cur, id_tienda, "usuarios")
        if rol == "Admin":
            plan_service.verificar_limite(cur, id_tienda, "admins")
        cur.execute(
            "INSERT INTO usuarios (id_tienda, id_sede, nombre_completo, correo, clave_hash, rol, cc) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (id_tienda, id_sede, nombre, correo, generate_password_hash(password), rol, cc),
        )
        id_usuario = cur.lastrowid
        conn.commit()
        return id_usuario
    except IntegrityError as exc:
        # Carrera entre el SELECT y el INSERT: uq_usuarios_correo / uq_usuarios_cc.
        conn.rollback()
        raise UsuarioError("Ya existe un usuario con ese correo o cedula.", 409) from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _usuario_de_tienda(cur, id_tienda: int, id_usuario: int) -> dict:
    cur.execute(
        "SELECT id_usuario, rol FROM usuarios "
        "WHERE id_usuario = %s AND id_tienda = %s AND estado_activo = 1 AND rol <> 'Master' FOR UPDATE",
        (id_usuario, id_tienda),
    )
    usuario = cur.fetchone()
    if not usuario:
        raise UsuarioError("Usuario no encontrado.", 404)
    return usuario


def _es_ultimo_admin(cur, id_tienda: int, id_usuario: int) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM usuarios "
        "WHERE id_tienda = %s AND rol = 'Admin' AND estado_activo = 1 AND id_usuario <> %s",
        (id_tienda, id_usuario),
    )
    return cur.fetchone()["n"] == 0


def actualizar_usuario(id_tienda: int, id_actor: int, id_usuario: int, data: dict) -> None:
    nombre = sanitize_text(data.get("nombre"), "El nombre completo", max_len=150)
    rol = _parse_rol(data.get("rol"))
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        usuario = _usuario_de_tienda(cur, id_tienda, id_usuario)
        if usuario["rol"] == "Admin" and rol != "Admin":
            if id_usuario == id_actor:
                raise UsuarioError("No puedes quitarte el rol de Admin a ti mismo.")
            if _es_ultimo_admin(cur, id_tienda, id_usuario):
                raise UsuarioError("El restaurante debe tener al menos un Admin.")
        if rol == "Admin" and usuario["rol"] != "Admin":
            plan_service.verificar_limite(cur, id_tienda, "admins")
        id_sede = _parse_sede(cur, id_tienda, rol, data.get("id_sede"))
        cur.execute(
            "UPDATE usuarios SET nombre_completo = %s, rol = %s, id_sede = %s WHERE id_usuario = %s",
            (nombre, rol, id_sede, id_usuario),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def desactivar_usuario(id_tienda: int, id_actor: int, id_usuario: int) -> None:
    if id_usuario == id_actor:
        raise UsuarioError("No puedes desactivar tu propia cuenta.")
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        usuario = _usuario_de_tienda(cur, id_tienda, id_usuario)
        if usuario["rol"] == "Admin" and _es_ultimo_admin(cur, id_tienda, id_usuario):
            raise UsuarioError("El restaurante debe tener al menos un Admin.")
        cur.execute("UPDATE usuarios SET " + LIBERAR_USUARIO_SQL + " WHERE id_usuario = %s", (id_usuario,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
