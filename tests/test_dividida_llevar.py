"""Cuenta dividida (por productos y en partes iguales) y pedidos para llevar."""
import uuid

from conftest import entrar
from test_salon import _pedir, salon  # noqa: F401  (fixture)


def _mesa_lista(salon, *items):
    """Mesa con lo pedido ya enviado a cocina y caja abierta."""
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 0})
    _pedir(salon.mesero, salon.mesa, *items)
    assert salon.mesero.post(f"/api/mesas/{salon.mesa}/comanda").status_code == 200
    return salon.mesero.get(f"/api/mesas/{salon.mesa}/pedido").get_json()


def test_dividir_por_productos_crea_una_venta_por_persona(salon, crear):
    detalle = _mesa_lista(salon, (salon.burger, 2), (salon.gaseosa, 3))
    burger, gaseosa = (i["id_item"] for i in detalle["items"])
    cobro = {
        "modo": "items", "uuid": str(uuid.uuid4()), "id_pedido": detalle["pedido"]["id_pedido"],
        "partes": [
            {"etiqueta": "Ana", "metodo": "efectivo", "propina": 2000,
             "items": [{"id_item": burger, "cantidad": 1}, {"id_item": gaseosa, "cantidad": 2}]},
            {"etiqueta": "Luis", "metodo": "nequi", "propina": 0,
             "items": [{"id_item": burger, "cantidad": 1}, {"id_item": gaseosa, "cantidad": 1}]},
        ],
    }
    # Falta repartir una gaseosa: no cobra nada.
    falta = {**cobro, "partes": [cobro["partes"][0], {**cobro["partes"][1], "items": cobro["partes"][1]["items"][:1]}]}
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=falta)
    assert r.status_code == 400 and "Gaseosa" in r.get_json()["msg"]
    assert crear.fila("SELECT COUNT(*) AS n FROM ventas")["n"] == 0

    assert salon.mesero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro).status_code == 403
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro)
    assert r.status_code == 200, r.get_json()
    partes = r.get_json()["cobro"]["partes"]
    assert [(p["etiqueta"], p["monto"], p["propina"]) for p in partes] == [("Ana", 26000, 2000), ("Luis", 22000, 0)]
    assert partes[0]["numero_venta"] != partes[1]["numero_venta"]

    ventas = crear.fila("SELECT COUNT(*) AS n, SUM(total_final) AS total, SUM(propina) AS propina FROM ventas")
    assert ventas["n"] == 2 and float(ventas["total"]) == 48000 and float(ventas["propina"]) == 2000
    detalle_ana = crear.fila(
        "SELECT SUM(d.cantidad) AS n FROM detalle_ventas d JOIN ventas v ON v.id_venta = d.id_venta "
        "WHERE v.numero_venta = %s", (partes[0]["numero_venta"],))
    assert float(detalle_ana["n"]) == 3
    assert crear.fila("SELECT COUNT(*) AS n FROM pedido_cuenta_items")["n"] == 4
    assert salon.stock() == 8  # cobrar no vuelve a descontar

    caja = salon.cajero.get("/api/caja").get_json()["turno"]
    assert caja["esperado_en_caja"] == 28000  # solo lo de Ana fue en efectivo
    assert salon.mesero.get(f"/api/mesas/{salon.mesa}/pedido").get_json()["pedido"] is None

    # Reenviado desde la cola: no cobra otra vez.
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro)
    assert r.status_code == 200 and r.get_json()["cobro"]["repetido"] is True
    assert crear.fila("SELECT COUNT(*) AS n FROM ventas")["n"] == 2


