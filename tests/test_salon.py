"""Mesas, pedidos, comandas, inventario por sede y cobro con propina."""
import uuid

import pytest

from conftest import entrar


@pytest.fixture
def salon(app, crear):
    """Restaurante Completo con una sede, su equipo con sesion abierta, una
    mesa y dos productos: una hamburguesa que controla stock (10 en la sede)
    y una gaseosa sin preparacion."""
    id_tienda, (sede,) = crear.tienda("completo")
    clientes = {}
    for rol in ("Admin", "Mesero", "Cajero", "Cocina"):
        correo = f"{rol.lower()}@chef.co"
        crear.usuario(correo, rol, id_tienda, None if rol == "Admin" else sede)
        clientes[rol] = app.test_client()
        entrar(clientes[rol], correo)
    admin = clientes["Admin"]
    r = admin.post("/api/carta", json={"nombre": "Hamburguesa", "categoria": "Platos", "precio_venta": 18000,
                                       "precio_costo": 7000, "estacion": "cocina", "controla_stock": True})
    burger = r.get_json()["id_producto"]
    gaseosa = admin.post("/api/carta", json={"nombre": "Gaseosa", "precio_venta": 4000, "estacion": "ninguna"}).get_json()["id_producto"]
    assert admin.post(f"/api/carta/{burger}/inventario", json={"tipo": "Entrada", "cantidad": 10}).status_code == 200
    mesa = admin.post("/api/mesas", json={"nombre": "M1", "capacidad": 4}).get_json()["id_mesa"]
    otra = admin.post("/api/mesas", json={"nombre": "M2"}).get_json()["id_mesa"]

    class S:
        pass

    s = S()
    s.id_tienda, s.sede, s.mesa, s.otra, s.burger, s.gaseosa = id_tienda, sede, mesa, otra, burger, gaseosa
    s.admin, s.mesero, s.cajero, s.cocina = admin, clientes["Mesero"], clientes["Cajero"], clientes["Cocina"]
    s.stock = lambda: float(crear.fila(
        "SELECT stock_actual FROM stock_sedes WHERE id_sede = %s AND id_producto = %s", (sede, burger))["stock_actual"])
    return s


def _pedir(cliente, mesa, *items):
    return cliente.post(f"/api/mesas/{mesa}/items", json={"items": [
        {"id_producto": pid, "cantidad": cant, "uuid": str(uuid.uuid4()), **extra} for pid, cant, *rest in items
        for extra in [rest[0] if rest else {}]
    ]})


def test_ciclo_completo_descuenta_al_enviar_y_cobra_con_propina(salon, crear):
    r = _pedir(salon.mesero, salon.mesa, (salon.burger, 2, {"nota": "sin cebolla"}), (salon.gaseosa, 2))
    assert r.status_code == 200
    datos = r.get_json()
    assert datos["total"] == 44000 and datos["sin_enviar"] == 2
    assert salon.stock() == 10  # pedir no descuenta

    r = salon.mesero.post(f"/api/mesas/{salon.mesa}/comanda")
    assert r.status_code == 200
    comandas = r.get_json()["comandas"]
    assert [c["estacion"] for c in comandas] == ["cocina"]  # la gaseosa no va a cocina
    assert comandas[0]["items"][0]["nota"] == "sin cebolla"
    assert salon.stock() == 8  # enviar si descuenta
    kardex = crear.fila(
        "SELECT tipo_movimiento, cantidad, stock_anterior, stock_posterior, id_sede FROM movimientos_inventario "
        "WHERE id_producto = %s ORDER BY id_movimiento DESC LIMIT 1", (salon.burger,))
    assert kardex["tipo_movimiento"] == "Salida" and float(kardex["cantidad"]) == 2 and kardex["id_sede"] == salon.sede
    # Reenviar (cola sin conexion) no descuenta dos veces.
    assert salon.mesero.post(f"/api/mesas/{salon.mesa}/comanda").get_json()["comandas"] == []
    assert salon.stock() == 8

    plano = salon.mesero.get("/api/mesas/plano").get_json()
    m1 = next(m for m in plano["mesas"] if m["id_mesa"] == salon.mesa)
    assert m1["estado"] == "abierto" and m1["total"] == 44000

    # El mesero no cobra; sin caja abierta no se cobra.
    pago = {"metodo": "efectivo", "propina": 4400, "uuid": str(uuid.uuid4())}
    assert salon.mesero.post(f"/api/mesas/{salon.mesa}/cobrar", json=pago).status_code == 403
    assert salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json=pago).status_code == 409
    assert salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 50000}).status_code == 201
    assert salon.admin.post("/api/caja/abrir", json={"monto_inicial": 1}).status_code == 409  # una por sede

    pre = salon.mesero.post(f"/api/mesas/{salon.mesa}/precuenta").get_json()
    assert pre["pedido"]["estado"] == "por_cobrar" and pre["propina_sugerida"] == 4400

    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json=pago)
    assert r.status_code == 200, r.get_json()
    venta = r.get_json()["venta"]
    assert venta["total"] == 44000 and venta["propina"] == 4400 and venta["a_pagar"] == 48400
    fila = crear.fila("SELECT total_final, propina, id_sede, id_pedido, numero_venta FROM ventas WHERE id_venta = %s",
                      (venta["id_venta"],))
    assert float(fila["total_final"]) == 44000 and float(fila["propina"]) == 4400 and fila["id_sede"] == salon.sede
    assert fila["numero_venta"].endswith("-000001")
    assert salon.stock() == 8  # cobrar no vuelve a descontar

    caja = salon.cajero.get("/api/caja").get_json()["turno"]
    assert caja["esperado_en_caja"] == 50000 + 48400 and caja["propinas"] == 4400 and caja["total_ventas"] == 44000

    # El mismo cobro reenviado no cobra dos veces; la mesa quedo libre.
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json=pago)
    assert r.status_code == 200 and r.get_json()["venta"]["repetido"] is True
    assert crear.fila("SELECT COUNT(*) AS n FROM ventas")["n"] == 1
    assert salon.mesero.get(f"/api/mesas/{salon.mesa}/pedido").get_json()["pedido"] is None

    r = salon.cajero.post("/api/caja/cerrar", json={"monto_final_real": 98000})
    assert r.status_code == 200 and r.get_json()["resumen"]["diferencia"] == -400


