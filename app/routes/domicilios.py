"""Domicilios de la sede activa: despachos, domiciliarios y recaudo en Caja.

Quien hace que:
  Mesero, Cajero, Admin  pantalla de domicilios, tomar, despachar, entregado
  Cajero, Admin          recibir el recaudo, cambiar el metodo de pago,
                         regresar sin entregar, crear/editar domiciliarios
  Admin                  tope de efectivo por domiciliario

Los platos, la cocina y el cobro anticipado usan las rutas de para llevar
(/api/llevar/<uuid>), que ya sirven para un domicilio.
"""
from __future__ import annotations

from flask import Blueprint, g, redirect, render_template, session, url_for

from app.routes.salon import CAJA, SALON, _ERRORES, _ctx, _error, _json, _ok
from app.services import domicilios_service, plan_service
from app.services.pedidos_service import llevar
from app.utils.decorators import login_required, roles_required

domicilios = Blueprint("domicilios", __name__)


@domicilios.get("/domicilios")
@login_required
@roles_required(*SALON)
def domicilios_page():
    return render_template("salon/domicilios.html", puede_cobrar=session.get("rol") in CAJA)


@domicilios.get("/domicilio/<uuid>")
@login_required
@roles_required(*SALON)
def domicilio_page(uuid):
    """Pantalla del pedido para un domicilio nuevo o existente (mismo pedido.html)."""
    try:
        lugar = llevar(uuid)
    except _ERRORES:
        return redirect(url_for("domicilios.domicilios_page"))
    return render_template(
        "salon/pedido.html",
        llevar=lugar.uuid,
        domicilio=True,
        id_mesa=None,
        puede_cobrar=session.get("rol") in CAJA,
        es_admin=session.get("rol") == "Admin",
        dividir=plan_service.tiene_funcion(g.plan_id, "cuenta_dividida"),
    )


@domicilios.get("/api/domicilios")
@login_required
@roles_required(*SALON)
def api_domicilios():
    return _ok(**domicilios_service.listar(g.id_sede))


@domicilios.post("/api/domicilios/<int:id_pedido>/despachar")
@login_required
@roles_required(*SALON)
def api_despachar(id_pedido):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        ticket = domicilios_service.despachar(id_tienda, id_sede, id_usuario, id_pedido, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=f"Despachado con {ticket['domiciliario']}.", ticket=ticket)


@domicilios.post("/api/domicilios/<int:id_pedido>/entregado")
@login_required
@roles_required(*SALON)
def api_entregado(id_pedido):
    try:
        domicilios_service.marcar_entregado(g.id_sede, id_pedido)
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Entregado al cliente.")


@domicilios.post("/api/domicilios/<int:id_pedido>/regresar")
@login_required
@roles_required(*CAJA)
def api_regresar(id_pedido):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        domicilios_service.regresar(id_tienda, id_sede, id_usuario, id_pedido, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="El domicilio quedó por despachar otra vez.")


@domicilios.post("/api/domicilios/<int:id_pedido>/metodo")
@login_required
@roles_required(*CAJA)
def api_metodo(id_pedido):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        datos = domicilios_service.cambiar_metodo(id_tienda, id_sede, id_usuario, id_pedido, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=f"Ahora paga con {datos['metodo_pago']}.", **datos)


@domicilios.get("/api/domicilios/recaudo")
@login_required
@roles_required(*CAJA)
def api_recaudo():
    return _ok(**domicilios_service.recaudo(session["id_tienda"], g.id_sede))


@domicilios.post("/api/domicilios/recaudo")
@login_required
@roles_required(*CAJA)
def api_liquidar():
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        r = domicilios_service.liquidar(id_tienda, id_sede, id_usuario, _json())
    except _ERRORES as exc:
        return _error(exc)
    efectivo = f"${int(r['efectivo']):,}".replace(",", ".")
    return _ok(msg=f"Recaudo de {r['domiciliario']} recibido: {efectivo} en efectivo.", recaudo=r)


@domicilios.put("/api/domicilios/tope")
@login_required
@roles_required("Admin")
def api_tope():
    try:
        tope = domicilios_service.cambiar_tope(session["id_tienda"], _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(tope=tope, msg="Tope actualizado.")


# --- domiciliarios -----------------------------------------------------------

@domicilios.post("/api/domiciliarios")
@login_required
@roles_required(*CAJA)
def api_domiciliario_crear():
    id_tienda, id_sede, _ = _ctx()
    try:
        id_domiciliario = domicilios_service.crear_domiciliario(id_tienda, id_sede, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(201, id_domiciliario=id_domiciliario, msg="Domiciliario agregado.")


@domicilios.put("/api/domiciliarios/<int:id_domiciliario>")
@login_required
@roles_required(*CAJA)
def api_domiciliario_actualizar(id_domiciliario):
    try:
        domicilios_service.actualizar_domiciliario(g.id_sede, id_domiciliario, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Domiciliario actualizado.")


@domicilios.delete("/api/domiciliarios/<int:id_domiciliario>")
@login_required
@roles_required(*CAJA)
def api_domiciliario_eliminar(id_domiciliario):
    try:
        domicilios_service.eliminar_domiciliario(g.id_sede, id_domiciliario)
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Domiciliario eliminado.")
