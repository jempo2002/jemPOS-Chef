from __future__ import annotations

import hmac

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

from app import limiter
from app.security import cerrar_sesion_publica
from app.services.auth_service import (
    create_reset_token,
    decode_reset_token,
    fijar_sede,
    first_password_policy_error,
    huella_clave,
    initialize_user_session,
    is_valid_email,
    resolve_post_login_redirect,
    sede_inicial,
    sedes_activas,
    send_recovery_email,
)
from app.utils.decorators import log_seguridad, login_required
from app.utils.validation import parse_int
from database import get_db

auth = Blueprint("auth", __name__)
LOGIN_RATE_LIMIT = "5 per minute"
# Por cuenta, no por IP: frena la fuerza bruta distribuida contra un correo.
LOGIN_CUENTA_RATE_LIMIT = "10 per 15 minutes"
# Hash valido para comparar cuando el correo no existe: iguala el tiempo de
# respuesta y no delata que cuentas existen.
_HASH_SENUELO = generate_password_hash("chef-senuelo")
_CREDENCIALES_INVALIDAS = "Correo o contrasena incorrectos."
_SEDE_INACTIVA = "Tu sede no esta activa. Pide al administrador que te asigne otra."
_USUARIO_LOGIN_SQL = (
    "SELECT id_usuario, id_tienda, id_sede, nombre_completo, clave_hash, rol, estado_activo "
    "FROM usuarios WHERE correo = %s LIMIT 1"
)


def _datos_peticion() -> dict:
    return (request.get_json(silent=True) if request.is_json else request.form) or {}


def _correo_login() -> str:
    return "login:" + str(_datos_peticion().get("correo", "")).strip().lower()[:150]


def _error_login(msg: str, status: int, field: str | None = None):
    if request.is_json:
        cuerpo = {"ok": False, "msg": msg}
        if field:
            cuerpo["field"] = field
        return jsonify(cuerpo), status
    flash(msg, "error")
    return redirect(url_for("auth.login"))


@auth.route("/login", methods=["GET", "POST"])
@limiter.limit(LOGIN_RATE_LIMIT, methods=["POST"])
@limiter.limit(LOGIN_CUENTA_RATE_LIMIT, methods=["POST"], key_func=_correo_login)
def login():
    if request.method == "GET":
        # Ruta publica: abrir el login destruye cualquier sesion activa en vez
        # de reenviar adentro. Dejar la sesion anterior viva permitia seguir
        # usandola con el boton "atras". Se usa el helper para no borrar los
        # flashes que trae el redirect.
        cerrar_sesion_publica()
        return render_template("auth/login.html")

    data = _datos_peticion()
    correo = str(data.get("correo", "")).strip().lower()
    contrasena = str(data.get("contrasena", ""))

    if not correo or not contrasena:
        return _error_login("Correo y contrasena son requeridos.", 400)
    if len(correo) > 150 or not is_valid_email(correo):
        return _error_login("Correo invalido.", 400)
    if len(contrasena) > 128:
        return _error_login("La contrasena supera el maximo permitido.", 400)

    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(_USUARIO_LOGIN_SQL, (correo,))
        user = cur.fetchone()
    finally:
        conn.close()

    # Mismo mensaje y mismo coste de hash exista o no el correo.
    clave_ok = check_password_hash(user["clave_hash"] if user else _HASH_SENUELO, contrasena)
    if not user or not clave_ok:
        log_seguridad("login_fallido", correo=correo, existe=bool(user))
        return _error_login(_CREDENCIALES_INVALIDAS, 401, field="contrasena")

    if not user["estado_activo"]:
        return _error_login("Cuenta desactivada. Contacta al administrador.", 403)

    sede = sede_inicial(user)
    if user["id_sede"] and not sede:
        return _error_login(_SEDE_INACTIVA, 403)

    initialize_user_session(session, user, sede)
    # ID de sesion nuevo al autenticarse: anula una fijacion de sesion previa.
    # (Solo las interfaces de Flask-Session lo tienen.)
    if hasattr(current_app.session_interface, "regenerate"):
        current_app.session_interface.regenerate(session)

    if user["rol"] != "Master" and not sede:
        redirect_url = url_for("auth.seleccionar_sede")
    else:
        redirect_url = resolve_post_login_redirect(user["rol"])
    if request.is_json:
        return jsonify({"ok": True, "redirect": redirect_url})
    return redirect(redirect_url)


