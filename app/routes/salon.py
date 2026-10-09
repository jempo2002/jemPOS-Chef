"""Salon: carta, mesas, pedidos, cocina y caja de la sede activa.

Todo trabaja sobre la sede de la sesion (g.id_sede): un mesero solo ve y
toca las mesas de su sede, y un Admin las de la sede que eligio al entrar.

Quien hace que:
  Mesero, Cajero, Admin  plano, pedido, para llevar, enviar a cocina,
                         precuenta, mover
  Cajero, Admin          cobrar, cuenta dividida (plan con "cuenta_dividida") y caja
  Cocina, Admin          pantalla de cocina (plan con "cocina")
  Admin                  carta, inventario, mesas y zonas
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for

from app.services import (
    caja_service,
    carta_service,
    cocina_service,
    gastos_service,
    inventario_service,
    mesas_service,
    pedidos_service,
    plan_service,
)
from app.services.errores import ErrorServicio
from app.services.pedidos_service import Mesa
from app.services.plan_service import requiere_funcion
from app.utils.decorators import login_required, roles_required

salon = Blueprint("salon", __name__)

SALON = ("Admin", "Cajero", "Mesero")
CAJA = ("Admin", "Cajero")
COCINA = ("Admin", "Cocina")


def _json() -> dict:
    return request.get_json(silent=True) or {}


def _ok(status: int = 200, **datos):
    return jsonify({"ok": True, **datos}), status


def _error(exc: Exception):
    status = getattr(exc, "status", 400)
    return jsonify({"ok": False, "msg": str(exc)}), status


_ERRORES = (ValueError, ErrorServicio)


def _ctx() -> tuple[int, int, int]:
    return session["id_tienda"], g.id_sede, session["id_usuario"]


# --- pantallas ---------------------------------------------------------------

@salon.get("/mesas")
@login_required
@roles_required(*SALON)
def mesas_page():
    return render_template(
        "salon/mesas.html",
        puede_cobrar=session.get("rol") in CAJA,
        cocina=plan_service.tiene_funcion(g.plan_id, "cocina"),
    )


@salon.get("/mesas/<int:id_mesa>")
@login_required
@roles_required(*SALON)
def pedido_page(id_mesa):
    return render_template(
        "salon/pedido.html",
        id_mesa=id_mesa,
        llevar=None,
        puede_cobrar=session.get("rol") in CAJA,
        es_admin=session.get("rol") == "Admin",
        dividir=plan_service.tiene_funcion(g.plan_id, "cuenta_dividida"),
    )


@salon.get("/mesas/configurar")
@login_required
@roles_required("Admin")
def configurar_mesas_page():
    return render_template("salon/configurar.html", **mesas_service.plano(g.id_sede))


@salon.get("/cocina")
@login_required
@roles_required(*COCINA)
@requiere_funcion("cocina")
def cocina_page():
    estacion = request.args.get("estacion", "cocina")
    if estacion not in cocina_service.ESTACIONES:
        estacion = "cocina"
    return render_template("salon/cocina.html", estacion=estacion)


@salon.get("/caja")
@login_required
@roles_required(*CAJA)
def caja_page():
    return render_template("salon/caja.html", turno=caja_service.estado(g.id_sede),
                           categorias=gastos_service.CATEGORIAS)


@salon.get("/carta")
@login_required
@roles_required("Admin")
def carta_page():
    return render_template(
        "salon/carta.html",
        productos=carta_service.listar(session["id_tienda"], g.id_sede),
        estaciones=carta_service.ESTACIONES,
    )


# --- carta e inventario ------------------------------------------------------

@salon.get("/api/carta")
@login_required
@roles_required(*SALON)
def api_carta():
    return _ok(productos=carta_service.listar(session["id_tienda"], g.id_sede))


@salon.post("/api/carta")
@login_required
@roles_required("Admin")
def api_carta_crear():
    try:
        id_producto = carta_service.crear(session["id_tienda"], _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(201, id_producto=id_producto, msg="Producto creado.")


@salon.put("/api/carta/<int:id_producto>")
@login_required
@roles_required("Admin")
def api_carta_actualizar(id_producto):
    try:
        carta_service.actualizar(session["id_tienda"], id_producto, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Producto actualizado.")


@salon.delete("/api/carta/<int:id_producto>")
@login_required
@roles_required("Admin")
def api_carta_eliminar(id_producto):
    try:
        carta_service.eliminar(session["id_tienda"], id_producto)
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Producto eliminado.")


@salon.post("/api/carta/<int:id_producto>/inventario")
@login_required
@roles_required("Admin")
def api_inventario_movimiento(id_producto):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        stock = inventario_service.registrar_movimiento(id_tienda, id_sede, id_usuario, id_producto, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(stock=float(stock), msg=f"Inventario actualizado: quedan {stock:g}.")


# --- zonas y mesas -----------------------------------------------------------

@salon.post("/api/zonas")
@login_required
@roles_required("Admin")
def api_zonas_crear():
    id_tienda, id_sede, _ = _ctx()
    try:
        id_zona = mesas_service.crear_zona(id_tienda, id_sede, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(201, id_zona=id_zona, msg="Zona creada.")


@salon.put("/api/zonas/<int:id_zona>")
@login_required
@roles_required("Admin")
def api_zonas_actualizar(id_zona):
    try:
        mesas_service.actualizar_zona(g.id_sede, id_zona, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Zona actualizada.")


@salon.delete("/api/zonas/<int:id_zona>")
@login_required
@roles_required("Admin")
def api_zonas_eliminar(id_zona):
    try:
        mesas_service.eliminar_zona(g.id_sede, id_zona)
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Zona eliminada.")


@salon.post("/api/mesas")
@login_required
@roles_required("Admin")
def api_mesas_crear():
    id_tienda, id_sede, _ = _ctx()
    try:
        id_mesa = mesas_service.crear_mesa(id_tienda, id_sede, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(201, id_mesa=id_mesa, msg="Mesa creada.")


@salon.put("/api/mesas/<int:id_mesa>")
@login_required
@roles_required("Admin")
def api_mesas_actualizar(id_mesa):
    try:
        mesas_service.actualizar_mesa(g.id_sede, id_mesa, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Mesa actualizada.")


@salon.delete("/api/mesas/<int:id_mesa>")
@login_required
@roles_required("Admin")
def api_mesas_eliminar(id_mesa):
    try:
        mesas_service.eliminar_mesa(g.id_sede, id_mesa)
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Mesa eliminada.")


@salon.get("/api/mesas/plano")
@login_required
@roles_required(*SALON)
def api_plano():
    return _ok(**mesas_service.plano(g.id_sede))


# --- pedido de una mesa ------------------------------------------------------

@salon.get("/api/mesas/<int:id_mesa>/pedido")
@login_required
@roles_required(*SALON)
def api_pedido(id_mesa):
    try:
        return _ok(**pedidos_service.ver(g.id_sede, Mesa(id_mesa)))
    except _ERRORES as exc:
        return _error(exc)


@salon.post("/api/mesas/<int:id_mesa>/items")
@login_required
@roles_required(*SALON)
def api_pedido_agregar(id_mesa):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        detalle = pedidos_service.agregar_items(id_tienda, id_sede, id_usuario, Mesa(id_mesa), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(**detalle)


@salon.post("/api/mesas/<int:id_mesa>/comanda")
@login_required
@roles_required(*SALON)
def api_pedido_comanda(id_mesa):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        resultado = pedidos_service.enviar_comanda(id_tienda, id_sede, id_usuario, Mesa(id_mesa))
    except _ERRORES as exc:
        return _error(exc)
    return _ok(**resultado)


@salon.post("/api/pedido-items/<int:id_item>/anular")
@login_required
@roles_required(*SALON)
def api_item_anular(id_item):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        pedidos_service.anular_item(id_tienda, id_sede, id_usuario, session.get("rol"), id_item, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Producto anulado.")


@salon.post("/api/mesas/<int:id_mesa>/precuenta")
@login_required
@roles_required(*SALON)
def api_pedido_precuenta(id_mesa):
    try:
        return _ok(**pedidos_service.precuenta(g.id_sede, Mesa(id_mesa)))
    except _ERRORES as exc:
        return _error(exc)


@salon.post("/api/mesas/<int:id_mesa>/mover")
@login_required
@roles_required(*SALON)
def api_pedido_mover(id_mesa):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        msg = pedidos_service.mover(id_tienda, id_sede, id_usuario, id_mesa, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=msg)


@salon.post("/api/mesas/<int:id_mesa>/anular")
@login_required
@roles_required(*SALON)
def api_pedido_anular(id_mesa):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        pedidos_service.anular_pedido(id_tienda, id_sede, id_usuario, Mesa(id_mesa), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Cuenta anulada. La mesa quedó libre.")


@salon.post("/api/mesas/<int:id_mesa>/cobrar")
@login_required
@roles_required(*CAJA)
def api_pedido_cobrar(id_mesa):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        venta = pedidos_service.cobrar(id_tienda, id_sede, id_usuario, Mesa(id_mesa), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=f"Cobrado. Venta {venta['numero_venta']}.", venta=venta)


@salon.post("/api/mesas/<int:id_mesa>/cobrar-dividido")
@login_required
@roles_required(*CAJA)
@requiere_funcion("cuenta_dividida")
def api_pedido_cobrar_dividido(id_mesa):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        cobro = pedidos_service.cobrar_dividido(id_tienda, id_sede, id_usuario, Mesa(id_mesa), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=_msg_dividido(cobro), cobro=cobro)


def _msg_dividido(cobro: dict) -> str:
    ventas = cobro["ventas"]
    return f"Cobrado en {len(cobro['partes'])} partes. Venta{'s' if len(ventas) > 1 else ''} {', '.join(ventas)}."


# --- para llevar -------------------------------------------------------------
# Mismas operaciones que una mesa, por el uuid que el dispositivo le pone al
# pedido (se puede abrir sin conexion). El pedido se crea con el primer plato.

def _llevar(uuid: str):
    return pedidos_service.llevar(uuid)


@salon.get("/llevar/<uuid>")
@login_required
@roles_required(*SALON)
def llevar_page(uuid):
    try:
        lugar = _llevar(uuid)
    except _ERRORES:
        return redirect(url_for("salon.mesas_page"))
    return render_template(
        "salon/pedido.html",
        llevar=lugar.uuid,
        id_mesa=None,
        puede_cobrar=session.get("rol") in CAJA,
        es_admin=session.get("rol") == "Admin",
        dividir=plan_service.tiene_funcion(g.plan_id, "cuenta_dividida"),
    )


@salon.get("/api/llevar/<uuid>/pedido")
@login_required
@roles_required(*SALON)
def api_llevar_pedido(uuid):
    try:
        return _ok(**pedidos_service.ver(g.id_sede, _llevar(uuid)))
    except _ERRORES as exc:
        return _error(exc)


@salon.post("/api/llevar/<uuid>/items")
@login_required
@roles_required(*SALON)
def api_llevar_agregar(uuid):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        detalle = pedidos_service.agregar_items(id_tienda, id_sede, id_usuario, _llevar(uuid), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(**detalle)


@salon.post("/api/llevar/<uuid>/comanda")
@login_required
@roles_required(*SALON)
def api_llevar_comanda(uuid):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        resultado = pedidos_service.enviar_comanda(id_tienda, id_sede, id_usuario, _llevar(uuid))
    except _ERRORES as exc:
        return _error(exc)
    return _ok(**resultado)


@salon.post("/api/llevar/<uuid>/precuenta")
@login_required
@roles_required(*SALON)
def api_llevar_precuenta(uuid):
    try:
        return _ok(**pedidos_service.precuenta(g.id_sede, _llevar(uuid)))
    except _ERRORES as exc:
        return _error(exc)


@salon.post("/api/llevar/<uuid>/anular")
@login_required
@roles_required(*SALON)
def api_llevar_anular(uuid):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        pedidos_service.anular_pedido(id_tienda, id_sede, id_usuario, _llevar(uuid), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Pedido para llevar anulado.")


@salon.post("/api/llevar/<uuid>/entregar")
@login_required
@roles_required(*SALON)
def api_llevar_entregar(uuid):
    try:
        pedidos_service.entregar_llevar(g.id_sede, _llevar(uuid))
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Entregado al cliente.")


@salon.post("/api/llevar/<uuid>/cobrar")
@login_required
@roles_required(*CAJA)
def api_llevar_cobrar(uuid):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        venta = pedidos_service.cobrar(id_tienda, id_sede, id_usuario, _llevar(uuid), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=f"Cobrado. Venta {venta['numero_venta']}.", venta=venta)


@salon.post("/api/llevar/<uuid>/cobrar-dividido")
@login_required
@roles_required(*CAJA)
@requiere_funcion("cuenta_dividida")
def api_llevar_cobrar_dividido(uuid):
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        cobro = pedidos_service.cobrar_dividido(id_tienda, id_sede, id_usuario, _llevar(uuid), _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=_msg_dividido(cobro), cobro=cobro)


# --- cocina ------------------------------------------------------------------

@salon.get("/api/cocina/comandas")
@login_required
@roles_required(*COCINA)
@requiere_funcion("cocina")
def api_cocina_comandas():
    try:
        datos = cocina_service.comandas(g.id_sede, request.args.get("estacion", "cocina"), request.args.get("desde"))
    except _ERRORES as exc:
        return _error(exc)
    return _ok(**datos)


@salon.post("/api/cocina/comandas/<int:id_comanda>/estado")
@login_required
@roles_required(*COCINA)
@requiere_funcion("cocina")
def api_cocina_avanzar(id_comanda):
    try:
        estado = cocina_service.avanzar(g.id_sede, id_comanda, str(_json().get("estado") or ""))
    except _ERRORES as exc:
        return _error(exc)
    return _ok(estado=estado)


# --- caja --------------------------------------------------------------------

@salon.get("/api/caja")
@login_required
@roles_required(*CAJA)
def api_caja_estado():
    return _ok(turno=caja_service.estado(g.id_sede))


@salon.post("/api/caja/abrir")
@login_required
@roles_required(*CAJA)
def api_caja_abrir():
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        caja_service.abrir(id_tienda, id_sede, id_usuario, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(201, msg="Caja abierta.")


@salon.post("/api/caja/gastos")
@login_required
@roles_required(*CAJA)
def api_caja_gasto():
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        gasto = caja_service.registrar_gasto(id_tienda, id_sede, id_usuario, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(201, msg="Gasto registrado.", gasto=gasto)


@salon.post("/api/caja/cerrar")
@login_required
@roles_required(*CAJA)
def api_caja_cerrar():
    id_tienda, id_sede, id_usuario = _ctx()
    try:
        resumen = caja_service.cerrar(id_tienda, id_sede, id_usuario, _json())
    except _ERRORES as exc:
        return _error(exc)
    diferencia = resumen["diferencia"]
    if diferencia == 0:
        msg = "Caja cerrada. Cuadra exacto."
    else:
        msg = f"Caja cerrada. {'Sobran' if diferencia > 0 else 'Faltan'} ${abs(int(diferencia)):,}.".replace(",", ".")
    return _ok(msg=msg, resumen=resumen)