def test_dividir_en_partes_iguales_una_venta_mixta(salon, crear):
    detalle = _mesa_lista(salon, (salon.burger, 1), (salon.gaseosa, 1))  # 22.000 entre 3
    cobro = {"modo": "iguales", "uuid": str(uuid.uuid4()), "partes": [
        {"metodo": "efectivo", "propina": 1000}, {"metodo": "tarjeta"}, {"metodo": "efectivo"},
    ]}
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro)
    assert r.status_code == 200, r.get_json()
    partes = r.get_json()["cobro"]["partes"]
    assert [p["monto"] for p in partes] == [7334, 7333, 7333]
    assert [p["etiqueta"] for p in partes] == ["Cuenta 1", "Cuenta 2", "Cuenta 3"]
    venta = crear.fila("SELECT metodo_pago, total_final, propina, monto_efectivo, monto_transferencia FROM ventas")
    assert venta["metodo_pago"] == "Mixto" and float(venta["total_final"]) == 22000 and float(venta["propina"]) == 1000
    assert float(venta["monto_efectivo"]) == 7334 + 1000 + 7333 and float(venta["monto_transferencia"]) == 7333
    assert crear.fila("SELECT COUNT(*) AS n FROM pedido_cuentas WHERE modo = 'iguales'")["n"] == 3
    assert crear.fila("SELECT COUNT(*) AS n FROM detalle_ventas")["n"] == 2  # los platos reales
    assert detalle["pedido"]["id_pedido"] == crear.fila("SELECT id_pedido FROM ventas")["id_pedido"]


def test_partes_iguales_mismo_metodo_no_es_mixto(salon, crear):
    _mesa_lista(salon, (salon.gaseosa, 2))
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json={
        "modo": "iguales", "partes": [{"metodo": "nequi"}, {"metodo": "transferencia"}]})
    assert r.status_code == 200
    assert crear.fila("SELECT metodo_pago FROM ventas")["metodo_pago"] == "Nequi/Daviplata"


def test_cuenta_dividida_no_viene_en_basico(app, crear):
    id_tienda, (sede,) = crear.tienda("basico", nombre="Chico")
    crear.usuario("caja@chico.co", "Cajero", id_tienda, sede)
    c = app.test_client()
    entrar(c, "caja@chico.co")
    r = c.post("/api/mesas/1/cobrar-dividido", json={})
    assert r.status_code == 403 and r.get_json()["code"] == "funcion_no_incluida"


def test_para_llevar_ciclo_completo(salon, crear):
    ref = str(uuid.uuid4())
    url = f"/api/llevar/{ref}"
    assert salon.mesero.get(f"{url}/pedido").get_json()["pedido"] is None
    r = salon.mesero.post(f"{url}/items", json={
        "cliente": "Marta", "telefono": "310 555 1234",
        "items": [{"id_producto": salon.burger, "cantidad": 1, "uuid": str(uuid.uuid4())}]})
    assert r.status_code == 200, r.get_json()
    datos = r.get_json()
    assert datos["lugar"]["titulo"] == "Para llevar #1 · Marta" and datos["lugar"]["telefono"] == "3105551234"
    assert datos["mesa"] is None
    pedido = crear.fila("SELECT tipo, id_mesa, numero_llevar FROM pedidos")
    assert pedido["tipo"] == "llevar" and pedido["id_mesa"] is None and pedido["numero_llevar"] == 1

    r = salon.mesero.post(f"{url}/comanda")
    assert r.get_json()["comandas"][0]["lugar"] == "Para llevar #1 · Marta"
    assert salon.stock() == 9
    (comanda,) = salon.cocina.get("/api/cocina/comandas?estacion=cocina").get_json()["comandas"]
    assert comanda["mesa"] == "Llevar #1 · Marta" and comanda["llevar"] is True

    plano = salon.mesero.get("/api/mesas/plano").get_json()
    (llevar,) = plano["llevar"]
    assert llevar["uuid"] == ref and llevar["total"] == 18000 and llevar["en_cocina"] == 1
    assert all(m["estado"] == "libre" for m in plano["mesas"])

    # Se cobra antes de que la cocina termine; sigue en la lista hasta entregarlo.
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 0})
    r = salon.cajero.post(f"{url}/cobrar", json={"metodo": "efectivo", "uuid": str(uuid.uuid4())})
    assert r.status_code == 200 and r.get_json()["venta"]["lugar"] == "Para llevar #1 · Marta"
    assert crear.fila("SELECT observaciones FROM ventas")["observaciones"] == "Para llevar #1 · Marta"
    vista = salon.mesero.get(f"{url}/pedido").get_json()
    assert vista["pedido"]["estado"] == "cerrado" and not vista["pedido"]["entregado"]
    assert len(salon.mesero.get("/api/mesas/plano").get_json()["llevar"]) == 1
    # Cerrado: ya no se le agrega nada.
    r = salon.mesero.post(f"{url}/items", json={"items": [{"id_producto": salon.gaseosa, "cantidad": 1}]})
    assert r.status_code == 409

    assert salon.mesero.post(f"{url}/entregar").status_code == 200
    assert salon.mesero.get("/api/mesas/plano").get_json()["llevar"] == []
    assert crear.fila("SELECT estado FROM comandas")["estado"] == "entregada"
    assert salon.mesero.post(f"{url}/entregar").status_code == 200  # repetir no pasa nada

    # El siguiente para llevar es el #2.
    otro = f"/api/llevar/{uuid.uuid4()}"
    r = salon.mesero.post(f"{otro}/items", json={"items": [{"id_producto": salon.gaseosa, "cantidad": 1}]})
    assert r.get_json()["lugar"]["nombre"] == "#2"


