"""Panel Master: restaurantes, planes y suscripciones."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from app.services import master_service, plan_service
from app.utils.decorators import login_required, roles_required
from app.utils.helpers import hoy_local

master = Blueprint("master", __name__)

_ERRORES = (ValueError, master_service.MasterError)


def _error(exc: Exception):
    return jsonify({"ok": False, "msg": str(exc)}), getattr(exc, "status", 400)


@master.get("/panel-master")
@login_required
@roles_required("Master")
def panel():
    restaurantes = master_service.listar_restaurantes()
    return render_template(
        "master/panel.html",
        restaurantes=restaurantes,
        planes=plan_service.PLANES,
        periodos=master_service.PERIODOS,
        mrr=sum(r["mensualidad"] for r in restaurantes if not r["en_prueba"]),
        hoy=hoy_local(),
    )


@master.post("/api/master/restaurantes")
@login_required
@roles_required("Master")
def api_crear():
    try:
        id_tienda = master_service.crear_restaurante(request.get_json(silent=True) or {})
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "id_tienda": id_tienda, "msg": "Restaurante creado con un mes de prueba."}), 201


@master.put("/api/master/restaurantes/<int:id_tienda>/plan")
@login_required
@roles_required("Master")
def api_plan(id_tienda):
    try:
        plan_id = master_service.cambiar_plan(id_tienda, (request.get_json(silent=True) or {}).get("plan_id"))
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "msg": f"Plan cambiado a {plan_service.PLANES[plan_id]['nombre']}."})


@master.post("/api/master/restaurantes/<int:id_tienda>/renovar")
@login_required
@roles_required("Master")
def api_renovar(id_tienda):
    try:
        fin = master_service.renovar(id_tienda, (request.get_json(silent=True) or {}).get("meses"))
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "msg": f"Suscripcion activa hasta {fin.strftime('%d/%m/%Y')}."})


@master.delete("/api/master/restaurantes/<int:id_tienda>")
@login_required
@roles_required("Master")
def api_eliminar(id_tienda):
    try:
        master_service.eliminar_restaurante(id_tienda)
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "msg": "Restaurante eliminado."})
