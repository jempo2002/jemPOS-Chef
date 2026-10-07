"""Insumos, recetas, costo por plato y descuento de ingredientes al enviar a cocina."""
import uuid

import pytest

from conftest import entrar


@pytest.fixture
def cocina(app, crear):
    """Restaurante Completo con mesa, caja abierta, dos insumos con stock
    (pan en unidades, queso en gramos) y una hamburguesa sin receta."""
    id_tienda, (sede,) = crear.tienda("completo")
    crear.usuario("admin@chef.co", "Admin", id_tienda)
    admin = app.test_client()
    entrar(admin, "admin@chef.co")
    burger = admin.post("/api/carta", json={"nombre": "Hamburguesa", "precio_venta": 20000}).get_json()["id_producto"]
    sanduche = admin.post("/api/carta", json={"nombre": "Sanduche", "precio_venta": 10000}).get_json()["id_producto"]
    mesa = admin.post("/api/mesas", json={"nombre": "M1"}).get_json()["id_mesa"]
    pan = admin.post("/api/insumos", json={"nombre": "Pan", "unidad_medida": "Unidad", "stock_minimo_alerta": 2}).get_json()["id_insumo"]
    queso = admin.post("/api/insumos", json={"nombre": "Queso", "unidad_medida": "Gramo"}).get_json()["id_insumo"]
    # 10 panes por $10.000 ($1.000 c/u) y 500 g de queso por $8.000 ($16/g).
    assert admin.post(f"/api/insumos/{pan}/movimiento", json={"tipo": "Entrada", "cantidad": 10, "precio_total": 10000}).status_code == 200
    assert admin.post(f"/api/insumos/{queso}/movimiento", json={"tipo": "Entrada", "cantidad": 500, "unidad": "g", "precio_total": "8.000"}).status_code == 200

    class C:
        pass

    c = C()
    c.id_tienda, c.sede, c.admin, c.mesa, c.burger, c.sanduche, c.pan, c.queso = id_tienda, sede, admin, mesa, burger, sanduche, pan, queso

    def stock(id_insumo):
        fila = crear.fila("SELECT stock_actual FROM stock_insumos_sedes WHERE id_sede = %s AND id_insumo = %s", (sede, id_insumo))
        return float(fila["stock_actual"]) if fila else 0.0

    c.stock = stock
    return c


def _receta(c, id_producto, *lineas):
    return c.admin.put(f"/api/recetas/{id_producto}", json={"lineas": [{"id_insumo": i, "cantidad": q} for i, q in lineas]})


def _pedir_y_enviar(c, *items):
    r = c.admin.post(f"/api/mesas/{c.mesa}/items", json={"items": [
        {"id_producto": pid, "cantidad": cant, "uuid": str(uuid.uuid4())} for pid, cant in items]})
    assert r.status_code == 200, r.get_json()
    return c.admin.post(f"/api/mesas/{c.mesa}/comanda")


