"""Sedes y equipo del restaurante (rol Admin)."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from app.services import sede_service, usuario_service
from app.services.plan_service import LimitePlanError
from app.utils.decorators import login_required, roles_required

restaurante = Blueprint("restaurante", __name__)


def _json() -> dict:
    return request.get_json(silent=True) or {}


def _error(exc: Exception):
    """Traduce las excepciones de los servicios a la respuesta JSON."""
    if isinstance(exc, LimitePlanError):
        cuerpo, status = exc.respuesta()
        return jsonify(cuerpo), status
    status = getattr(exc, "status", 400)
    return jsonify({"ok": False, "msg": str(exc)}), status


_ERRORES = (ValueError, LimitePlanError, sede_service.SedeError, usuario_service.UsuarioError)


@restaurante.get("/sedes")
@login_required
@roles_required("Admin")
def sedes_page():
    return render_template("sedes.html", **sede_service.resumen_sedes(session["id_tienda"]))


@restaurante.post("/api/sedes")
@login_required
@roles_required("Admin")
def api_sedes_crear():
    try:
        id_sede = sede_service.crear_sede(session["id_tienda"], _json())
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "id_sede": id_sede, "msg": "Sede creada."}), 201


@restaurante.put("/api/sedes/<int:id_sede>")
@login_required
@roles_required("Admin")
def api_sedes_actualizar(id_sede):
    try:
        nombre = sede_service.actualizar_sede(session["id_tienda"], id_sede, _json())
    except _ERRORES as exc:
        return _error(exc)
    if session.get("id_sede") == id_sede:
        session["nombre_sede"] = nombre
    return jsonify({"ok": True, "msg": "Sede actualizada."})


@restaurante.delete("/api/sedes/<int:id_sede>")
@login_required
@roles_required("Admin")
def api_sedes_eliminar(id_sede):
    try:
        sede_service.eliminar_sede(session["id_tienda"], id_sede)
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "msg": "Sede eliminada."})


@restaurante.get("/equipo")
@login_required
@roles_required("Admin")
def equipo_page():
    id_tienda = session["id_tienda"]
    return render_template(
        "equipo.html",
        usuarios=usuario_service.listar_usuarios(id_tienda),
        sedes=sede_service.resumen_sedes(id_tienda)["sedes"],
        roles=usuario_service.ROLES_RESTAURANTE,
    )


@restaurante.post("/api/usuarios")
@login_required
@roles_required("Admin")
def api_usuarios_crear():
    try:
        id_usuario = usuario_service.crear_usuario(session["id_tienda"], _json())
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "id_usuario": id_usuario, "msg": "Usuario creado."}), 201


@restaurante.put("/api/usuarios/<int:id_usuario>")
@login_required
@roles_required("Admin")
def api_usuarios_actualizar(id_usuario):
    try:
        usuario_service.actualizar_usuario(session["id_tienda"], session["id_usuario"], id_usuario, _json())
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "msg": "Usuario actualizado."})


@restaurante.delete("/api/usuarios/<int:id_usuario>")
@login_required
@roles_required("Admin")
def api_usuarios_desactivar(id_usuario):
    try:
        usuario_service.desactivar_usuario(session["id_tienda"], session["id_usuario"], id_usuario)
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "msg": "Usuario desactivado."})