def test_items_reenviados_desde_la_cola_no_se_duplican(salon):
    item = {"id_producto": salon.burger, "cantidad": 1, "uuid": str(uuid.uuid4())}
    for _ in range(2):
        r = salon.mesero.post(f"/api/mesas/{salon.mesa}/items", json={"items": [item]})
        assert r.status_code == 200
    assert len(r.get_json()["items"]) == 1 and r.get_json()["agregados"] == 0


def test_sin_inventario_no_se_envia_nada(salon, crear):
    r = _pedir(salon.mesero, salon.mesa, (salon.burger, 11))
    assert r.get_json()["avisos"]  # avisa al pedir
    r = salon.mesero.post(f"/api/mesas/{salon.mesa}/comanda")
    assert r.status_code == 409 and "Hamburguesa" in r.get_json()["msg"]
    assert salon.stock() == 10
    assert crear.fila("SELECT COUNT(*) AS n FROM comandas")["n"] == 0
    assert crear.fila("SELECT estado FROM pedido_items")["estado"] == "pendiente"


def test_no_se_cobra_con_productos_sin_enviar(salon):
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 0})
    _pedir(salon.mesero, salon.mesa, (salon.burger, 1))
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json={"metodo": "efectivo"})
    assert r.status_code == 409 and "sin enviar" in r.get_json()["msg"]


def test_anular_lo_enviado_merma_o_devolucion(salon, crear):
    _pedir(salon.mesero, salon.mesa, (salon.burger, 1), (salon.burger, 1))
    salon.mesero.post(f"/api/mesas/{salon.mesa}/comanda")
    assert salon.stock() == 8
    items = salon.mesero.get(f"/api/mesas/{salon.mesa}/pedido").get_json()["items"]
    uno, dos = (i["id_item"] for i in items)

    assert salon.mesero.post(f"/api/pedido-items/{uno}/anular", json={"motivo": "x"}).status_code == 403
    assert salon.admin.post(f"/api/pedido-items/{uno}/anular", json={}).status_code == 400  # sin motivo

    # Ya cocinado: merma, el inventario no vuelve.
    assert salon.admin.post(f"/api/pedido-items/{uno}/anular", json={"motivo": "Se quemó"}).status_code == 200
    merma = crear.fila("SELECT cantidad, costo_unitario, motivo FROM mermas")
    assert float(merma["cantidad"]) == 1 and float(merma["costo_unitario"]) == 7000
    assert salon.stock() == 8
    # No se alcanzo a preparar: vuelve al inventario.
    r = salon.admin.post(f"/api/pedido-items/{dos}/anular", json={"motivo": "Cliente se arrepintió", "devolver": True})
    assert r.status_code == 200
    assert salon.stock() == 9
    assert crear.fila("SELECT COUNT(*) AS n FROM auditoria WHERE accion = 'anular_item_enviado'")["n"] == 2
    # Todo anulado: la cuenta se puede anular y la mesa queda libre.
    assert salon.mesero.post(f"/api/mesas/{salon.mesa}/anular", json={"motivo": "Se fueron"}).status_code == 200
    assert salon.mesero.get(f"/api/mesas/{salon.mesa}/pedido").get_json()["pedido"] is None


def test_lo_pendiente_lo_quita_cualquiera_sin_tocar_inventario(salon):
    item = _pedir(salon.mesero, salon.mesa, (salon.burger, 1)).get_json()["items"][0]
    assert salon.mesero.post(f"/api/pedido-items/{item['id_item']}/anular", json={}).status_code == 200
    assert salon.stock() == 10


