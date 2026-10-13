"""Reportes y contabilidad (rol Admin, todos los planes).

/reportes tiene cinco pestanas: Resumen (estado de resultados), Ventas
(por producto y por mesero), Gastos (lista, registrar y anular), Cierres
de caja y Calificaciones (lo que califican los clientes). Filtros por URL: periodo, desde/hasta (rango) y sede ('todas' o un
id). Un Admin atado a una sede solo ve esa sede.
"""
from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, g, jsonify, render_template, request, session

from app.services import calificaciones_service, gastos_service, reportes_service
from app.services.errores import ErrorServicio
from app.utils.decorators import login_required, roles_required

reportes = Blueprint("reportes", __name__)

PESTANAS = (("resumen", "Resumen"), ("ventas", "Ventas"), ("gastos", "Gastos"), ("cierres", "Cierres de caja"),
            ("calificaciones", "Calificaciones"))
_ERRORES = (ValueError, ErrorServicio)


def _alcance(id_tienda: int) -> tuple[list[dict], list[int], str]:
    """(sedes que puede elegir, ids a consultar, valor elegido)."""
    sedes = reportes_service.sedes_de(id_tienda)
    if g.sede_fija is not None:
        sedes = [s for s in sedes if s["id_sede"] == g.sede_fija]
        return sedes, [g.sede_fija], str(g.sede_fija)
    elegida = request.args.get("sede", "todas")
    for s in sedes:
        if str(s["id_sede"]) == elegida:
            return sedes, [s["id_sede"]], elegida
    return sedes, [s["id_sede"] for s in sedes] or [0], "todas"


def _filtros(id_tienda: int) -> dict:
    sedes, ids, sede = _alcance(id_tienda)
    per = reportes_service.periodo(request.args.get("periodo"), request.args.get("desde"), request.args.get("hasta"))
    return {"sedes": sedes, "ids": ids, "sede": sede, "periodo": per}


def _sedes_activas(sedes: list[dict]) -> list[dict]:
    return [s for s in sedes if s["estado"] == "Activa"]


@reportes.get("/reportes")
@login_required
@roles_required("Admin")
def reportes_page():
    id_tienda = session["id_tienda"]
    pestana = request.args.get("pestana", "resumen")
    if pestana not in dict(PESTANAS):
        pestana = "resumen"
    f = _filtros(id_tienda)
    datos = {}
    if pestana == "resumen":
        datos["r"] = reportes_service.resumen(id_tienda, f["ids"], f["periodo"])
    elif pestana == "ventas":
        datos["productos"] = reportes_service.productos(id_tienda, f["ids"], f["periodo"])
        datos["meseros"] = reportes_service.meseros(id_tienda, f["ids"], f["periodo"])
    elif pestana == "gastos":
        categoria = request.args.get("categoria") or ""
        datos.update(reportes_service.gastos(id_tienda, f["ids"], f["periodo"], categoria, request.args.get("pagina")))
        datos["categoria"] = categoria
        datos["categorias"] = gastos_service.CATEGORIAS
        datos["sedes_activas"] = _sedes_activas(f["sedes"])
    elif pestana == "calificaciones":
        filtro = request.args.get("ver") if request.args.get("ver") in ("bajas", "comentarios") else ""
        datos["c"] = calificaciones_service.resumen(id_tienda, f["ids"], f["periodo"])
        datos.update(calificaciones_service.lista(id_tienda, f["ids"], f["periodo"], request.args.get("pagina"),
                                                  filtro_estrellas=filtro))
        datos["ver"] = filtro
        datos["pedir_al_cobrar"] = calificaciones_service.pedir_al_cobrar(id_tienda)
    else:
        datos.update(reportes_service.cierres(id_tienda, f["ids"], f["periodo"]))
    # Los enlaces de pestanas y paginas conservan los filtros.
    filtros_url = {"periodo": f["periodo"]["clave"], "sede": f["sede"]}
    if f["periodo"]["clave"] == "rango":
        filtros_url.update(desde=f["periodo"]["desde"], hasta=f["periodo"]["hasta"])
    return render_template(
        "reportes/reportes.html",
        pestana=pestana,
        pestanas=PESTANAS,
        periodos=reportes_service.PERIODOS,
        filtros_url=filtros_url,
        sede_actual_id=g.id_sede,
        **f,
        **datos,
    )


def _csv(nombre: str, encabezado: list[str], filas: list[list]) -> Response:
    salida = io.StringIO()
    escritor = csv.writer(salida, delimiter=";")
    escritor.writerow(encabezado)
    for fila in filas:
        # Un texto que empieza con =, +, - o @ se abre como formula en Excel.
        escritor.writerow([f"'{c}" if isinstance(c, str) and c[:1] in "=+-@" else c for c in fila])
    return Response(
        "﻿" + salida.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}.csv"'},
    )


