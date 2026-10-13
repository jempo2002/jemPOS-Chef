"""Adicionales: salsas y extras con receta propia que se le agregan a un plato."""
import uuid

import pytest

from conftest import entrar
from test_recetas import _receta, cocina  # noqa: F401  (fixture)


@pytest.fixture
def salsa(cocina):  # noqa: F811
    """Salsa de la casa: adicional de $1.500 con 10 g de queso de receta."""
    r = cocina.admin.post("/api/carta", json={"nombre": "Salsa de la casa", "categoria": "Salsas", "precio_venta": 1500,
                                              "es_adicional": True})
    assert r.status_code == 201, r.get_json()
    id_salsa = r.get_json()["id_producto"]
    assert _receta(cocina, id_salsa, (cocina.queso, 10)).status_code == 200
    return id_salsa


def _agregar(c, *items):
    return c.admin.post(f"/api/mesas/{c.mesa}/items", json={"items": list(items)})


def _plato(id_producto, cantidad=1, adicionales=(), **extra):
    return {"id_producto": id_producto, "cantidad": cantidad, "uuid": str(uuid.uuid4()),
            "adicionales": [{"id_producto": pid, "cantidad": n} for pid, n in adicionales], **extra}


def test_adicional_se_cobra_y_descuenta_su_receta(cocina, salsa, crear):  # noqa: F811
    r = _agregar(cocina, _plato(cocina.burger, 2, [(salsa, 1)]))
    assert r.status_code == 200, r.get_json()
    datos = r.get_json()
    padre, hijo = datos["items"]
    assert hijo["id_item_padre"] == padre["id_item"] and hijo["nombre"] == "Salsa de la casa"
    assert hijo["cantidad"] == 2 and hijo["precio_unitario"] == 1500
    assert datos["total"] == 2 * 20000 + 2 * 1500

    r = cocina.admin.post(f"/api/mesas/{cocina.mesa}/comanda")
    assert r.status_code == 200, r.get_json()
    (comanda,) = r.get_json()["comandas"]  # la salsa va en la comanda de su plato
    assert [i["adicional"] for i in comanda["items"]] == [False, True]
    assert cocina.stock(cocina.queso) == 500 - 20

    pantalla = cocina.admin.get("/api/cocina/comandas?estacion=cocina").get_json()
    assert pantalla["comandas"][0]["items"][1]["adicional"] is True

    assert cocina.admin.post("/api/caja/abrir", json={"monto_inicial": 0}).status_code == 201
    r = cocina.admin.post(f"/api/mesas/{cocina.mesa}/cobrar", json={"metodo": "efectivo", "uuid": str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    venta = crear.fila(
        "SELECT dv.cantidad, dv.subtotal_linea, dv.costo_unitario_historico FROM detalle_ventas dv "
        "WHERE dv.id_producto = %s", (salsa,))
    # 10 g de queso a $16/g = $160 de costo por salsa.
    assert float(venta["cantidad"]) == 2 and float(venta["subtotal_linea"]) == 3000
    assert float(venta["costo_unitario_historico"]) == 160


def test_adicional_no_se_pide_solo_ni_un_plato_es_adicional(cocina, salsa):  # noqa: F811
    r = _agregar(cocina, _plato(salsa))
    assert r.status_code == 400 and "adicional" in r.get_json()["msg"]
    r = _agregar(cocina, _plato(cocina.burger, adicionales=[(cocina.sanduche, 1)]))
    assert r.status_code == 400 and "no es un adicional" in r.get_json()["msg"]
    # La carta los marca para que la pantalla los saque de la grilla.
    carta = {p["id_producto"]: p for p in cocina.admin.get("/api/carta").get_json()["productos"]}
    assert carta[salsa]["es_adicional"] and not carta[cocina.burger]["es_adicional"]


def test_reenviar_desde_la_cola_no_duplica_adicionales(cocina, salsa):  # noqa: F811
    plato = _plato(cocina.burger, 1, [(salsa, 2)])
    assert _agregar(cocina, plato).status_code == 200
    datos = _agregar(cocina, plato).get_json()
    assert len(datos["items"]) == 2 and datos["items"][1]["cantidad"] == 2


def test_quitar_el_plato_quita_sus_adicionales(cocina, salsa, crear):  # noqa: F811
    datos = _agregar(cocina, _plato(cocina.burger, 1, [(salsa, 1)]), _plato(cocina.sanduche)).get_json()
    burger = datos["items"][0]["id_item"]
    assert cocina.admin.post(f"/api/mesas/{cocina.mesa}/comanda").status_code == 200
    assert cocina.stock(cocina.queso) == 490
    r = cocina.admin.post(f"/api/pedido-items/{burger}/anular", json={"motivo": "Se fueron", "devolver": True})
    assert r.status_code == 200, r.get_json()
    estados = cocina.admin.get(f"/api/mesas/{cocina.mesa}/pedido").get_json()["items"]
    assert [i["estado"] for i in estados[:2]] == ["anulado", "anulado"] and estados[2]["estado"] == "enviado"
    assert estados[1]["id_item_padre"] == burger
    assert cocina.stock(cocina.queso) == 500  # la salsa devolvio su queso


def test_mesero_no_quita_un_plato_enviado_con_adicional(cocina, salsa, app, crear):  # noqa: F811
    crear.usuario("mesero@chef.co", "Mesero", cocina.id_tienda, cocina.sede)
    mesero = app.test_client()
    entrar(mesero, "mesero@chef.co")
    datos = _agregar(cocina, _plato(cocina.burger, 1, [(salsa, 1)])).get_json()
    cocina.admin.post(f"/api/mesas/{cocina.mesa}/comanda")
    r = mesero.post(f"/api/pedido-items/{datos['items'][0]['id_item']}/anular", json={"motivo": "x"})
    assert r.status_code == 403


def test_salsa_gratis_no_sale_en_alerta_de_costos(cocina):  # noqa: F811
    gratis = cocina.admin.post("/api/carta", json={"nombre": "Ají", "precio_venta": 0, "es_adicional": True}).get_json()["id_producto"]
    assert _receta(cocina, gratis, (cocina.queso, 5)).status_code == 200
    datos = _agregar(cocina, _plato(cocina.burger, 1, [(gratis, 1)])).get_json()
    assert datos["total"] == 20000 and datos["items"][1]["precio_unitario"] == 0
    lista = cocina.admin.get("/api/recetas/costos").get_json()["platos"]
    aji = next(p for p in lista if p["id_producto"] == gratis)
    assert aji["es_adicional"] and aji["alerta"] is False