def test_pantalla_de_cocina(salon):
    _pedir(salon.mesero, salon.mesa, (salon.burger, 1))
    salon.mesero.post(f"/api/mesas/{salon.mesa}/comanda")
    r = salon.cocina.get("/api/cocina/comandas?estacion=cocina")
    assert r.status_code == 200
    datos = r.get_json()
    (comanda,) = datos["comandas"]
    assert comanda["mesa"] == "M1" and comanda["estado"] == "nueva" and comanda["items"][0]["nombre"] == "Hamburguesa"
    desde = datos["desde"]
    assert salon.cocina.get(f"/api/cocina/comandas?estacion=cocina&desde={desde}").get_json()["comandas"] == []

    url = f"/api/cocina/comandas/{comanda['id_comanda']}/estado"
    assert salon.cocina.post(url, json={"estado": "lista"}).status_code == 409  # no se salta pasos
    assert salon.cocina.post(url, json={"estado": "preparando"}).status_code == 200
    assert salon.cocina.post(url, json={"estado": "lista"}).status_code == 200
    cambios = salon.cocina.get(f"/api/cocina/comandas?estacion=cocina&desde={desde}").get_json()["comandas"]
    assert [c["estado"] for c in cambios] == ["lista"]
    m1 = next(m for m in salon.mesero.get("/api/mesas/plano").get_json()["mesas"] if m["id_mesa"] == salon.mesa)
    assert m1["listos"] == 1
    assert salon.mesero.get("/api/cocina/comandas").status_code == 403  # el mesero no


def test_cocina_no_viene_en_basico(app, crear):
    id_tienda, (sede,) = crear.tienda("basico", nombre="Chico")
    crear.usuario("cocina@chico.co", "Cocina", id_tienda, sede)
    c = app.test_client()
    entrar(c, "cocina@chico.co")
    r = c.get("/api/cocina/comandas")
    assert r.status_code == 403 and r.get_json()["code"] == "funcion_no_incluida"


def test_mover_y_unir_mesas(salon, crear):
    _pedir(salon.mesero, salon.mesa, (salon.burger, 1))
    r = salon.mesero.post(f"/api/mesas/{salon.mesa}/mover", json={"id_mesa_destino": salon.otra})
    assert r.status_code == 200
    assert salon.mesero.get(f"/api/mesas/{salon.mesa}/pedido").get_json()["pedido"] is None
    _pedir(salon.mesero, salon.mesa, (salon.gaseosa, 1))
    r = salon.mesero.post(f"/api/mesas/{salon.mesa}/mover", json={"id_mesa_destino": salon.otra})
    assert "unidas" in r.get_json()["msg"]
    destino = salon.mesero.get(f"/api/mesas/{salon.otra}/pedido").get_json()
    assert destino["total"] == 22000 and len(destino["items"]) == 2


def test_pago_mixto_debe_sumar_exacto(salon):
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 0})
    _pedir(salon.mesero, salon.mesa, (salon.gaseosa, 1))
    salon.mesero.post(f"/api/mesas/{salon.mesa}/comanda")
    mal = {"metodo": "mixto", "propina": 0, "monto_efectivo": 1000, "monto_transferencia": 2000}
    assert salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json=mal).status_code == 400
    bien = {**mal, "monto_transferencia": 3000}
    assert salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json=bien).status_code == 200
    assert salon.cajero.get("/api/caja").get_json()["turno"]["esperado_en_caja"] == 1000


def test_no_se_tocan_mesas_de_otra_sede_ni_otro_restaurante(salon, app, crear):
    otra_tienda, (otra_sede,) = crear.tienda(nombre="Vecino")
    crear.usuario("vecino@chef.co", "Admin", otra_tienda)
    c = app.test_client()
    entrar(c, "vecino@chef.co")
    assert c.get(f"/api/mesas/{salon.mesa}/pedido").status_code == 404
    assert c.post(f"/api/mesas/{salon.mesa}/items", json={"items": [{"id_producto": salon.burger, "cantidad": 1}]}).status_code == 404
    mesa_vecina = c.post("/api/mesas", json={"nombre": "M1"}).get_json()["id_mesa"]  # mismo nombre, otra sede
    r = c.post(f"/api/mesas/{mesa_vecina}/items", json={"items": [{"id_producto": salon.burger, "cantidad": 1}]})
    assert r.status_code == 404  # producto de otro restaurante


def test_mesa_con_cuenta_no_se_elimina_y_soft_delete(salon, crear):
    _pedir(salon.mesero, salon.mesa, (salon.burger, 1))
    assert salon.admin.delete(f"/api/mesas/{salon.mesa}").status_code == 409
    assert salon.admin.delete(f"/api/mesas/{salon.otra}").status_code == 200
    assert crear.fila("SELECT estado_activo FROM mesas WHERE id_mesa = %s", (salon.otra,))["estado_activo"] == 0
    assert salon.admin.post("/api/mesas", json={"nombre": "M2"}).status_code == 201  # nombre libre otra vez


def test_pantallas_cargan(salon):
    for url in ("/mesas", f"/mesas/{salon.mesa}", "/carta", "/mesas/configurar", "/caja", "/cocina"):
        assert salon.admin.get(url).status_code == 200, url
    assert salon.mesero.get("/carta").status_code == 302
    assert salon.cocina.get("/cocina").status_code == 200
