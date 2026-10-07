"""Login, sesion por sede, roles y Redis."""
import os

import pytest

from conftest import CLAVE, entrar


def test_login_correcto_y_contrasena_incorrecta(client, crear):
    id_tienda, (sede,) = crear.tienda()
    crear.usuario("admin@chef.co", "Admin", id_tienda)

    r = entrar(client, "admin@chef.co", "Mala1234")
    assert r.status_code == 302 and r.location.endswith("/login")

    r = entrar(client, "admin@chef.co")
    assert r.location.endswith("/inicio")
    with client.session_transaction() as s:
        assert s["id_sede"] == sede  # una sola sede: se elige sola
    assert client.get("/inicio").status_code == 200


def test_admin_con_varias_sedes_elige_sede(client, crear):
    id_tienda, (centro, norte) = crear.tienda("cadena", sedes=("Centro", "Norte"))
    crear.usuario("admin@chef.co", "Admin", id_tienda)

    r = entrar(client, "admin@chef.co")
    assert r.location.endswith("/seleccionar-sede")
    assert client.get("/inicio").location.endswith("/seleccionar-sede")
    assert client.get("/sedes", headers={"Accept": "application/json"}).status_code == 409

    r = client.post("/seleccionar-sede", data={"id_sede": norte})
    assert r.location.endswith("/inicio")
    assert b"Norte" in client.get("/inicio").data


def test_no_se_puede_elegir_sede_de_otro_restaurante(client, crear):
    id_tienda, _ = crear.tienda("cadena", sedes=("Centro", "Norte"))
    _, (ajena,) = crear.tienda(nombre="Otro")
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    entrar(client, "admin@chef.co")
    r = client.post("/seleccionar-sede", json={"id_sede": ajena})
    assert r.status_code == 404


def test_mesero_entra_a_su_sede_y_pierde_sesion_si_lo_mueven(client, crear, db):
    id_tienda, (centro, norte) = crear.tienda("cadena", sedes=("Centro", "Norte"))
    id_mesero = crear.usuario("mesero@chef.co", "Mesero", id_tienda, norte)

    assert entrar(client, "mesero@chef.co").location.endswith("/inicio")
    with client.session_transaction() as s:
        assert s["id_sede"] == norte
    # Su sede fija no se puede cambiar por la otra.
    assert client.post("/seleccionar-sede", json={"id_sede": centro}).status_code == 404

    entrar(client, "mesero@chef.co")
    db.cursor().execute("UPDATE usuarios SET id_sede = %s WHERE id_usuario = %s", (centro, id_mesero))
    r = client.get("/inicio")
    assert r.status_code == 302 and r.location.endswith("/login")


def test_sede_eliminada_bloquea_el_login_de_sus_usuarios(client, crear, db):
    id_tienda, (centro, norte) = crear.tienda("cadena", sedes=("Centro", "Norte"))
    crear.usuario("cocina@chef.co", "Cocina", id_tienda, norte)
    db.cursor().execute("UPDATE sedes SET estado = 'Eliminada' WHERE id_sede = %s", (norte,))
    r = entrar(client, "cocina@chef.co")
    assert r.location.endswith("/login")
    with client.session_transaction() as s:
        assert "id_usuario" not in s


def test_roles_no_admin_no_ven_sedes_ni_equipo(client, crear):
    id_tienda, (sede,) = crear.tienda()
    crear.usuario("cajero@chef.co", "Cajero", id_tienda, sede)
    entrar(client, "cajero@chef.co")
    assert client.get("/sedes").status_code == 302
    assert client.post("/api/sedes", json={"nombre": "X"}).status_code == 403
    assert client.get("/panel-master").status_code == 302


def test_logout_es_post_con_csrf(app, client, crear):
    id_tienda, _ = crear.tienda()
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    entrar(client, "admin@chef.co")
    assert client.get("/logout").status_code == 405
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/logout").status_code == 400  # sin token
    app.config["WTF_CSRF_ENABLED"] = False
    client.post("/logout")
    assert client.get("/inicio").location.endswith("/login")


def test_limite_de_intentos_de_login(client, crear):
    crear.usuario("admin@chef.co", "Admin")
    codigos = [entrar(client, "admin@chef.co", "Mala1234").status_code for _ in range(6)]
    assert codigos[:5] == [302] * 5
    assert codigos[5] == 302  # formulario HTML: vuelve con aviso
    assert "Demasiados intentos" in client.get("/login").get_data(as_text=True)


def test_suscripcion_vencida_queda_en_solo_lectura(client, crear, db):
    id_tienda, _ = crear.tienda("cadena")
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    db.cursor().execute("UPDATE tiendas SET trial_ends_at = CURDATE() WHERE id_tienda = %s", (id_tienda,))
    entrar(client, "admin@chef.co")
    assert client.get("/sedes").status_code == 200
    r = client.post("/api/sedes", json={"nombre": "Norte"})
    assert r.status_code == 402 and r.get_json()["code"] == "suscripcion_vencida"


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="sin TEST_REDIS_URL")
def test_sesion_y_limites_en_redis(client, crear):
    import redis

    r = redis.from_url(os.environ["TEST_REDIS_URL"])
    r.flushdb()
    id_tienda, _ = crear.tienda()
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    entrar(client, "admin@chef.co", CLAVE)
    claves = [k.decode() for k in r.keys("*")]
    assert any(k.startswith("chef:sesion:") for k in claves), claves
    assert any("LIMITER" in k or "limiter" in k.lower() for k in claves), claves