@reportes.get("/reportes/exportar/<tipo>")
@login_required
@roles_required("Admin")
def exportar(tipo):
    id_tienda = session["id_tienda"]
    f = _filtros(id_tienda)
    per = f["periodo"]
    sufijo = f"{per['desde']}_{per['hasta']}"
    if tipo == "productos":
        filas = reportes_service.productos(id_tienda, f["ids"], per)
        return _csv(f"ventas-por-producto_{sufijo}",
                    ["Producto", "Categoria", "Cantidad", "Ventas", "Costo", "Utilidad", "Margen %"],
                    [[p["nombre"], p["categoria"], p["cantidad"], round(p["ventas"]), round(p["costo"]),
                      round(p["utilidad"]), p["margen"] if p["margen"] is not None else ""] for p in filas])
    if tipo == "gastos":
        datos = reportes_service.gastos(id_tienda, f["ids"], per, request.args.get("categoria"), 1, por_pagina=5000)
        return _csv(f"gastos_{sufijo}",
                    ["Fecha", "Sede", "Concepto", "Categoria", "Metodo", "Origen", "Monto", "Registro", "Estado", "Nota"],
                    [[x["fecha"], x["sede"], x["concepto"], x["categoria"], x["metodo"], x["origen"], round(x["monto"]),
                      x["usuario"], "Activo" if x["activo"] else "Anulado",
                      x["motivo_anulacion"] or x["descripcion"]] for x in datos["gastos"]])
    if tipo == "cierres":
        datos = reportes_service.cierres(id_tienda, f["ids"], per)
        return _csv(f"cierres-de-caja_{sufijo}",
                    ["Sede", "Apertura", "Cierre", "Abrio", "Cerro", "Base", "Ventas", "Propinas", "Gastos",
                     "Debia haber", "Contado", "Diferencia", "Observaciones"],
                    [[t["sede"], t["apertura"], t["cierre"], t["abrio"], t["cerro"], round(t["base"]),
                      round(t["total_ventas"]), round(t["propinas"]), round(t["gastos"]), round(t["esperado"]),
                      "" if t["contado"] is None else round(t["contado"]),
                      "" if t["diferencia"] is None else round(t["diferencia"]), t["observaciones"]]
                     for t in datos["turnos"]])
    if tipo == "calificaciones":
        datos = calificaciones_service.lista(id_tienda, f["ids"], per, 1, por_pagina=5000)
        return _csv(f"calificaciones_{sufijo}",
                    ["Fecha", "Sede", "Pedido", "Mesero", "Comida", "Atencion", "Comentario", "Estado"],
                    [[x["fecha"], x["sede"], x["lugar"], x["mesero"], x["comida"], x["atencion"], x["comentario"],
                      "Activa" if x["activa"] else f"Oculta: {x['motivo_oculta']}"] for x in datos["calificaciones"]])
    return "No encontrado", 404


@reportes.post("/api/gastos")
@login_required
@roles_required("Admin")
def api_gastos_crear():
    data = request.get_json(silent=True) or {}
    sedes, ids, _ = _alcance(session["id_tienda"])
    activas = [s["id_sede"] for s in _sedes_activas(sedes)]
    try:
        id_sede = int(data.get("id_sede") or g.id_sede)
    except (TypeError, ValueError):
        id_sede = 0
    if id_sede not in activas:
        return jsonify({"ok": False, "msg": "Elige una sede válida."}), 400
    try:
        id_gasto = gastos_service.registrar(session["id_tienda"], session["id_usuario"], id_sede, data)
    except _ERRORES as exc:
        return jsonify({"ok": False, "msg": str(exc)}), getattr(exc, "status", 400)
    return jsonify({"ok": True, "id_gasto": id_gasto, "msg": "Gasto registrado."}), 201


@reportes.post("/api/gastos/<int:id_gasto>/anular")
@login_required
@roles_required("Admin")
def api_gastos_anular(id_gasto):
    permitidas = None if g.sede_fija is None else [g.sede_fija]
    try:
        r = gastos_service.anular(session["id_tienda"], session["id_usuario"], id_gasto,
                                  request.get_json(silent=True) or {}, permitidas)
    except _ERRORES as exc:
        return jsonify({"ok": False, "msg": str(exc)}), getattr(exc, "status", 400)
    msg = "Gasto anulado."
    if r["devuelto_a_caja"]:
        msg += f" Volvieron ${int(r['devuelto_a_caja']):,} a lo que debe haber en la caja.".replace(",", ".")
    return jsonify({"ok": True, "msg": msg, **r})