def test_para_llevar_reenviado_no_se_duplica_y_se_anula(salon, crear):
    ref = str(uuid.uuid4())
    item = {"id_producto": salon.gaseosa, "cantidad": 1, "uuid": str(uuid.uuid4())}
    for _ in range(2):
        assert salon.mesero.post(f"/api/llevar/{ref}/items", json={"items": [item]}).status_code == 200
    assert crear.fila("SELECT COUNT(*) AS n FROM pedidos")["n"] == 1
    assert crear.fila("SELECT COUNT(*) AS n FROM pedido_items")["n"] == 1
    assert salon.mesero.post(f"/api/llevar/{ref}/anular", json={"motivo": "No volvió"}).status_code == 200
    assert salon.mesero.get("/api/mesas/plano").get_json()["llevar"] == []


def test_para_llevar_dividido_y_de_otra_sede(salon, app, crear):
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 0})
    ref = str(uuid.uuid4())
    salon.mesero.post(f"/api/llevar/{ref}/items", json={"items": [{"id_producto": salon.gaseosa, "cantidad": 2}]})
    salon.mesero.post(f"/api/llevar/{ref}/comanda")
    r = salon.cajero.post(f"/api/llevar/{ref}/cobrar-dividido", json={
        "modo": "iguales", "partes": [{"metodo": "efectivo"}, {"metodo": "efectivo"}]})
    assert r.status_code == 200 and [p["monto"] for p in r.get_json()["cobro"]["partes"]] == [4000, 4000]

    otra_tienda, _ = crear.tienda(nombre="Vecino")
    crear.usuario("vecino@chef.co", "Admin", otra_tienda)
    c = app.test_client()
    entrar(c, "vecino@chef.co")
    assert c.get(f"/api/llevar/{ref}/pedido").get_json()["pedido"] is None
    r = c.post(f"/api/llevar/{ref}/items", json={"items": [{"id_producto": salon.gaseosa, "cantidad": 1}]})
    assert r.status_code in (404, 409)
    assert c.get("/api/llevar/no-es-uuid/pedido").status_code == 400


def test_pantallas_de_llevar(salon):
    ref = str(uuid.uuid4())
    assert salon.mesero.get(f"/llevar/{ref}").status_code == 200
    assert salon.mesero.get("/llevar/xyz").status_code == 302


def test_dividir_sin_propina_cobra_cero(salon, crear):
    """El modal manda propina 0 por defecto; una parte sin propina no suma nada."""
    _mesa_lista(salon, (salon.gaseosa, 2))
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json={
        "modo": "iguales", "partes": [{"metodo": "efectivo", "propina": 0}, {"metodo": "efectivo"}]})
    assert r.status_code == 200, r.get_json()
    assert [p["propina"] for p in r.get_json()["cobro"]["partes"]] == [0, 0]
    assert float(crear.fila("SELECT propina FROM ventas")["propina"]) == 0


def test_modal_dividir_arranca_con_propina_cero():
    """Sin propina automatica: cada parte nueva del modal empieza en 0."""
    import os
    import re

    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "js", "pedido.js")
    js = open(ruta, encoding="utf-8").read()
    assert re.search(r"function nuevaParte\(n\) \{[^}]*propina: 0,", js)
    assert "function propinaDe(p) { return p.propina || 0; }" in js
    assert "monto * 0.1" not in js
