"""Domicilios: despacho con domiciliario y recaudo del efectivo en Caja."""
import uuid

from test_salon import salon  # noqa: F401  (fixture)


def _domicilio(cliente, *items, enviar=True, **extra):
    """Crea un domicilio con los platos dados y lo manda a cocina."""
    u = str(uuid.uuid4())
    cuerpo = {"domicilio": True, "direccion": "Cra 5 # 10-20", "cliente": "Ana", "telefono": "3001234567",
              "items": [{"id_producto": pid, "cantidad": cant, "uuid": str(uuid.uuid4())} for pid, cant in items], **extra}
    r = cliente.post(f"/api/llevar/{u}/items", json=cuerpo)
    assert r.status_code == 200, r.get_json()
    if enviar:
        assert cliente.post(f"/api/llevar/{u}/comanda").status_code == 200
    return u, r.get_json()["pedido"]["id_pedido"]


def _rider(salon, nombre="Pedro"):
    r = salon.cajero.post("/api/domiciliarios", json={"nombre": nombre, "telefono": "3110000000"})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id_domiciliario"]


def _tarjeta(salon, rider):
    datos = salon.cajero.get("/api/domicilios/recaudo").get_json()
    return next(t for t in datos["domiciliarios"] if t["id_domiciliario"] == rider)


def test_domicilio_se_crea_con_direccion_y_numero_propio(salon, crear):
    r = salon.mesero.post(f"/api/llevar/{uuid.uuid4()}/items", json={
        "domicilio": True, "items": [{"id_producto": salon.gaseosa, "cantidad": 1}]})
    assert r.status_code == 400 and "dirección" in r.get_json()["msg"]
    u, id_pedido = _domicilio(salon.mesero, (salon.burger, 1))
    detalle = salon.mesero.get(f"/api/llevar/{u}/pedido").get_json()
    assert detalle["lugar"]["tipo"] == "domicilio" and detalle["lugar"]["titulo"] == "Domicilio #1 · Ana"
    assert detalle["lugar"]["domicilio"]["direccion"] == "Cra 5 # 10-20"
    assert detalle["lugar"]["domicilio"]["estado"] == "por_despachar"
    lista = salon.mesero.get("/api/domicilios").get_json()["domicilios"]
    assert [d["id_pedido"] for d in lista] == [id_pedido] and lista[0]["total"] == 18000
    # No aparece en la lista de para llevar de Mesas.
    assert salon.mesero.get("/api/mesas/plano").get_json()["llevar"] == []


def test_despachar_y_recaudo_solo_cuenta_efectivo_sin_cobrar(salon, crear):
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 50000})
    pedro = _rider(salon)
    _, efectivo = _domicilio(salon.mesero, (salon.burger, 2))          # 36.000 en efectivo
    _, nequi = _domicilio(salon.mesero, (salon.gaseosa, 3))            # 12.000 por Nequi
    u_pagado, pagado = _domicilio(salon.mesero, (salon.gaseosa, 1))    # 4.000 cobrado antes de salir

    # Con algo sin enviar a cocina no sale.
    _, pend = _domicilio(salon.mesero, (salon.gaseosa, 1), enviar=False)
    r = salon.mesero.post(f"/api/domicilios/{pend}/despachar", json={"id_domiciliario": pedro, "metodo": "efectivo"})
    assert r.status_code == 409

    r = salon.mesero.post(f"/api/domicilios/{efectivo}/despachar",
                          json={"id_domiciliario": pedro, "metodo": "efectivo", "paga_con": 50000})
    assert r.status_code == 200, r.get_json()
    ticket = r.get_json()["ticket"]
    assert ticket["cobrar"] == 36000 and ticket["vuelto"] == 14000 and ticket["metodo_pago"] == "Efectivo"
    assert salon.mesero.post(f"/api/domicilios/{efectivo}/despachar",
                             json={"id_domiciliario": pedro, "metodo": "efectivo", "paga_con": 20000}).status_code == 400

    salon.mesero.post(f"/api/domicilios/{nequi}/despachar", json={"id_domiciliario": pedro, "metodo": "nequi"})
    r = salon.cajero.post(f"/api/llevar/{u_pagado}/cobrar", json={"metodo": "efectivo", "propina": 0})
    assert r.status_code == 200, r.get_json()
    ticket = salon.mesero.post(f"/api/domicilios/{pagado}/despachar", json={"id_domiciliario": pedro}).get_json()["ticket"]
    assert ticket["pagado"] is True and ticket["cobrar"] == 0

    t = _tarjeta(salon, pedro)
    assert t["efectivo"] == 36000 and t["en_camino"] == 3 and t["otros_medios"] == 12000 and t["alerta"] is False

    # En camino no se agregan platos ni se cobra desde el pedido.
    u_ef = crear.fila("SELECT uuid_cliente FROM pedidos WHERE id_pedido = %s", (efectivo,))["uuid_cliente"]
    assert salon.mesero.post(f"/api/llevar/{u_ef}/items", json={"items": [{"id_producto": salon.gaseosa, "cantidad": 1}]}).status_code == 409
    assert salon.cajero.post(f"/api/llevar/{u_ef}/cobrar", json={"metodo": "efectivo"}).status_code == 409

    # Mesero no recibe recaudos.
    cuerpo = {"id_domiciliario": pedro, "pedidos": [efectivo, nequi, pagado], "efectivo": 36000}
    assert salon.mesero.post("/api/domicilios/recaudo", json=cuerpo).status_code == 403
    # Cifra vieja: no registra nada.
    r = salon.cajero.post("/api/domicilios/recaudo", json={**cuerpo, "efectivo": 30000})
    assert r.status_code == 409 and "36.000" in r.get_json()["msg"]
    assert crear.fila("SELECT COUNT(*) AS n FROM ventas")["n"] == 1

    r = salon.cajero.post("/api/domicilios/recaudo", json=cuerpo)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["recaudo"]["efectivo"] == 36000 and len(r.get_json()["recaudo"]["ventas"]) == 2
    ventas = crear.fila("SELECT COUNT(*) AS n, SUM(total_final) AS total FROM ventas")
    assert ventas["n"] == 3 and float(ventas["total"]) == 52000
    caja = salon.cajero.get("/api/caja").get_json()["turno"]
    assert caja["esperado_en_caja"] == 50000 + 4000 + 36000  # el Nequi no entra al cajon
    assert _tarjeta(salon, pedro)["domicilios"] == []
    assert crear.fila("SELECT COUNT(*) AS n FROM pedido_domicilios WHERE estado = 'liquidado'")["n"] == 3
    # Reenviado: ya no esta en la calle.
    assert salon.cajero.post("/api/domicilios/recaudo", json=cuerpo).status_code == 409
    assert crear.fila("SELECT COUNT(*) AS n FROM ventas")["n"] == 3


