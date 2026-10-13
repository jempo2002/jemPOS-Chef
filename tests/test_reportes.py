"""Reportes y contabilidad: estado de resultados, costo historico, gastos
por categoria y anulacion, filtros por sede, cierres y exportes."""

from conftest import entrar
from test_dividida_llevar import _mesa_lista
from test_salon import salon  # noqa: F401  (fixture)


def _resumen(app, id_tienda, ids, clave="hoy", **rango):
    from app.services import reportes_service

    with app.app_context():
        return reportes_service.resumen(id_tienda, ids, reportes_service.periodo(clave, **rango))


def _venta_y_gastos(salon):
    _mesa_lista(salon, (salon.burger, 2), (salon.gaseosa, 1))  # 36.000 + 4.000, costo 2 x 7.000
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json={"metodo": "efectivo", "propina": 2000})
    assert r.status_code == 200, r.get_json()
    assert salon.cajero.post("/api/caja/gastos", json={
        "concepto": "Hielo", "monto": 5000, "metodo": "efectivo"}).status_code == 201
    assert salon.cajero.post("/api/caja/gastos", json={
        "concepto": "Carne", "categoria": "insumos", "monto": 20000, "metodo": "nequi"}).status_code == 201
    r = salon.admin.post("/api/gastos", json={
        "concepto": "Arriendo", "categoria": "arriendo", "monto": 100000, "metodo": "transferencia"})
    assert r.status_code == 201, r.get_json()


def test_estado_de_resultados(app, salon, crear):
    _venta_y_gastos(salon)
    r = _resumen(app, salon.id_tienda, [salon.sede])
    assert r["ventas"] == 40000 and r["num_ventas"] == 1 and r["propinas"] == 2000
    assert r["costo"] == 14000 and r["utilidad_bruta"] == 26000
    assert r["gastos_operacion"] == 105000  # hielo + arriendo; la carne es compra
    assert r["compras"] == 20000
    assert r["utilidad_neta"] == 26000 - 105000
    assert r["flujo_caja"] == 40000 - 105000 - 20000
    assert r["sin_costo"] == 1  # la gaseosa no tiene costo
    metodos = {m["metodo"]: m["total"] for m in r["metodos"]}
    assert metodos == {"Efectivo": 42000, "Nequi/Daviplata": 0, "Tarjeta": 0}
    assert {t["tipo"]: t["total"] for t in r["por_tipo"]} == {"mesa": 40000}

    pagina = salon.admin.get("/reportes").get_data(as_text=True)
    assert "Estado de resultados" in pagina and "-$79.000" in pagina and "Arriendo" in pagina
    assert "no tiene costo" in pagina
    for pestana in ("ventas", "gastos", "cierres"):
        assert salon.admin.get(f"/reportes?pestana={pestana}&periodo=mes").status_code == 200


def test_costo_queda_fijo_al_cobrar(app, salon, crear):
    _venta_y_gastos(salon)
    linea = crear.fila("SELECT costo_unitario_historico FROM detalle_ventas WHERE id_producto = %s", (salon.burger,))
    assert float(linea["costo_unitario_historico"]) == 7000
    assert salon.admin.put(f"/api/carta/{salon.burger}", json={
        "nombre": "Hamburguesa", "precio_venta": 18000, "precio_costo": 9000, "estacion": "cocina",
        "controla_stock": True}).status_code == 200
    assert _resumen(app, salon.id_tienda, [salon.sede])["costo"] == 14000


def test_pago_mixto_y_tarjeta_por_medio(app, salon, crear):
    _mesa_lista(salon, (salon.burger, 1), (salon.gaseosa, 1))  # 22.000 entre 2
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar-dividido", json={"modo": "iguales", "partes": [
        {"metodo": "tarjeta"}, {"metodo": "mixto", "monto_efectivo": 5000},
    ]})
    assert r.status_code == 200, r.get_json()
    metodos = {m["metodo"]: m["total"] for m in _resumen(app, salon.id_tienda, [salon.sede])["metodos"]}
    assert metodos == {"Efectivo": 5000, "Nequi/Daviplata": 6000, "Tarjeta": 11000}


def test_anular_gasto(salon, crear):
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 50000})
    assert salon.cajero.post("/api/caja/gastos", json={
        "concepto": "Gas", "categoria": "servicios", "monto": 30000, "metodo": "efectivo"}).status_code == 201
    id_gasto = crear.fila("SELECT id_gasto FROM gastos_caja")["id_gasto"]
    assert salon.cajero.get("/api/caja").get_json()["turno"]["esperado_en_caja"] == 20000

    assert salon.cajero.post(f"/api/gastos/{id_gasto}/anular", json={"motivo": "x"}).status_code == 403
    assert salon.admin.post(f"/api/gastos/{id_gasto}/anular", json={}).status_code == 400  # sin motivo
    r = salon.admin.post(f"/api/gastos/{id_gasto}/anular", json={"motivo": "Lo pagó el dueño"})
    assert r.status_code == 200 and r.get_json()["devuelto_a_caja"] == 30000
    assert salon.admin.post(f"/api/gastos/{id_gasto}/anular", json={"motivo": "otra vez"}).status_code == 409

    caja = salon.cajero.get("/api/caja").get_json()["turno"]
    assert caja["esperado_en_caja"] == 50000 and caja["gastos"] == 0
    fila = crear.fila("SELECT estado_activo, motivo_anulacion FROM gastos_caja WHERE id_gasto = %s", (id_gasto,))
    assert fila["estado_activo"] == 0 and fila["motivo_anulacion"] == "Lo pagó el dueño"
    assert crear.fila("SELECT COUNT(*) AS n FROM auditoria WHERE accion = 'anular_gasto'")["n"] == 1
    pagina = salon.admin.get("/reportes?pestana=gastos").get_data(as_text=True)
    assert "Anulado" in pagina and "Lo pagó el dueño" in pagina


