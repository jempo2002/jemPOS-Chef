from __future__ import annotations

from functools import wraps

from flask import current_app, flash, g, jsonify, redirect, request, session, url_for

from app.utils.helpers import hoy_local
from database import get_db

MSG_SOLO_LECTURA = (
    "Suscripción vencida. Tu cuenta está en modo lectura. "
    "Renueva para volver a vender y registrar datos."
)
# Vencida la suscripcion, la cuenta queda en solo lectura: todo GET sigue
# abierto y cualquier otro metodo se rechaza (por defecto, asi una ruta nueva
# nunca queda sin proteger). Unicas escrituras permitidas: salir y elegir sede.
_METODOS_LECTURA = frozenset({"GET", "HEAD", "OPTIONS"})
_ESCRITURA_PERMITIDA = frozenset({"auth.logout", "auth.api_logout", "auth.seleccionar_sede"})
# Rutas que funcionan sin sede elegida: las que sirven para elegirla o salir.
_SIN_SEDE = frozenset({"auth.seleccionar_sede", "auth.logout", "auth.api_logout"})


def _is_api_request() -> bool:
    path = request.path or ""
    if request.is_json:
        return True
    if path.startswith("/api") or "/api/" in path:
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.accept_mimetypes
    if accept and accept.best == "application/json":
        return True
    return False


def log_seguridad(evento: str, **datos) -> None:
    """Una linea WARNING por evento sospechoso, con quien y desde donde."""
    extra = " ".join(f"{k}={v!r}" for k, v in datos.items())
    current_app.logger.warning(
        "SEGURIDAD %s ip=%s usuario=%s tienda=%s sede=%s ruta=%s %s",
        evento, request.remote_addr, session.get("id_usuario"),
        session.get("id_tienda"), session.get("id_sede"), request.path, extra,
    )


def _usuario_vigente(user_id, id_sede) -> dict | None:
    """Usuario, su tienda y la sede de la sesion en una sola consulta por PK."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            # Sin pago registrado (fecha_fin NULL) manda el fin de la prueba.
            "SELECT u.rol, u.id_tienda, u.id_sede AS sede_fija, u.estado_activo, "
            "t.plan_id, t.estado AS estado_tienda, "
            "COALESCE(t.fecha_fin_suscripcion, t.trial_ends_at) AS fecha_fin_suscripcion, "
            "s.id_tienda AS sede_tienda, s.estado AS estado_sede "
            "FROM usuarios u "
            "LEFT JOIN tiendas t ON t.id_tienda = u.id_tienda "
            "LEFT JOIN sedes s ON s.id_sede = %s "
            "WHERE u.id_usuario = %s LIMIT 1",
            (id_sede, user_id),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _sede_valida(fila: dict, id_sede) -> bool:
    """La sede de la sesion existe, esta activa, es de su tienda y, si el
    usuario esta atado a una sede, es esa."""
    if not id_sede:
        return False
    if fila["estado_sede"] != "Activa" or fila["sede_tienda"] != fila["id_tienda"]:
        return False
    return fila["sede_fija"] is None or fila["sede_fija"] == id_sede


def _bloqueo_solo_lectura():
    """Respuesta de rechazo para una tienda vencida, o None si la peticion pasa."""
    if request.method in _METODOS_LECTURA or request.endpoint in _ESCRITURA_PERMITIDA:
        return None
    log_seguridad("escritura_suscripcion_vencida", metodo=request.method)
    # Siempre JSON: toda escritura del POS es fetch, y el front lee `code`.
    return jsonify({"ok": False, "code": "suscripcion_vencida", "msg": MSG_SOLO_LECTURA}), 402


def _sesion_expirada():
    session.clear()
    if _is_api_request():
        return jsonify({"ok": False, "msg": "Sesion expirada."}), 401
    return redirect(url_for("auth.login"))


def login_required(f):
    """Exige una sesion valida en paginas y APIs.

    La sesion se contrasta con la base en cada peticion: un usuario eliminado,
    desactivado o movido de tienda pierde el acceso al instante, un cambio de
    rol aplica sin volver a entrar, y una suscripcion vencida pasa la cuenta a
    solo lectura tambien en las APIs (ver _bloqueo_solo_lectura).

    Sedes: todo usuario de un restaurante trabaja en una sede (session
    id_sede). Si la sede se elimina, o el usuario se mueve a otra, la sesion
    vuelve a elegir sede (Admin) o se cierra (los demas roles).

    Deja en `g`: plan_id, id_sede, sede_fija, es_master, dias_suscripcion y
    tienda_vencida (el banner y los botones de las plantillas leen de ahi).
    ponytail: 1 SELECT por PK por peticion; no se cachea en Redis a proposito
    (Redis es solo para sesion y limites).
    """
    @wraps(f)
    def _inner(*args, **kwargs):
        user_id = session.get("id_usuario")
        id_sede = session.get("id_sede")
        fila = _usuario_vigente(user_id, id_sede) if user_id and session.get("rol") else None
        if (
            not fila
            or not fila["estado_activo"]
            or fila["id_tienda"] != session.get("id_tienda")
            or (fila["rol"] != "Master" and fila["estado_tienda"] == "Eliminado")
        ):
            if user_id:
                log_seguridad("sesion_revocada")
            return _sesion_expirada()
        session["rol"] = fila["rol"]
        g.es_master = fila["rol"] == "Master"
        g.plan_id = fila["plan_id"]
        g.sede_fija = fila["sede_fija"]

        if not g.es_master and not _sede_valida(fila, id_sede):
            session.pop("id_sede", None)
            session.pop("nombre_sede", None)
            if fila["sede_fija"] is not None:
                # Cajero/Mesero/Cocina: su sede cambio o se elimino.
                log_seguridad("sede_revocada")
                return _sesion_expirada()
            if request.endpoint not in _SIN_SEDE:
                if _is_api_request():
                    return jsonify({"ok": False, "code": "sin_sede", "msg": "Elige una sede para continuar."}), 409
                return redirect(url_for("auth.seleccionar_sede"))
        g.id_sede = session.get("id_sede")

        vence = fila["fecha_fin_suscripcion"]
        g.dias_suscripcion = None if g.es_master or vence is None else (vence - hoy_local()).days
        g.tienda_vencida = g.dias_suscripcion is not None and g.dias_suscripcion <= 0
        if g.tienda_vencida:
            rechazo = _bloqueo_solo_lectura()
            if rechazo is not None:
                return rechazo
        return f(*args, **kwargs)

    return _inner


def roles_required(*roles: str):
    """Solo los roles dados (comparacion sin mayusculas). Va despues de
    login_required."""
    allowed_roles = {
        str(role).strip().lower()
        for role in roles
        if isinstance(role, str) and role.strip()
    }
    if not allowed_roles:
        raise ValueError("roles_required necesita al menos un rol valido.")

    def decorator(f):
        @wraps(f)
        def _inner(*args, **kwargs):
            current_role = str(session.get("rol") or "").strip().lower()
            if current_role not in allowed_roles:
                log_seguridad("rol_denegado", requerido=sorted(allowed_roles))
                if _is_api_request():
                    return jsonify({"ok": False, "msg": "No tienes permisos para esta accion."}), 403
                flash("No tienes permisos para ver esta pantalla.", "error")
                return redirect(url_for("core.inicio"))
            return f(*args, **kwargs)

        return _inner

    return decorator
