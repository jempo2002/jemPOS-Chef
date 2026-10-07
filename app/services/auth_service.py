from __future__ import annotations

import hashlib
import re

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.utils.mail import send_recovery_email_async
from database import get_db

# Charset cerrado: el anterior ([^\s@]+) aceptaba "<img/src=x/onerror=...>@a.co"
# y ese correo terminaba pintado en el Panel Master (XSS almacenado en jemPOS).
_EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
_PWD_UPPER_RE = re.compile(r"[A-Z]")
_PWD_LOWER_RE = re.compile(r"[a-z]")
_PWD_NUMBER_RE = re.compile(r"\d")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(str(email or "").strip().lower()))


def first_password_policy_error(password: str) -> str | None:
    raw = str(password or "")
    if not raw:
        return "La contrasena es requerida."
    if len(raw) < 8:
        return "La contrasena debe tener al menos 8 caracteres."
    if not _PWD_UPPER_RE.search(raw):
        return "La contrasena debe incluir al menos una letra mayuscula."
    if not _PWD_LOWER_RE.search(raw):
        return "La contrasena debe incluir al menos una letra minuscula."
    if not _PWD_NUMBER_RE.search(raw):
        return "La contrasena debe incluir al menos un numero."
    return None


# Libera correo y cc (UNIQUE) de un usuario eliminado anteponiendo
# 'deleted_<unix_ts>_'. CONCAT con cc NULL da NULL. SQL fijo, sin datos del
# usuario. Viene de migrations/2026-09-23_liberar_unicos_eliminados.sql de jemPOS.
LIBERAR_USUARIO_SQL = (
    "estado_activo = 0, "
    "correo = CONCAT('deleted_', UNIX_TIMESTAMP(), '_', correo), "
    "cc = CONCAT('deleted_', UNIX_TIMESTAMP(), '_', cc)"
)


def liberar_datos_inactivos(cur, correo: str, cc: str | None = None) -> None:
    """Si el correo o la cc pertenecen a un usuario inactivo (eliminado antes
    de que existiera el prefijo deleted_), los libera para poder reusarlos.
    Un usuario activo no se toca: ese sigue bloqueando el registro."""
    cur.execute(
        "UPDATE usuarios SET " + LIBERAR_USUARIO_SQL +
        " WHERE estado_activo = 0 AND (correo = %s OR cc = %s)",
        (correo, cc),
    )


def initialize_user_session(session_obj, user: dict, sede: dict | None) -> None:
    session_obj.clear()
    session_obj.permanent = True
    session_obj["id_usuario"] = user["id_usuario"]
    session_obj["id_tienda"] = user["id_tienda"]
    session_obj["nombre_completo"] = user["nombre_completo"]
    session_obj["rol"] = user["rol"]
    if sede:
        fijar_sede(session_obj, sede)


def fijar_sede(session_obj, sede: dict) -> None:
    session_obj["id_sede"] = sede["id_sede"]
    session_obj["nombre_sede"] = sede["nombre"]


def sedes_activas(id_tienda: int) -> list[dict]:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id_sede, nombre, direccion, es_principal FROM sedes "
            "WHERE id_tienda = %s AND estado = 'Activa' "
            "ORDER BY es_principal DESC, nombre",
            (id_tienda,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def sede_inicial(user: dict) -> dict | None:
    """Sede con la que arranca la sesion, o None si hay que elegirla.

    Usuario atado a una sede: esa (si sigue activa). Admin sin sede fija: la
    unica activa del restaurante; con varias, la elige en /seleccionar-sede.
    """
    if not user.get("id_tienda"):
        return None
    sedes = sedes_activas(user["id_tienda"])
    if user.get("id_sede"):
        return next((s for s in sedes if s["id_sede"] == user["id_sede"]), None)
    return sedes[0] if len(sedes) == 1 else None


# Pantalla de entrada por rol. Caja, mesas y cocina llegan en sus propios
# modulos; mientras tanto todos entran a /inicio.
_INICIO_POR_ROL = {"master": "/panel-master"}


def resolve_post_login_redirect(rol: str) -> str:
    return _INICIO_POR_ROL.get(str(rol or "").strip().lower(), "/inicio")


def huella_clave(clave_hash: str) -> str:
    """Huella del hash actual. Va dentro del token de reset: al cambiar la
    contrasena la huella cambia y el enlace deja de servir (antes se podia
    reusar durante 30 min, incluso despues de usarlo)."""
    return hashlib.sha256(str(clave_hash or "").encode()).hexdigest()[:16]


def create_reset_token(secret_key: str, email: str, clave_hash: str, salt: str = "password-reset-salt") -> str:
    serializer = URLSafeTimedSerializer(secret_key)
    return serializer.dumps([str(email or "").strip().lower(), huella_clave(clave_hash)], salt=salt)


def decode_reset_token(
    secret_key: str,
    token: str,
    salt: str = "password-reset-salt",
    max_age: int = 1800,
) -> tuple[str, str]:
    """(correo, huella). Lanza BadSignature/SignatureExpired si no vale."""
    serializer = URLSafeTimedSerializer(secret_key)
    datos = serializer.loads(token, salt=salt, max_age=max_age)
    if not (isinstance(datos, list) and len(datos) == 2):
        raise BadSignature("Formato de token antiguo.")
    return str(datos[0]), str(datos[1])


def send_recovery_email(destinatario: str, enlace: str) -> bool:
    """Encola el correo de recuperacion en un hilo aparte: un fallo de SMTP
    no bloquea ni rompe la peticion."""
    if not destinatario or not enlace:
        return False

    send_recovery_email_async(destinatario=destinatario, enlace=enlace)
    return True
