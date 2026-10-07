from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, session, url_for

from app.services import plan_service
from app.utils.decorators import login_required

core = Blueprint("core", __name__)


@core.get("/inicio")
@login_required
def inicio():
    if g.es_master:
        return redirect(url_for("master.panel"))
    plan = plan_service.plan_de(g.plan_id)
    funciones = [
        (texto, plan_service.tiene_funcion(g.plan_id, clave))
        for clave, texto in plan_service.NOMBRE_FUNCION.items()
    ]
    return render_template(
        "inicio.html",
        plan=plan,
        funciones=funciones,
        nombre_sede=session.get("nombre_sede"),
    )
