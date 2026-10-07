"""Sedes y usuarios segun el plan."""
from conftest import entrar


def _admin(client, crear, plan, sedes=("Principal",)):
    id_tienda, ids = crear.tienda(plan, sedes=sedes)
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    entrar(client, "admin@chef.co")
    if len(ids) > 1:
        client.post("/seleccionar-sede", data={"id_sede": ids[0]})
    return id_tienda, ids


def test_basico_no_abre_segunda_sede(client, crear):
    _admin(client, crear, "basico")
    r = client.post("/api/sedes", json={"nombre": "Norte"})
    assert r.status_code == 403
    assert r.get_json()["code"] == "limite_plan"


def test_completo_abre_hasta_cuatro_sedes_con_montaje(client, crear):
    id_tienda, _ = _admin(client, crear, "completo")
    for nombre in ("Norte", "Sur", "Oriente"):
        assert client.post("/api/sedes", json={"nombre": nombre}).status_code == 201
    r = client.post("/api/sedes", json={"nombre": "Quinta"})
    assert r.status_code == 403 and "corporativo" in r.get_json()["msg"]
    assert client.post("/api/sedes", json={"nombre": "Norte"}).status_code in (403, 409)
    pagina = client.get("/sedes").get_data(as_text=True)
    assert "$172.500" in pagina  # 69.000 + 3 x 34.500
    fila = crear.fila(
        "SELECT SUM(costo_montaje) AS total, SUM(montaje_pagado) AS pagadas FROM sedes WHERE id_tienda = %s",
        (id_tienda,),
    )
    assert int(fila["total"]) == 3 * 79000 and int(fila["pagadas"]) == 0


def test_nombre_de_sede_repetido(client, crear):
    _admin(client, crear, "cadena")
    assert client.post("/api/sedes", json={"nombre": "Norte"}).status_code == 201
    assert client.post("/api/sedes", json={"nombre": "Norte"}).status_code == 409


def test_la_base_rechaza_una_quinta_sede_y_basico_con_varias(crear, db):
    import mysql.connector
    import pytest

    id_tienda, _ = crear.tienda("cadena", sedes=("A", "B", "C", "D"))
    with pytest.raises(mysql.connector.Error) as exc:
        crear.sede(id_tienda, "E")
    assert exc.value.errno == 1644
    with pytest.raises(mysql.connector.Error):
        db.cursor().execute("UPDATE tiendas SET plan_id = 'basico' WHERE id_tienda = %s", (id_tienda,))
    basico, _ = crear.tienda("basico", nombre="Uno")
    with pytest.raises(mysql.connector.Error):
        crear.sede(basico, "Segunda")


def test_eliminar_sede_es_soft_delete(client, crear):
    id_tienda, (principal, norte) = _admin(client, crear, "cadena", ("Centro", "Norte"))
    assert client.delete(f"/api/sedes/{principal}").status_code == 400
    crear.usuario("mesero@chef.co", "Mesero", id_tienda, norte)
    assert client.delete(f"/api/sedes/{norte}").status_code == 409  # tiene usuarios
    crear.fila("UPDATE usuarios SET estado_activo = 0 WHERE correo = 'mesero@chef.co'")
    assert client.delete(f"/api/sedes/{norte}").status_code == 200
    fila = crear.fila("SELECT estado, fecha_eliminacion FROM sedes WHERE id_sede = %s", (norte,))
    assert fila["estado"] == "Eliminada" and fila["fecha_eliminacion"] is not None
    # El nombre queda libre para una sede nueva.
    assert client.post("/api/sedes", json={"nombre": "Norte"}).status_code == 201


def test_sede_de_otro_restaurante_no_se_toca(client, crear):
    _admin(client, crear, "cadena")
    _, (ajena,) = crear.tienda(nombre="Otro")
    assert client.put(f"/api/sedes/{ajena}", json={"nombre": "Mia"}).status_code == 404
    assert client.delete(f"/api/sedes/{ajena}").status_code == 404


def _nuevo(n, rol="Mesero", **extra):
    return {
        "nombre": f"Persona {n}", "cc": f"10000{n:03d}", "correo": f"p{n}@chef.co", "rol": rol,
        "password": "Clave123", "confirm_password": "Clave123", **extra,
    }


def test_tope_de_usuarios_del_basico(client, crear):
    from app.services.plan_service import PLANES

    _admin(client, crear, "basico")
    tope = PLANES["basico"]["usuarios_por_sede"]
    for n in range(tope - 1):  # el Admin ya cuenta
        assert client.post("/api/usuarios", json=_nuevo(n)).status_code == 201
    r = client.post("/api/usuarios", json=_nuevo(99))
    assert r.status_code == 403 and r.get_json()["recurso"] == "usuarios"


def test_usuario_sin_sede_en_restaurante_de_varias_sedes(client, crear):
    id_tienda, (centro, norte) = _admin(client, crear, "cadena", ("Centro", "Norte"))
    assert client.post("/api/usuarios", json=_nuevo(1)).status_code == 400
    assert client.post("/api/usuarios", json=_nuevo(1, id_sede=norte)).status_code == 201
    fila = crear.fila("SELECT id_sede, rol FROM usuarios WHERE correo = 'p1@chef.co'")
    assert fila == {"id_sede": norte, "rol": "Mesero"}


def test_desactivar_libera_correo_y_no_deja_sin_admin(client, crear):
    id_tienda, _ = _admin(client, crear, "completo")
    yo = crear.fila("SELECT id_usuario FROM usuarios WHERE correo = 'admin@chef.co'")["id_usuario"]
    assert client.delete(f"/api/usuarios/{yo}").status_code == 400
    client.post("/api/usuarios", json=_nuevo(1))
    otro = crear.fila("SELECT id_usuario FROM usuarios WHERE correo = 'p1@chef.co'")["id_usuario"]
    assert client.delete(f"/api/usuarios/{otro}").status_code == 200
    assert crear.fila("SELECT correo FROM usuarios WHERE id_usuario = %s", (otro,))["correo"].startswith("deleted_")
    assert client.post("/api/usuarios", json=_nuevo(1)).status_code == 201  # correo libre otra vez
    r = client.put(f"/api/usuarios/{yo}", json={"nombre": "Yo", "rol": "Cajero"})
    assert r.status_code == 400


def test_recetas_requiere_plan(app, crear):
    from flask import Blueprint, jsonify

    from app.services.plan_service import requiere_funcion
    from app.utils.decorators import login_required

    bp = Blueprint("prueba", __name__)

    @bp.get("/api/recetas-prueba")
    @login_required
    @requiere_funcion("recetas")
    def recetas():
        return jsonify({"ok": True})

    app.register_blueprint(bp)
    for plan, esperado in (("basico", 403), ("completo", 200), ("cadena", 200)):
        id_tienda, _ = crear.tienda(plan, nombre=plan)
        crear.usuario(f"{plan}@chef.co", "Admin", id_tienda)
        c = app.test_client()
        entrar(c, f"{plan}@chef.co")
        assert c.get("/api/recetas-prueba").status_code == esperado, plan
