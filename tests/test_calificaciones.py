"""Calificacion del servicio: enlace y QR por pedido cobrado, pagina publica
del cliente, una sola vez, vencimiento, ocultar y reportes."""
import uuid
from datetime import timedelta

from conftest import entrar
from test_dividida_llevar import _mesa_lista
from test_salon import salon  # noqa: F401  (fixture)


def _cobrada(salon):
    """Mesa cobrada; devuelve el id del pedido."""
    detalle = _mesa_lista(salon, (salon.burger, 1))
    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json={"metodo": "efectivo", "propina": 0})
    assert r.status_code == 200, r.get_json()
    return detalle["pedido"]["id_pedido"]


def _enlace(cliente, id_pedido):
    r = cliente.post(f"/api/pedidos/{id_pedido}/calificacion")
    assert r.status_code == 200, r.get_json()
    datos = r.get_json()
    return datos, datos["url"].split("/calificar/")[1]


def test_cliente_califica_una_sola_vez(app, salon, crear):
    id_pedido = _cobrada(salon)
    datos, token = _enlace(salon.cajero, id_pedido)
    assert datos["qr"].startswith("data:image/svg+xml") and datos["lugar"] == "Mesa M1"
    assert datos["whatsapp"] == "" and datos["calificada"] is False
    # Pedirlo otra vez (otra pantalla, doble toque) da el mismo enlace.
    assert _enlace(salon.mesero, id_pedido)[1] == token

    publico = app.test_client()  # sin sesion
    pagina = publico.get(f"/calificar/{token}")
    assert pagina.status_code == 200 and "¿Cómo te fue?" in pagina.get_data(as_text=True)
    assert "Restaurante" in pagina.get_data(as_text=True)

    r = publico.post(f"/calificar/{token}", data={"comida": "6", "atencion": "4"})
    assert "Elige de 1 a 5 estrellas para la comida." in r.get_data(as_text=True)
    r = publico.post(f"/calificar/{token}", data={"comida": "5", "atencion": "4", "comentario": " <b>Muy rico</b> "})
    assert r.status_code == 200 and "¡Gracias por calificarnos!" in r.get_data(as_text=True)
    fila = crear.fila("SELECT comida, atencion, comentario, id_mesero, calificada_en FROM calificaciones")
    assert (fila["comida"], fila["atencion"], fila["comentario"]) == (5, 4, "<b>Muy rico</b>")
    assert fila["id_mesero"] == crear.fila("SELECT id_usuario FROM usuarios WHERE rol = 'Mesero'")["id_usuario"]

    # Segunda vez: no cambia; el cliente ve lo que ya califico.
    r = publico.post(f"/calificar/{token}", data={"comida": "1", "atencion": "1"})
    assert "¡Gracias por calificarnos!" in r.get_data(as_text=True)
    from app.services import calificaciones_service
    from app.services.errores import Conflicto
    with app.app_context():
        try:
            calificaciones_service.calificar(token, {"comida": "1", "atencion": "1"})
            raise AssertionError("debio fallar")
        except Conflicto as exc:
            assert "ya fue calificado" in str(exc)
    assert crear.fila("SELECT comida FROM calificaciones")["comida"] == 5
    assert _enlace(salon.cajero, id_pedido)[0]["calificada"] is True


def test_enlace_invalido_vencido_y_pedido_sin_cobrar(app, salon, crear, db):
    publico = app.test_client()
    assert publico.get("/calificar/no-existe").status_code == 404
    assert publico.get("/calificar/" + "a" * 32).status_code == 404

    # Pedido abierto: todavia no hay QR.
    _mesa_lista(salon, (salon.burger, 1))
    abierto = crear.fila("SELECT id_pedido FROM pedidos")["id_pedido"]
    assert salon.cajero.post(f"/api/pedidos/{abierto}/calificacion").status_code == 409
    assert salon.cocina.post(f"/api/pedidos/{abierto}/calificacion").status_code == 403

    r = salon.cajero.post(f"/api/mesas/{salon.mesa}/cobrar", json={"metodo": "efectivo"})
    assert r.status_code == 200
    _, token = _enlace(salon.cajero, abierto)
    # Cobrado hace 4 dias: el enlace vencio.
    db.cursor().execute("UPDATE pedidos SET cerrado_en = cerrado_en - INTERVAL 4 DAY WHERE id_pedido = %s", (abierto,))
    assert "ya venció" in publico.get(f"/calificar/{token}").get_data(as_text=True)
    r = publico.post(f"/calificar/{token}", data={"comida": "5", "atencion": "5"})
    assert "ya venció" in r.get_data(as_text=True)
    assert crear.fila("SELECT calificada_en FROM calificaciones")["calificada_en"] is None
    assert salon.cajero.post(f"/api/pedidos/{abierto}/calificacion").status_code == 409


def test_otra_sede_no_pide_el_qr(app, salon, crear):
    id_pedido = _cobrada(salon)
    otra = crear.sede(salon.id_tienda, "Norte")
    crear.usuario("cajero2@chef.co", "Cajero", salon.id_tienda, otra)
    cliente = app.test_client()
    entrar(cliente, "cajero2@chef.co")
    assert cliente.post(f"/api/pedidos/{id_pedido}/calificacion").status_code == 404


