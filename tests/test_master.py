"""Panel Master: alta de restaurantes, cambio de plan y renovacion."""
from datetime import date

from conftest import entrar

NUEVO = {
    "nombre_negocio": "Asadero Yotoco", "plan_id": "completo", "sede_nombre": "Centro",
    "admin_nombre": "Ana Dueña", "admin_cc": "1112223", "admin_correo": "ana@chef.co",
    "admin_password": "Clave123",
}


def _master(client, crear):
    crear.usuario("master@chef.co", "Master")
    assert entrar(client, "master@chef.co").location.endswith("/panel-master")


def test_crear_restaurante_con_sede_y_admin(client, crear):
    _master(client, crear)
    r = client.post("/api/master/restaurantes", json=NUEVO)
    assert r.status_code == 201, r.get_json()
    id_tienda = r.get_json()["id_tienda"]
    t = crear.fila("SELECT plan_id, trial_ends_at, es_restaurante FROM tiendas WHERE id_tienda = %s", (id_tienda,))
    assert t["plan_id"] == "completo" and t["es_restaurante"] == 1 and t["trial_ends_at"] > date.today()
    s = crear.fila("SELECT nombre, es_principal FROM sedes WHERE id_tienda = %s", (id_tienda,))
    assert s == {"nombre": "Centro", "es_principal": 1}
    assert client.post("/api/master/restaurantes", json=NUEVO).status_code == 409
    assert "Asadero Yotoco" in client.get("/panel-master").get_data(as_text=True)

    nuevo = client.application.test_client()
    assert entrar(nuevo, "ana@chef.co").location.endswith("/inicio")


def test_bajar_de_cadena_con_varias_sedes_se_bloquea(client, crear):
    _master(client, crear)
    id_tienda, _ = crear.tienda("cadena", sedes=("Centro", "Norte"))
    r = client.put(f"/api/master/restaurantes/{id_tienda}/plan", json={"plan_id": "completo"})
    assert r.status_code == 400 and "sedes" in r.get_json()["msg"]
    crear.fila("UPDATE sedes SET estado = 'Eliminada' WHERE nombre = 'Norte'")
    r = client.put(f"/api/master/restaurantes/{id_tienda}/plan", json={"plan_id": "completo"})
    assert r.status_code == 200
    assert client.put(f"/api/master/restaurantes/{id_tienda}/plan", json={"plan_id": "factura"}).status_code == 400


def test_renovar_suma_desde_el_vencimiento(client, crear):
    _master(client, crear)
    id_tienda, _ = crear.tienda()
    crear.fila("UPDATE tiendas SET trial_ends_at = DATE_ADD(CURDATE(), INTERVAL 10 DAY) WHERE id_tienda = %s", (id_tienda,))
    assert client.post(f"/api/master/restaurantes/{id_tienda}/renovar", json={"meses": 1}).status_code == 200
    t = crear.fila("SELECT DATEDIFF(fecha_fin_suscripcion, CURDATE()) AS d FROM tiendas WHERE id_tienda = %s", (id_tienda,))
    assert t["d"] >= 10 + 28
    assert client.post(f"/api/master/restaurantes/{id_tienda}/renovar", json={"meses": 2}).status_code == 400


def test_eliminar_restaurante_saca_a_sus_usuarios(client, crear):
    _master(client, crear)
    id_tienda, (sede,) = crear.tienda()
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    otro = client.application.test_client()
    entrar(otro, "admin@chef.co")
    assert client.delete(f"/api/master/restaurantes/{id_tienda}").status_code == 200
    assert otro.get("/inicio").location.endswith("/login")
    assert crear.fila("SELECT estado FROM sedes WHERE id_sede = %s", (sede,))["estado"] == "Eliminada"
