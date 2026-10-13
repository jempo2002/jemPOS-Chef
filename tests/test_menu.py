"""Menu de la app: barra inferior del celular y hoja "Mas" segun el rol."""
import re

from conftest import entrar


def _secciones(html):
    barra = re.search(r'<nav class="tabbar".*?</nav>', html, re.S).group(0)
    hoja = re.search(r'<dialog class="hoja".*?</dialog>', html, re.S).group(0)
    return barra, hoja


def test_admin_tiene_lo_principal_abajo_y_el_resto_en_mas(client, crear):
    id_tienda, ids = crear.tienda("completo")
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    entrar(client, "admin@chef.co")
    barra, hoja = _secciones(client.get("/carta").get_data(as_text=True))
    assert re.findall(r"<span>(\w+)</span>", barra) == ["Mesas", "Domicilios", "Caja", "Reportes", "Más"]
    for nombre in ("Inicio", "Cocina", "Carta", "Recetas", "Sedes", "Equipo"):
        assert f"<span>{nombre}</span>" in hoja
    # La pagina actual esta en "Mas": se marca el boton y el enlace.
    assert 'es-activo" data-abrir-menu' in barra
    assert 'href="/carta" class="hoja__item es-activo"' in hoja


def test_mesero_solo_ve_lo_suyo(client, crear):
    id_tienda, ids = crear.tienda("completo")
    crear.usuario("mesero@chef.co", "Mesero", id_tienda, ids[0])
    entrar(client, "mesero@chef.co")
    html = client.get("/inicio").get_data(as_text=True)
    barra, hoja = _secciones(html)
    assert re.findall(r"<span>(\w+)</span>", barra) == ["Inicio", "Mesas", "Domicilios", "Más"]
    assert "Reportes" not in html and "Caja</span>" not in html
    assert "Cerrar sesión" in hoja
