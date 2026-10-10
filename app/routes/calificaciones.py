"""Calificacion del servicio.

  /calificar/<token>                     publica: el cliente califica (sin cuenta)
  POST /api/pedidos/<id>/calificacion    Mesero, Cajero, Admin: enlace y QR del pedido cobrado
  POST /api/calificaciones/<id>/ocultar  Admin: deja de contar (borrado suave)
  POST /api/calificaciones/ajustes       Admin: mostrar o no el QR al cobrar

La pagina publica no usa sesion ni CSRF: el token del enlace es lo que
autoriza, y quien lo tiene ya puede calificar. Sin sesion tampoco se crea una
sesion en Redis por cada cliente que abre el enlace.
"""
from __future__ import annotations

from urllib.parse import quote

import segno
from flask import Blueprint, g, jsonify, render_template, request, session, url_for

from app import csrf, limiter
from app.services import calificaciones_service
from app.services.errores import ErrorServicio
from app.utils.decorators import login_required, roles_required

calificaciones = Blueprint("calificaciones", __name__)

_ERRORES = (ValueError, ErrorServicio)


def _error(exc: Exception):
    return jsonify({"ok": False, "msg": str(exc)}), getattr(exc, "status", 400)


# --- cliente ---------------------------------------------------------------------

@calificaciones.route("/calificar/<token>", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("30 per minute", methods=["GET"])
@limiter.limit("10 per minute; 40 per hour", methods=["POST"])
def calificar_page(token):
    error = None
    enviado = {}
    if request.method == "POST":
        enviado = request.form
        try:
            calificaciones_service.calificar(token, request.form)
        except _ERRORES as exc:
            error = str(exc)
    info = calificaciones_service.por_token(token)
    respuesta = render_template(
        "calificar/calificar.html",
        info=info,
        error=error,
        enviado=enviado,
        estrellas=calificaciones_service.ESTRELLAS,
        maximo=calificaciones_service.COMENTARIO_MAX,
    )
    return respuesta, 404 if info is None else 200


# --- equipo ------------------------------------------------------------------------

@calificaciones.post("/api/pedidos/<int:id_pedido>/calificacion")
@login_required
@roles_required("Admin", "Cajero", "Mesero")
def api_enlace(id_pedido):
    try:
        datos = calificaciones_service.enlace(session["id_tienda"], g.id_sede, id_pedido)
    except _ERRORES as exc:
        return _error(exc)
    url = url_for("calificaciones.calificar_page", token=datos["token"], _external=True)
    qr = segno.make(url, error="m").svg_data_uri(scale=6, border=2, dark="#120f0d")
    whatsapp = ""
    telefono = "".join(c for c in datos["telefono"] if c.isdigit())
    if len(telefono) == 10:  # celular colombiano sin indicativo
        telefono = "57" + telefono
    if len(telefono) >= 11:
        texto = f"¡Gracias por tu pedido! ¿Cómo te fue? Califica la comida y la atención aquí: {url}"
        whatsapp = f"https://wa.me/{telefono}?text={quote(texto)}"
    return jsonify({
        "ok": True,
        "url": url,
        "qr": qr,
        "lugar": datos["lugar"],
        "calificada": datos["calificada"],
        "whatsapp": whatsapp,
    })


@calificaciones.post("/api/calificaciones/<int:id_calificacion>/ocultar")
@login_required
@roles_required("Admin")
def api_ocultar(id_calificacion):
    permitidas = None if g.sede_fija is None else [g.sede_fija]
    try:
        calificaciones_service.ocultar(session["id_tienda"], session["id_usuario"], id_calificacion,
                                       request.get_json(silent=True) or {}, permitidas)
    except _ERRORES as exc:
        return _error(exc)
    return jsonify({"ok": True, "msg": "Calificación oculta. Ya no cuenta en los promedios."})


@calificaciones.post("/api/calificaciones/ajustes")
@login_required
@roles_required("Admin")
def api_ajustes():
    data = request.get_json(silent=True) or {}
    activo = str(data.get("pedir_al_cobrar") or "").lower() in ("1", "true", "on", "si")
    calificaciones_service.cambiar_pedir_al_cobrar(session["id_tienda"], activo)
    msg = "Listo: al cobrar se mostrará el QR para calificar." if activo else "Listo: ya no se mostrará el QR al cobrar."
    return jsonify({"ok": True, "msg": msg})
