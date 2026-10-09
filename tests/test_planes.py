"""Precios, topes y funciones por plan (sin base de datos)."""
from app.services import plan_service as ps


def test_precios_del_landing():
    assert ps.PLANES["basico"]["precio"] == 49000
    assert ps.PLANES["completo"]["precio"] == 69000
    assert ps.PLANES["cadena"]["precio"] == 99000
    assert ps.COSTO_MONTAJE_SEDE == 79000


def test_sede_adicional_por_tramos():
    assert [ps.precio_sede(n) for n in range(1, 8)] == [0, 0, 45000, 45000, 45000, 35000, 35000]
    assert ps.mensualidad("completo", 2) == 69000  # 2 sedes incluidas
    assert ps.mensualidad("completo", 3) == 69000 + 45000
    assert ps.mensualidad("cadena", 6) == 99000 + 3 * 45000 + 35000
    assert ps.mensualidad("basico", 1) == 49000


def test_recetas_solo_en_completo_y_cadena():
    assert not ps.tiene_funcion("basico", "recetas")
    assert ps.tiene_funcion("completo", "recetas")
    assert ps.tiene_funcion("cadena", "recetas")


def test_multisede_desde_completo_sin_tope_y_dos_incluidas():
    assert ps.tope_sedes("basico") == 1
    assert ps.tope_sedes("completo") is None
    assert ps.tope_sedes("cadena") is None
    assert ps.SEDES_INCLUIDAS == 2
    assert not ps.sede_nueva_es_adicional("completo", 1)
    assert ps.sede_nueva_es_adicional("completo", 2)
    assert not ps.tiene_funcion("basico", "multisede")


def test_dos_admin_desde_completo():
    assert ps.tope_admins("basico") == 1
    assert ps.tope_admins("completo") == 2
    assert ps.tope_admins("cadena") == 2
    cuerpo, _ = ps.LimitePlanError("admins", "basico", 1).respuesta()
    assert "Completo" in cuerpo["accion_texto"] and "1 administrador" in cuerpo["msg"]
    cuerpo, _ = ps.LimitePlanError("admins", "cadena", 2).respuesta()
    assert "máximo" in cuerpo["msg"] and cuerpo["accion_texto"] == "Escribirnos por WhatsApp"


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
    assert "2 sedes" in cuerpo["msg"] and "más" in cuerpo["msg"]
