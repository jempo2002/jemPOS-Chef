"""Pago Mixto elegido a mano: en cada parte de una cuenta dividida y en gastos."""
import uuid

from test_dividida_llevar import _mesa_lista
from test_salon import salon  # noqa: F401  (fixture)


def test_parte_mixta_por_productos(salon, crear):
    detalle = _mesa_lista(salon, (salon.burger, 2))  # 2 x 18.000
    burger = detalle["items"][0]["id_item"]
    parte = lambda **extra: {"items": [{"id_item": burger, "cantidad": 1}], "propina": 0, **extra}  # noqa: E731
    cobro = {"modo": "items", "uuid": str(uuid.uuid4()), "partes": [
        parte(etiqueta="Ana", metodo="mixto", monto_efectivo=18000), parte(etiqueta="Luis", metodo="efectivo"),
    ]}
    # Todo en efectivo no es mixto; sin efectivo tampoco.
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro)
    assert r.status_code == 400 and "Ana" in r.get_json()["msg"]
    cobro["partes"][0]["monto_efectivo"] = None
    assert salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro).status_code == 400
    assert crear.fila("SELECT COUNT(*) AS n FROM ventas")["n"] == 0

    cobro["partes"][0]["monto_efectivo"] = 10000
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro)
    assert r.status_code == 200, r.get_json()
    ana = r.get_json()["cobro"]["partes"][0]
    assert ana["metodo_pago"] == "Mixto" and ana["monto_efectivo"] == 10000 and ana["monto_transferencia"] == 8000
    venta = crear.fila("SELECT monto_efectivo, monto_transferencia FROM ventas WHERE metodo_pago = 'Mixto'")
    assert float(venta["monto_efectivo"]) == 10000 and float(venta["monto_transferencia"]) == 8000
    cuenta = crear.fila("SELECT monto_efectivo, monto_transferencia FROM pedido_cuentas WHERE metodo_pago = 'Mixto'")
    assert float(cuenta["monto_efectivo"]) == 10000 and float(cuenta["monto_transferencia"]) == 8000
    caja = salon.cajero.get("/api/caja").get_json()["turno"]
    assert caja["esperado_en_caja"] == 10000 + 18000 and caja["efectivo_ventas"] == 28000
    assert caja["nequi"] == 8000

    # Reenviado desde la cola: devuelve lo mismo sin cobrar otra vez.
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json=cobro)
    assert r.get_json()["cobro"]["repetido"] is True and r.get_json()["cobro"]["partes"][0]["monto_efectivo"] == 10000


def test_partes_iguales_todas_mixtas(salon, crear):
    _mesa_lista(salon, (salon.burger, 1), (salon.gaseosa, 1))  # 22.000 entre 2
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json={"modo": "iguales", "partes": [
        {"metodo": "mixto", "monto_efectivo": 5000, "propina": 1000}, {"metodo": "mixto", "monto_efectivo": 2000},
    ]})
    assert r.status_code == 200, r.get_json()
    venta = crear.fila("SELECT metodo_pago, monto_efectivo, monto_transferencia, propina FROM ventas")
    assert venta["metodo_pago"] == "Mixto" and float(venta["monto_efectivo"]) == 7000
    assert float(venta["monto_transferencia"]) == 22000 + 1000 - 7000
    caja = salon.cajero.get("/api/caja").get_json()["turno"]
    assert caja["esperado_en_caja"] == 7000 and caja["nequi"] == 16000


def test_gastos_con_cada_metodo(salon, crear):
    assert salon.cajero.post("/api/caja/gastos", json={"concepto": "Hielo", "monto": 5000, "metodo": "efectivo"}).status_code == 409
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 100000})
    assert salon.mesero.post("/api/caja/gastos", json={"concepto": "Hielo", "monto": 5000, "metodo": "efectivo"}).status_code == 403

    def gasto(**datos):
        return salon.cajero.post("/api/caja/gastos", json={"concepto": "Gas", "monto": 30000, **datos})

    assert gasto(metodo="cheque").status_code == 400
    assert gasto(metodo="mixto").status_code == 400  # falta el efectivo
    assert gasto(metodo="mixto", monto_efectivo=30000).status_code == 400  # eso es efectivo
    assert gasto(metodo="efectivo", monto=5000, concepto="Hielo").status_code == 201
    assert gasto(metodo="nequi").status_code == 201
    r = gasto(metodo="mixto", monto_efectivo=12000)
    assert r.status_code == 201 and r.get_json()["gasto"]["efectivo"] == 12000

    mixto = crear.fila("SELECT monto, monto_transferencia, fuente_dinero FROM gastos_caja WHERE metodo_pago = 'Mixto'")
    assert float(mixto["monto"]) == 30000 and float(mixto["monto_transferencia"]) == 18000
    assert mixto["fuente_dinero"] == "Caja Menor"
    assert crear.fila("SELECT fuente_dinero FROM gastos_caja WHERE metodo_pago = 'Nequi/Daviplata'")["fuente_dinero"] == "Bancos"

    caja = salon.cajero.get("/api/caja").get_json()["turno"]
    assert caja["gastos"] == 3 and caja["total_gastos"] == 65000 and caja["gastos_efectivo"] == 17000
    assert caja["esperado_en_caja"] == 100000 - 17000
    assert [g["metodo_pago"] for g in caja["lista_gastos"]] == ["Mixto", "Nequi/Daviplata", "Efectivo"]
    pagina = salon.cajero.get("/caja").get_data(as_text=True)
    assert "Gas" in pagina and "Mixto (efectivo + transferencia)" in pagina

    r = salon.cajero.post("/api/caja/cerrar", json={"monto_final_real": 83000})
    assert r.status_code == 200 and "exacto" in r.get_json()["msg"]