def test_enviar_descuenta_insumos_una_sola_vez(cocina, crear):
    assert _receta(cocina, cocina.burger, (cocina.pan, 1), (cocina.queso, 50)).status_code == 200
    r = _pedir_y_enviar(cocina, (cocina.burger, 2))
    assert r.status_code == 200, r.get_json()
    assert cocina.stock(cocina.pan) == 8 and cocina.stock(cocina.queso) == 400
    mov = crear.fila(
        "SELECT tipo_movimiento, cantidad, id_pedido, id_producto FROM movimientos_inventario "
        "WHERE id_insumo = %s ORDER BY id_movimiento DESC LIMIT 1", (cocina.queso,))
    assert mov["tipo_movimiento"] == "Salida" and float(mov["cantidad"]) == 100
    assert mov["id_pedido"] is not None and mov["id_producto"] is None
    # Reenviar no descuenta; cobrar tampoco.
    assert cocina.admin.post(f"/api/mesas/{cocina.mesa}/comanda").get_json()["comandas"] == []
    assert cocina.admin.post("/api/caja/abrir", json={"monto_inicial": 0}).status_code == 201
    r = cocina.admin.post(f"/api/mesas/{cocina.mesa}/cobrar", json={"metodo": "efectivo", "uuid": str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert cocina.stock(cocina.pan) == 8 and cocina.stock(cocina.queso) == 400


def test_validacion_acumulada_por_insumo(cocina):
    # Cada plato por separado alcanza (300 g y 300 g de 500 g), juntos no.
    _receta(cocina, cocina.burger, (cocina.queso, 300))
    _receta(cocina, cocina.sanduche, (cocina.queso, 300))
    r = _pedir_y_enviar(cocina, (cocina.burger, 1), (cocina.sanduche, 1))
    assert r.status_code == 409
    assert "Queso" in r.get_json()["msg"]
    assert cocina.stock(cocina.queso) == 500  # nada se desconto


def test_compra_recalcula_costo_promedio_y_costo_del_plato(cocina, crear):
    _receta(cocina, cocina.burger, (cocina.queso, 100))
    assert float(crear.fila("SELECT precio_costo FROM productos WHERE id_producto = %s", (cocina.burger,))["precio_costo"]) == 1600
    # 1 kg por $20.000 sobre 500 g a $16/g -> (8.000 + 20.000) / 1.500 = $18,6667/g
    r = cocina.admin.post(f"/api/insumos/{cocina.queso}/movimiento",
                          json={"tipo": "Entrada", "cantidad": "1", "unidad": "kg", "precio_total": "$20.000"})
    assert r.status_code == 200 and round(r.get_json()["costo_unitario"], 2) == 18.67
    assert cocina.stock(cocina.queso) == 1500
    # El costo del plato se actualiza solo (copia a precio_costo para mermas y utilidad).
    assert float(crear.fila("SELECT precio_costo FROM productos WHERE id_producto = %s", (cocina.burger,))["precio_costo"]) == 1866.67
    receta = cocina.admin.get(f"/api/recetas/{cocina.burger}").get_json()
    assert receta["costo"] == 1866.67 and receta["disponibles"] == 15


def test_costos_marca_platos_sobre_el_margen(cocina):
    _receta(cocina, cocina.burger, (cocina.pan, 1), (cocina.queso, 50))   # 1.800 / 20.000 = 9 %
    _receta(cocina, cocina.sanduche, (cocina.pan, 4))                      # 4.000 / 10.000 = 40 %
    datos = cocina.admin.get("/api/recetas/costos").get_json()
    assert [p["nombre"] for p in datos["platos"]] == ["Sanduche", "Hamburguesa"]
    assert [p["alerta"] for p in datos["platos"]] == [True, False]
    assert datos["platos"][0]["pct_costo"] == 40.0 and datos["margen_alerta"] == 35
    assert cocina.admin.put("/api/recetas/margen", json={"margen_alerta": "45"}).status_code == 200
    assert cocina.admin.get("/api/recetas/costos").get_json()["en_alerta"] == 0
    csv = cocina.admin.get("/recetas/costos.csv")
    assert csv.status_code == 200 and "Sanduche;" in csv.get_data(as_text=True)


def test_soft_delete_de_insumos_y_lineas(cocina, crear):
    _receta(cocina, cocina.burger, (cocina.pan, 1), (cocina.queso, 50))
    id_linea = crear.fila("SELECT id_receta FROM recetas_productos WHERE id_producto = %s AND id_insumo = %s",
                          (cocina.burger, cocina.queso))["id_receta"]
    # Un insumo en una receta activa no se puede eliminar.
    r = cocina.admin.delete(f"/api/insumos/{cocina.queso}")
    assert r.status_code == 409 and "Hamburguesa" in r.get_json()["msg"]
    # Quitar y volver a poner el queso reactiva la misma fila.
    _receta(cocina, cocina.burger, (cocina.pan, 1))
    assert crear.fila("SELECT estado_activo FROM recetas_productos WHERE id_receta = %s", (id_linea,))["estado_activo"] == 0
    _receta(cocina, cocina.burger, (cocina.pan, 1), (cocina.queso, 60))
    fila = crear.fila("SELECT estado_activo, cantidad_requerida FROM recetas_productos WHERE id_receta = %s", (id_linea,))
    assert fila["estado_activo"] == 1 and float(fila["cantidad_requerida"]) == 60
    # Fuera de la receta ya se puede eliminar; luego no aparece ni se descuenta.
    _receta(cocina, cocina.burger, (cocina.pan, 1))
    assert cocina.admin.delete(f"/api/insumos/{cocina.queso}").status_code == 200
    assert cocina.queso not in [i["id_insumo"] for i in cocina.admin.get("/api/insumos").get_json()["insumos"]]
    assert _receta(cocina, cocina.burger, (cocina.queso, 10)).status_code == 404
    # Se puede crear otro insumo con el mismo nombre.
    assert cocina.admin.post("/api/insumos", json={"nombre": "Queso", "unidad_medida": "Gramo"}).status_code == 201


def test_otra_tienda_no_ve_ni_usa_mis_insumos(cocina, app, crear):
    otra, _ = crear.tienda("completo", nombre="Otro")
    crear.usuario("otro@chef.co", "Admin", otra)
    ajeno = app.test_client()
    entrar(ajeno, "otro@chef.co")
    assert ajeno.get("/api/insumos").get_json()["insumos"] == []
    plato = ajeno.post("/api/carta", json={"nombre": "Pizza", "precio_venta": 1000}).get_json()["id_producto"]
    assert ajeno.put(f"/api/recetas/{plato}", json={"lineas": [{"id_insumo": cocina.queso, "cantidad": 1}]}).status_code == 404
    assert ajeno.post(f"/api/insumos/{cocina.queso}/movimiento", json={"tipo": "Entrada", "cantidad": 1}).status_code == 404
    assert ajeno.get(f"/api/recetas/{cocina.burger}").status_code == 404


def test_anular_sin_preparar_devuelve_insumos(cocina, crear):
    _receta(cocina, cocina.burger, (cocina.pan, 1), (cocina.queso, 50))
    _pedir_y_enviar(cocina, (cocina.burger, 2))
    item = crear.fila("SELECT id_item FROM pedido_items WHERE id_producto = %s", (cocina.burger,))["id_item"]
    r = cocina.admin.post(f"/api/pedido-items/{item}/anular", json={"motivo": "Se fueron", "devolver": True})
    assert r.status_code == 200, r.get_json()
    assert cocina.stock(cocina.pan) == 10 and cocina.stock(cocina.queso) == 500


def test_plato_con_receta_no_lleva_inventario_propio(cocina):
    _receta(cocina, cocina.burger, (cocina.pan, 1))
    r = cocina.admin.put(f"/api/carta/{cocina.burger}", json={"nombre": "Hamburguesa", "precio_venta": 20000,
                                                             "controla_stock": True})
    assert r.status_code == 409
    gaseosa = cocina.admin.post("/api/carta", json={"nombre": "Gaseosa", "precio_venta": 3000, "controla_stock": True}).get_json()["id_producto"]
    assert _receta(cocina, gaseosa, (cocina.pan, 1)).status_code == 409
    # La carta del pedido muestra cuantos platos alcanzan.
    carta = {p["id_producto"]: p for p in cocina.admin.get("/api/carta").get_json()["productos"]}
    assert carta[cocina.burger]["disponibles"] == 10 and carta[gaseosa]["disponibles"] is None


def test_kardex_no_se_edita_ni_se_borra(cocina, db):
    cur = db.cursor()
    with pytest.raises(Exception):
        cur.execute("UPDATE movimientos_inventario SET cantidad = 0")
    with pytest.raises(Exception):
        cur.execute("DELETE FROM movimientos_inventario")


def test_plan_basico_no_tiene_recetas(app, crear):
    id_tienda, (sede,) = crear.tienda("basico")
    crear.usuario("basico@chef.co", "Admin", id_tienda)
    cliente = app.test_client()
    entrar(cliente, "basico@chef.co")
    r = cliente.get("/api/insumos")
    assert r.status_code == 403 and r.get_json()["code"] == "funcion_no_incluida"
    pagina = cliente.get("/recetas")
    assert pagina.status_code == 200 and "Pasarme al Plan Completo" in pagina.get_data(as_text=True)


def test_pantallas_cargan(cocina):
    _receta(cocina, cocina.burger, (cocina.pan, 1))
    for url in ("/recetas", "/recetas?pestana=recetas", "/recetas?pestana=costos", "/recetas?estado=bajo",
                f"/recetas/plato/{cocina.burger}", f"/recetas/insumos/{cocina.queso}"):
        r = cocina.admin.get(url)
        assert r.status_code == 200, url
