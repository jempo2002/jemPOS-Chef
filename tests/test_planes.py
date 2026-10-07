"""Precios, topes y funciones por plan (sin base de datos)."""
from app.services import plan_service as ps


def test_precios_del_landing():
    assert ps.PLANES["basico"]["precio"] == 49000
    assert ps.PLANES["completo"]["precio"] == 69000
    assert ps.PLANES["cadena"]["precio"] == 99000
    assert ps.COSTO_MONTAJE_SEDE == 79000


def test_sede_extra_cuesta_la_mitad_del_plan():
    assert ps.PLANES["completo"]["sede_extra"] == 34500
    assert ps.PLANES["cadena"]["sede_extra"] == 49500
    assert ps.PLANES["basico"]["sede_extra"] is None
    assert ps.mensualidad("completo", 2) == 69000 + 34500
    assert ps.mensualidad("cadena", 4) == 99000 + 3 * 49500
    assert ps.mensualidad("basico", 1) == 49000


def test_recetas_solo_en_completo_y_cadena():
    assert not ps.tiene_funcion("basico", "recetas")
    assert ps.tiene_funcion("completo", "recetas")
    assert ps.tiene_funcion("cadena", "recetas")


def test_multisede_desde_completo_y_maximo_cuatro():
    assert ps.tope_sedes("basico") == 1
    assert ps.tope_sedes("completo") == 4
    assert ps.tope_sedes("cadena") == 4
    assert not ps.tiene_funcion("basico", "multisede")


def test_plan_desconocido_se_trata_como_basico():
    assert ps.normalizar_plan(None) == "basico"
    assert ps.normalizar_plan("factura") == "basico"
    assert not ps.tiene_funcion(None, "recetas")


def test_tope_de_usuarios_crece_con_las_sedes_en_cadena():
    por_sede = ps.PLANES["cadena"]["usuarios_por_sede"]
    assert ps.tope_usuarios("cadena", 3) == 3 * por_sede
    assert ps.tope_usuarios("basico", 0) == ps.PLANES["basico"]["usuarios_por_sede"]


def test_respuesta_de_limite_de_sedes():
    cuerpo, status = ps.LimitePlanError("sedes", "basico", 1).respuesta()
    assert status == 403 and cuerpo["code"] == "limite_plan"
    assert "Completo" in cuerpo["accion_texto"]
    assert cuerpo["accion_url"].startswith("https://wa.me/")
    cuerpo, _ = ps.LimitePlanError("sedes", "cadena", 4).respuesta()
    assert "corporativo" in cuerpo["msg"]
    assert cuerpo["accion_texto"] == "Escribirnos por WhatsApp"