def test_gasto_admin_validaciones(salon, crear):
    gasto = {"concepto": "Nómina", "categoria": "nomina", "monto": 80000, "metodo": "efectivo"}
    assert salon.cajero.post("/api/gastos", json=gasto).status_code == 403
    assert salon.admin.post("/api/gastos", json={**gasto, "categoria": "fiesta"}).status_code == 400
    assert salon.admin.post("/api/gastos", json={**gasto, "metodo": "cheque"}).status_code == 400
    assert salon.admin.post("/api/gastos", json={**gasto, "id_sede": 999999}).status_code == 400
    assert salon.admin.post("/api/gastos", json=gasto).status_code == 201
    fila = crear.fila("SELECT id_sede, id_turno, fuente_dinero FROM gastos_caja")
    assert fila["id_sede"] == salon.sede and fila["id_turno"] is None and fila["fuente_dinero"] == "Caja Fuerte"
    # No mueve ninguna caja: no hay turno abierto y aun asi se registra.
    assert salon.cajero.get("/api/caja").get_json()["turno"] is None


def test_filtro_por_sede(app, crear):
    id_tienda, (centro, norte) = crear.tienda("completo", sedes=("Centro", "Norte"))
    admin_id = crear.usuario("dueno@chef.co", "Admin", id_tienda)
    crear.usuario("jefe@chef.co", "Admin", id_tienda, norte)
    from conftest import DB_NAME, _conectar

    conn = _conectar(database=DB_NAME, autocommit=True)
    c = conn.cursor()
    for sede, total in ((centro, 10000), (norte, 25000)):
        c.execute("INSERT INTO ventas (id_tienda, id_sede, id_cajero, total_final, metodo_pago) "
                  "VALUES (%s, %s, %s, %s, 'Efectivo')", (id_tienda, sede, admin_id, total))
    conn.close()
    assert _resumen(app, id_tienda, [centro, norte])["ventas"] == 35000
    assert _resumen(app, id_tienda, [norte])["ventas"] == 25000

    dueno = app.test_client()
    entrar(dueno, "dueno@chef.co")
    dueno.post("/seleccionar-sede", data={"id_sede": centro})
    assert "$35.000" in dueno.get("/reportes").get_data(as_text=True)
    assert "$10.000" in dueno.get(f"/reportes?sede={centro}").get_data(as_text=True)
    # Un Admin atado a una sede no ve las otras aunque las pida.
    jefe = app.test_client()
    entrar(jefe, "jefe@chef.co")
    pagina = jefe.get(f"/reportes?sede={centro}").get_data(as_text=True)
    assert "$25.000" in pagina and "$35.000" not in pagina


def test_cierres_y_exportes(salon, crear):
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 10000})
    assert salon.cajero.post("/api/caja/gastos", json={
        "concepto": "=HYPERLINK(\"x\")", "monto": 1000, "metodo": "efectivo"}).status_code == 201
    salon.cajero.post("/api/caja/cerrar", json={"monto_final_real": 8000})
    pagina = salon.admin.get("/reportes?pestana=cierres").get_data(as_text=True)
    assert "Faltan $1.000" in pagina

    r = salon.admin.get("/reportes/exportar/cierres")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "-1000" in r.get_data(as_text=True)
    gastos = salon.admin.get("/reportes/exportar/gastos").get_data(as_text=True)
    assert "'=HYPERLINK" in gastos
    assert salon.admin.get("/reportes/exportar/productos").status_code == 200
    assert salon.admin.get("/reportes/exportar/otra").status_code == 404
    assert salon.cajero.get("/reportes").status_code == 302


def test_periodos(app):
    from app.services import reportes_service

    with app.app_context():
        hoy = reportes_service.periodo("hoy")
        assert hoy["dias"] == 1 and hoy["clave"] == "hoy"
        assert reportes_service.periodo("rango", "2026-02-10", "2026-01-01")["clave"] == "hoy"  # al reves
        assert reportes_service.periodo("rango", "2020-01-01", "2026-01-01")["clave"] == "hoy"  # muy largo
        r = reportes_service.periodo("rango", "2026-01-15", "2026-03-10")
        assert r["clave"] == "rango" and r["dias"] == 55
        anterior = reportes_service.periodo("mes_anterior")
        assert anterior["inicio"].day == 1 and anterior["fin"].day == 1
