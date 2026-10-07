"""Recetas, insumos y costo por plato (rol Admin, planes Completo y Cadena).

La pantalla /recetas tiene tres pestanas (Insumos, Recetas, Costos) y una
franja de alertas. En el Plan Basico la pantalla muestra el aviso para
cambiar de plan y la API responde 403 `funcion_no_incluida`.
"""
from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, g, jsonify, render_template, request, session

from app.services import plan_service, recetas_service
from app.services.errores import ErrorServicio
from app.services.plan_service import requiere_funcion
from app.utils.decorators import login_required, roles_required

recetas = Blueprint("recetas", __name__)

PESTANAS = ("insumos", "recetas", "costos")
_ERRORES = (ValueError, ErrorServicio)


def _json() -> dict:
    return request.get_json(silent=True) or {}


def _ok(status: int = 200, **datos):
    return jsonify({"ok": True, **datos}), status


def _error(exc: Exception):
    return jsonify({"ok": False, "msg": str(exc)}), getattr(exc, "status", 400)


def _upsell():
    """Aviso del Plan Basico: que gana pasandose al Completo."""
    return plan_service.LimitePlanError("usuarios", plan_service.normalizar_plan(g.plan_id), 0).respuesta()[0]


# --- pantallas ---------------------------------------------------------------

@recetas.get("/recetas")
@login_required
@roles_required("Admin")
def recetas_page():
    if not plan_service.tiene_funcion(g.plan_id, "recetas"):
        return render_template("recetas/sin_plan.html", plan=plan_service.plan_de(g.plan_id), aviso=_upsell())
    id_tienda, id_sede = session["id_tienda"], g.id_sede
    pestana = request.args.get("pestana", "insumos")
    if pestana not in PESTANAS:
        pestana = "insumos"
    insumos = recetas_service.listar_insumos(id_tienda, id_sede)
    estado = request.args.get("estado")
    if estado == "bajo":
        insumos = [i for i in insumos if i["estado"] != "ok"]
    return render_template(
        "recetas/recetas.html",
        pestana=pestana,
        alertas=recetas_service.alertas(id_tienda, id_sede),
        insumos=insumos,
        filtro_estado=estado,
        platos=recetas_service.platos(id_tienda, id_sede) if pestana == "recetas" else [],
        costos=recetas_service.costos(id_tienda, id_sede) if pestana == "costos" else None,
        unidades=recetas_service.UNIDADES_BASE,
        nombre_unidad=recetas_service.NOMBRE_UNIDAD,
    )


@recetas.get("/recetas/plato/<int:id_producto>")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def receta_page(id_producto):
    id_tienda, id_sede = session["id_tienda"], g.id_sede
    try:
        receta = recetas_service.ver_receta(id_tienda, id_sede, id_producto)
    except ErrorServicio as exc:
        return str(exc), exc.status
    return render_template(
        "recetas/receta.html",
        receta=receta,
        insumos=recetas_service.listar_insumos(id_tienda, id_sede),
    )


@recetas.get("/recetas/insumos/<int:id_insumo>")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def movimientos_page(id_insumo):
    try:
        datos = recetas_service.movimientos_insumo(session["id_tienda"], g.id_sede, id_insumo)
    except ErrorServicio as exc:
        return str(exc), exc.status
    return render_template("recetas/movimientos.html", **datos)


@recetas.get("/recetas/costos.csv")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def costos_csv():
    datos = recetas_service.costos(session["id_tienda"], g.id_sede)
    salida = io.StringIO()
    # Punto y coma: el Excel en espanol abre asi las columnas sin preguntar.
    escritor = csv.writer(salida, delimiter=";")
    escritor.writerow(["Plato", "Categoria", "Costo", "Precio", "Utilidad", "% costo", "Alerta"])
    for p in datos["platos"]:
        escritor.writerow([
            p["nombre"], p["categoria"] or "", round(p["costo"]), round(p["precio_venta"]), round(p["utilidad"]),
            "" if p["pct_costo"] is None else str(p["pct_costo"]).replace(".", ","),
            "Si" if p["alerta"] else "",
        ])
    return Response(
        "﻿" + salida.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=costos-por-plato.csv"},
    )


# --- insumos -----------------------------------------------------------------

@recetas.get("/api/insumos")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_insumos():
    return _ok(insumos=recetas_service.listar_insumos(session["id_tienda"], g.id_sede))


@recetas.post("/api/insumos")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_insumos_crear():
    try:
        id_insumo = recetas_service.crear_insumo(session["id_tienda"], _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(201, id_insumo=id_insumo, msg="Insumo creado.")


@recetas.put("/api/insumos/<int:id_insumo>")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_insumos_actualizar(id_insumo):
    try:
        recetas_service.actualizar_insumo(session["id_tienda"], id_insumo, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Insumo actualizado.")


@recetas.delete("/api/insumos/<int:id_insumo>")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_insumos_eliminar(id_insumo):
    try:
        recetas_service.eliminar_insumo(session["id_tienda"], id_insumo)
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg="Insumo eliminado.")


@recetas.post("/api/insumos/<int:id_insumo>/movimiento")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_insumos_movimiento(id_insumo):
    try:
        r = recetas_service.registrar_movimiento_insumo(
            session["id_tienda"], g.id_sede, session["id_usuario"], id_insumo, _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(msg=f"Inventario actualizado: quedan {r['stock']:g} {r['unidad']}.", **r)


@recetas.get("/api/insumos/<int:id_insumo>/movimientos")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_insumos_movimientos(id_insumo):
    try:
        datos = recetas_service.movimientos_insumo(session["id_tienda"], g.id_sede, id_insumo)
    except _ERRORES as exc:
        return _error(exc)
    for m in datos["movimientos"]:
        m["fecha_creacion"] = m["fecha_creacion"].strftime("%Y-%m-%d %H:%M")
    datos["insumo"]["costo_unitario"] = float(datos["insumo"]["costo_unitario"])
    if datos["insumo"]["stock_minimo_alerta"] is not None:
        datos["insumo"]["stock_minimo_alerta"] = float(datos["insumo"]["stock_minimo_alerta"])
    return _ok(**datos)


# --- recetas y costos --------------------------------------------------------

@recetas.get("/api/recetas/costos")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_costos():
    return _ok(**recetas_service.costos(session["id_tienda"], g.id_sede))


@recetas.put("/api/recetas/margen")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_margen():
    try:
        margen = recetas_service.guardar_margen(session["id_tienda"], _json())
    except _ERRORES as exc:
        return _error(exc)
    return _ok(margen_alerta=margen, msg=f"Alerta en {margen:g} % del precio.")


@recetas.get("/api/recetas/alertas")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_alertas():
    return _ok(**recetas_service.alertas(session["id_tienda"], g.id_sede))


@recetas.get("/api/recetas/<int:id_producto>")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_receta(id_producto):
    try:
        return _ok(**recetas_service.ver_receta(session["id_tienda"], g.id_sede, id_producto))
    except _ERRORES as exc:
        return _error(exc)


@recetas.put("/api/recetas/<int:id_producto>")
@login_required
@roles_required("Admin")
@requiere_funcion("recetas")
def api_receta_guardar(id_producto):
    try:
        r = recetas_service.guardar_receta(session["id_tienda"], id_producto, _json())
    except _ERRORES as exc:
        return _error(exc)
    msg = "Receta guardada." if r["ingredientes"] else "Receta vacía: el plato ya no descuenta ingredientes."
    if r["alerta"]:
        msg += f" Ojo: los ingredientes cuestan el {r['pct_costo']:g} % del precio."
    return _ok(msg=msg, **r)