def test_cambiar_a_nequi_en_camino_y_roles(salon, crear):
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 0})
    pedro = _rider(salon)
    _, id_pedido = _domicilio(salon.mesero, (salon.burger, 1))
    salon.mesero.post(f"/api/domicilios/{id_pedido}/despachar", json={"id_domiciliario": pedro, "metodo": "efectivo"})
    assert _tarjeta(salon, pedro)["efectivo"] == 18000

    assert salon.mesero.post(f"/api/domicilios/{id_pedido}/metodo", json={"metodo": "nequi"}).status_code == 403
    r = salon.cajero.post(f"/api/domicilios/{id_pedido}/metodo", json={"metodo": "nequi"})
    assert r.status_code == 200 and r.get_json()["domiciliario"]["efectivo"] == 0
    assert _tarjeta(salon, pedro)["efectivo"] == 0
    assert crear.fila("SELECT COUNT(*) AS n FROM auditoria WHERE accion = 'domicilio_metodo'")["n"] == 1

    assert salon.mesero.post(f"/api/domicilios/{id_pedido}/entregado").status_code == 200
    r = salon.cajero.post("/api/domicilios/recaudo", json={"id_domiciliario": pedro, "pedidos": [id_pedido], "efectivo": 0})
    assert r.status_code == 200, r.get_json()
    venta = crear.fila("SELECT metodo_pago, total_final FROM ventas")
    assert venta["metodo_pago"] == "Nequi/Daviplata" and float(venta["total_final"]) == 18000
    assert salon.cajero.get("/api/caja").get_json()["turno"]["esperado_en_caja"] == 0


def test_alerta_por_tope_regresar_y_no_eliminar_con_recaudo(salon, crear):
    pedro = _rider(salon)
    assert salon.cajero.put("/api/domicilios/tope", json={"tope": 30000}).status_code == 403
    assert salon.admin.put("/api/domicilios/tope", json={"tope": 30000}).status_code == 200
    _, id_pedido = _domicilio(salon.mesero, (salon.burger, 2))
    salon.mesero.post(f"/api/domicilios/{id_pedido}/despachar", json={"id_domiciliario": pedro, "metodo": "efectivo"})
    assert _tarjeta(salon, pedro)["alerta"] is True

    assert salon.cajero.delete(f"/api/domiciliarios/{pedro}").status_code == 409
    assert salon.cajero.post(f"/api/domicilios/{id_pedido}/regresar", json={}).status_code == 400  # sin motivo
    assert salon.cajero.post(f"/api/domicilios/{id_pedido}/regresar", json={"motivo": "No contestó"}).status_code == 200
    assert _tarjeta(salon, pedro)["efectivo"] == 0
    assert salon.cajero.delete(f"/api/domiciliarios/{pedro}").status_code == 200
    assert crear.fila("SELECT estado_activo FROM domiciliarios WHERE id_domiciliario = %s", (pedro,))["estado_activo"] == 0
    # Mismo nombre otra vez: se puede (el eliminado no ocupa el nombre).
    _rider(salon)


def test_otra_sede_no_ve_ni_toca_los_domicilios(salon, crear, app):
    from conftest import entrar

    pedro = _rider(salon)
    _, id_pedido = _domicilio(salon.mesero, (salon.gaseosa, 1))
    otra = crear.sede(salon.id_tienda, "Norte")
    crear.usuario("cajero2@chef.co", "Cajero", salon.id_tienda, otra)
    cliente = app.test_client()
    entrar(cliente, "cajero2@chef.co")
    assert cliente.post(f"/api/domicilios/{id_pedido}/despachar", json={"id_domiciliario": pedro}).status_code == 404
    assert cliente.get("/api/domicilios").get_json()["domicilios"] == []
    assert cliente.post(f"/api/domicilios/{id_pedido}/metodo", json={"metodo": "nequi"}).status_code == 404
