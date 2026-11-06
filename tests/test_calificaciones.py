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


GOOGLE = "https://g.page/r/CabcDEF123/review"


def test_enlace_de_google_solo_a_dominios_de_google(app):
    from app.services.calificaciones_service import limpiar_url_google

    assert limpiar_url_google("") is None and limpiar_url_google("  ") is None
    assert limpiar_url_google(GOOGLE) == GOOGLE
    assert limpiar_url_google("g.page/r/CabcDEF123/review") == GOOGLE
    ok = "https://search.google.com/local/writereview?placeid=ChIJ123"
    assert limpiar_url_google(ok) == ok
    assert limpiar_url_google("https://maps.app.goo.gl/AbC123") == "https://maps.app.goo.gl/AbC123"
    for malo in ("http://g.page/r/x/review", "https://g.page.evil.co/r", "https://evilgoogle.com/x",
                 "https://user@google.com/x", "https://google.com:8443/x", "javascript:alert(1)",
                 'https://g.page/r/x"onclick=1', "https://g.page/" + "a" * 300):
        try:
            limpiar_url_google(malo)
            raise AssertionError(malo)
        except ValueError:
            pass


def _calificada(app, salon, comida, atencion, comentario=""):
    id_pedido = _cobrada(salon)
    _, token = _enlace(salon.cajero, id_pedido)
    publico = app.test_client()
    r = publico.post(f"/calificar/{token}", data={"comida": comida, "atencion": atencion, "comentario": comentario})
    return publico, token, r.get_data(as_text=True)


def test_buena_invita_a_google_y_cuenta_la_visita(app, salon, crear):
    # Sin enlace de Google: solo las gracias.
    r = salon.admin.post("/api/calificaciones/google", json={"id_sede": salon.sede, "url": "https://otro.com/x"})
    assert r.status_code == 400
    assert salon.cajero.post("/api/calificaciones/google", json={"id_sede": salon.sede, "url": GOOGLE}).status_code == 403
    assert salon.admin.post("/api/calificaciones/google", json={"id_sede": salon.sede, "url": GOOGLE}).status_code == 200

    publico, token, html = _calificada(app, salon, "5", "4", "Las hamburguesas, brutales")
    assert "Dejar mi reseña en Google" in html and "brutales" in html
    r = publico.get(f"/calificar/{token}/google")
    assert r.status_code == 302 and r.headers["Location"] == GOOGLE
    primera = crear.fila("SELECT google_en FROM calificaciones")["google_en"]
    assert primera is not None
    publico.get(f"/calificar/{token}/google")  # otra vez: no cambia la primera fecha
    assert crear.fila("SELECT google_en FROM calificaciones")["google_en"] == primera

    from app.services import calificaciones_service, reportes_service
    with app.app_context():
        c = calificaciones_service.resumen(salon.id_tienda, [salon.sede], reportes_service.periodo("hoy"))
    assert c["buenas"] == 1 and c["a_google"] == 1
    assert "Fue a Google" in salon.admin.get("/reportes?pestana=calificaciones").get_data(as_text=True)

    # Token falso o sin calificar: no lleva a Google.
    assert publico.get("/calificar/" + "b" * 32 + "/google").headers["Location"].endswith("/calificar/" + "b" * 32)

    # Quitar el enlace: ya no hay boton.
    assert salon.admin.post("/api/calificaciones/google", json={"id_sede": salon.sede, "url": ""}).status_code == 200
    assert "Dejar mi reseña en Google" not in publico.get(f"/calificar/{token}").get_data(as_text=True)


def test_mala_llega_al_dueno_y_se_atiende(app, salon, crear, db):
    salon.admin.post("/api/calificaciones/google", json={"id_sede": salon.sede, "url": GOOGLE})
    publico, token, html = _calificada(app, salon, "5", "3", "Nos atendieron tarde")
    db.cursor().execute("UPDATE pedidos SET cliente_nombre = 'Marta Ruiz', cliente_telefono = '3105551234'")
    assert "Dejar mi reseña en Google" not in html
    assert "le llegó directo al dueño" in html and "opinar en Google" in html

    inicio = salon.admin.get("/inicio").get_data(as_text=True)
    assert "Un cliente calificó mal su visita" in inicio
    assert "calificó mal" not in salon.cajero.get("/inicio").get_data(as_text=True)

    pagina = salon.admin.get("/reportes?pestana=calificaciones").get_data(as_text=True)
    assert "Nos atendieron tarde" in pagina and "https://wa.me/573105551234?text=Hola%20Marta" in pagina

    id_calif = crear.fila("SELECT id_calificacion FROM calificaciones")["id_calificacion"]
    url = f"/api/calificaciones/{id_calif}/atendida"
    assert salon.cajero.post(url, json={}).status_code == 403
    assert salon.admin.post(url, json={"nota": "Le dimos un postre"}).status_code == 200
    assert salon.admin.post(url, json={}).status_code == 409
    assert "calificó mal" not in salon.admin.get("/inicio").get_data(as_text=True)
    pagina = salon.admin.get("/reportes?pestana=calificaciones").get_data(as_text=True)
    assert "Nada pendiente" in pagina and "Le dimos un postre" in pagina

    # Otro restaurante no la alcanza.
    otra, sedes_otra = crear.tienda(nombre="Otro")
    crear.usuario("admin2@chef.co", "Admin", otra, None)
    cliente = app.test_client()
    entrar(cliente, "admin2@chef.co")
    assert cliente.post(url, json={}).status_code in (404, 409)