@auth.route("/seleccionar-sede", methods=["GET", "POST"])
@login_required
def seleccionar_sede():
    """El Admin de un restaurante con varias sedes elige en cual trabaja. Los
    demas roles estan atados a la suya y no pasan por aqui."""
    if session.get("rol") == "Master":
        return redirect(resolve_post_login_redirect("Master"))
    sedes = sedes_activas(session["id_tienda"])
    if request.method == "GET":
        return render_template("auth/seleccionar_sede.html", sedes=sedes, sede_actual=session.get("id_sede"))

    data = _datos_peticion()
    try:
        id_sede = parse_int(data.get("id_sede"), "Sede", min_value=1)
    except ValueError as exc:
        return _respuesta_sede(str(exc), 400)
    sede = next((s for s in sedes if s["id_sede"] == id_sede), None)
    # Quien esta atado a una sede (Cajero, Mesero, Cocina) no se cambia solo.
    if not sede or g.sede_fija not in (None, id_sede):
        log_seguridad("sede_ajena", id_sede=id_sede)
        return _respuesta_sede("Sede no encontrada.", 404)
    fijar_sede(session, sede)
    destino = resolve_post_login_redirect(session.get("rol"))
    if request.is_json:
        return jsonify({"ok": True, "redirect": destino, "msg": f"Trabajando en {sede['nombre']}."})
    return redirect(destino)


def _respuesta_sede(msg: str, status: int):
    if request.is_json:
        return jsonify({"ok": False, "msg": msg}), status
    flash(msg, "error")
    return redirect(url_for("auth.seleccionar_sede"))


@auth.route("/logout", methods=["POST"])
def logout():
    # POST + CSRF: un <img src="/logout"> en otra pagina no saca al usuario.
    session.clear()
    return redirect(url_for("auth.login"))


@auth.route("/api/auth/login", methods=["POST"])
@limiter.limit(LOGIN_RATE_LIMIT)
def api_login():
    return login()


@auth.route("/api/auth/logout", methods=["POST"])
@login_required
def api_logout():
    session.clear()
    return jsonify({"ok": True, "redirect": "/login"})


@auth.route("/olvide-password", methods=["GET", "POST"])
@limiter.limit("3 per minute; 10 per hour", methods=["POST"])
def olvide_password():
    if request.method == "POST":
        correo = str(request.form.get("correo", "")).strip().lower()
        if correo:
            if len(correo) > 150 or not is_valid_email(correo):
                flash("Debes ingresar un correo valido.", "error")
                return redirect(url_for("auth.olvide_password"))
            conn = get_db()
            try:
                cur = conn.cursor(dictionary=True)
                cur.execute(
                    "SELECT correo, clave_hash FROM usuarios WHERE correo = %s AND estado_activo = 1 LIMIT 1",
                    (correo,),
                )
                user = cur.fetchone()
            finally:
                conn.close()

            if user:
                token = create_reset_token(current_app.secret_key, correo, user["clave_hash"])
                enlace = url_for("auth.reset_password", token=token, _external=True)
                # En segundo plano: los fallos de SMTP quedan en el log.
                send_recovery_email(correo, enlace)

        flash("Si el correo existe, recibiras un enlace de recuperacion.", "success")
        return redirect(url_for("auth.olvide_password"))

    return render_template("auth/olvide_password.html")


@auth.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def reset_password(token):
    try:
        correo, huella = decode_reset_token(current_app.secret_key, token)
    except (SignatureExpired, BadSignature):
        flash("Enlace inválido o expirado", "error")
        return redirect(url_for("auth.login"))

    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id_usuario, correo, estado_activo, clave_hash FROM usuarios WHERE correo = %s LIMIT 1",
            (correo,),
        )
        user = cur.fetchone()
    finally:
        conn.close()

    if (
        not correo
        or not user
        or not user.get("estado_activo")
        or not hmac.compare_digest(huella, huella_clave(user["clave_hash"]))
    ):
        flash("Enlace inválido o expirado", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        password = str(request.form.get("password", ""))
        confirm = str(request.form.get("confirm_password", ""))

        if password != confirm:
            flash("Las contrasenas no coinciden.", "error")
            return redirect(url_for("auth.reset_password", token=token))
        if len(password) > 128:
            flash("La contrasena supera el maximo permitido.", "error")
            return redirect(url_for("auth.reset_password", token=token))
        pwd_error = first_password_policy_error(password)
        if pwd_error:
            flash(pwd_error, "error")
            return redirect(url_for("auth.reset_password", token=token))

        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE usuarios SET clave_hash = %s WHERE id_usuario = %s AND correo = %s",
                (generate_password_hash(password), user["id_usuario"], user["correo"]),
            )
            conn.commit()
        finally:
            conn.close()

        flash("Tu contrasena fue actualizada correctamente.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)