def test_para_llevar_con_telefono_ofrece_whatsapp(salon, crear):
    ref = f"/api/llevar/{uuid.uuid4()}"
    salon.cajero.post("/api/caja/abrir", json={"monto_inicial": 0})
    r = salon.mesero.post(f"{ref}/items", json={
        "cliente": "Marta", "telefono": "310 555 1234",
        "items": [{"id_producto": salon.burger, "cantidad": 1, "uuid": str(uuid.uuid4())}]})
    id_pedido = r.get_json()["pedido"]["id_pedido"]
    assert salon.mesero.post(f"{ref}/comanda").status_code == 200
    assert salon.cajero.post(f"{ref}/cobrar", json={"metodo": "efectivo"}).status_code == 200
    datos, _ = _enlace(salon.cajero, id_pedido)
    assert datos["lugar"] == "Para llevar #1"
    assert datos["whatsapp"].startswith("https://wa.me/573105551234?text=")


def test_reportes_promedios_ocultar_y_ajuste(app, salon, crear):
    id_pedido = _cobrada(salon)
    _, token = _enlace(salon.cajero, id_pedido)
    publico = app.test_client()
    publico.post(f"/calificar/{token}", data={"comida": "2", "atencion": "4", "comentario": "Llegó fría"})

    # Otra mesa, otro pedido.
    salon.mesero.post(f"/api/mesas/{salon.otra}/items", json={"items": [
        {"id_producto": salon.gaseosa, "cantidad": 1, "uuid": str(uuid.uuid4())}]})
    salon.mesero.post(f"/api/mesas/{salon.otra}/comanda")
    assert salon.cajero.post(f"/api/mesas/{salon.otra}/cobrar", json={"metodo": "efectivo"}).status_code == 200
    otro = crear.fila("SELECT id_pedido FROM pedidos WHERE id_mesa = %s", (salon.otra,))["id_pedido"]
    _, token2 = _enlace(salon.cajero, otro)
    publico.post(f"/calificar/{token2}", data={"comida": "4", "atencion": "5"})

    from app.services import calificaciones_service, reportes_service

    with app.app_context():
        per = reportes_service.periodo("hoy")
        c = calificaciones_service.resumen(salon.id_tienda, [salon.sede], per)
    assert c["n"] == 2 and c["comida"] == 3.0 and c["atencion"] == 4.5 and c["comentarios"] == 1
    assert c["cobrados"] == 2 and c["tasa"] == 100.0
    assert {e["estrellas"]: e["n"] for e in c["reparto_comida"]} == {5: 0, 4: 1, 3: 0, 2: 1, 1: 0}
    assert c["meseros"][0]["n"] == 2

    pagina = salon.admin.get("/reportes?pestana=calificaciones").get_data(as_text=True)
    assert "Llegó fría" in pagina and "Por mesero" in pagina
    bajas = salon.admin.get("/reportes?pestana=calificaciones&ver=bajas").get_data(as_text=True)
    assert "Llegó fría" in bajas and "1 calificación" in bajas
    csv = salon.admin.get("/reportes/exportar/calificaciones").get_data(as_text=True)
    assert "Llegó fría" in csv
    assert salon.cajero.get("/reportes?pestana=calificaciones").status_code in (302, 403)

    id_calif = crear.fila("SELECT id_calificacion FROM calificaciones WHERE comida = 2")["id_calificacion"]
    assert salon.cajero.post(f"/api/calificaciones/{id_calif}/ocultar", json={"motivo": "x"}).status_code == 403
    assert salon.admin.post(f"/api/calificaciones/{id_calif}/ocultar", json={}).status_code == 400
    assert salon.admin.post(f"/api/calificaciones/{id_calif}/ocultar", json={"motivo": "Prueba"}).status_code == 200
    assert salon.admin.post(f"/api/calificaciones/{id_calif}/ocultar", json={"motivo": "Prueba"}).status_code == 409
    with app.app_context():
        c = calificaciones_service.resumen(salon.id_tienda, [salon.sede], per)
    assert c["n"] == 1 and c["comida"] == 4.0
    assert crear.fila("SELECT COUNT(*) AS n FROM calificaciones")["n"] == 2  # nunca se borra

    # Apagar el QR al cobrar.
    assert 'data-calificar="1"' in salon.cajero.get(f"/mesas/{salon.mesa}").get_data(as_text=True)
    assert salon.admin.post("/api/calificaciones/ajustes", json={}).status_code == 200
    assert 'data-calificar="0"' in salon.cajero.get(f"/mesas/{salon.mesa}").get_data(as_text=True)
    assert salon.admin.post("/api/calificaciones/ajustes", json={"pedir_al_cobrar": "on"}).status_code == 200
    assert 'data-calificar="1"' in salon.cajero.get(f"/mesas/{salon.mesa}").get_data(as_text=True)


def test_vigencia_en_dias(app):
    from app.services import calificaciones_service
    from app.utils.helpers import ahora_local

    assert calificaciones_service._vigente(ahora_local() - timedelta(days=2))
    assert not calificaciones_service._vigente(ahora_local() - timedelta(days=3, minutes=1))
    assert not calificaciones_service._vigente(None)
