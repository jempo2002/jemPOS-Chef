"""security.py — cabeceras de seguridad y HTTPS forzado.

La llama create_app (app/__init__.py) al arrancar.

Hace dos cosas:

  1) Flask-Talisman: redirige HTTP -> HTTPS, manda HSTS y marca la cookie de
     sesion como Secure. Todo esto se apaga en desarrollo local, porque con
     force_https activo el servidor de pruebas en http://127.0.0.1:5000 entra en
     un bucle de redirecciones y la cookie Secure nunca se guarda.

  2) Cabeceras anti-cache en las respuestas autenticadas. Hace falta para que
     cerrar sesion sea real: `session.clear()` invalida la sesion en el
     servidor, pero si la pagina protegida quedo en el cache del historial, el
     boton "atras" del navegador la vuelve a pintar sin pedir credenciales.
     Con `no-store` el navegador tiene que volver a pedirla y el servidor
     redirige al login.
"""

from __future__ import annotations

import os

from flask import request, session
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix

# Valores de FLASK_ENV que significan "estoy en mi maquina".
_ENTORNOS_DEV = {"development", "dev", "local", "testing", "test"}

# Rutas publicas: su respuesta si se puede cachear (no depende de la sesion).
_PREFIJOS_PUBLICOS = ("/static/", "/favicon.ico", "/health")


def cerrar_sesion_publica() -> None:
    """Destruye la sesion en una ruta publica, conservando los flashes.

    Flask guarda los mensajes flash DENTRO de la sesion, en la clave `_flashes`.
    Un `session.clear()` pelado en GET /login borraria el "Contrasena
    incorrecta" que acaba de poner el POST antes de redirigir, y el usuario
    veria el formulario en blanco sin saber que fallo. Igual con el
    "Cuenta creada exitosamente" de registro y con los avisos de permisos.

    Se conserva solo esa clave: nada de identidad (id_usuario, rol, id_tienda)
    sobrevive.
    """
    flashes = session.get("_flashes")
    session.clear()
    if flashes:
        session["_flashes"] = flashes


def es_desarrollo() -> bool:
    """True cuando corremos en local y NO hay que forzar HTTPS.

    Por defecto (sin variables) se asume produccion: es el lado seguro del
    error. Para desarrollo hay que declararlo con FLASK_ENV=development.
    """
    entorno = str(os.getenv("FLASK_ENV") or "").strip().lower()
    if entorno in _ENTORNOS_DEV:
        return True
    for bandera in ("FLASK_DEBUG", "DEBUG"):
        valor = str(os.getenv(bandera) or "").strip().lower()
        if valor in {"1", "true", "yes", "on"}:
            return True
    return False


def init_security(app) -> None:
    """Aplica Talisman y las cabeceras anti-cache sobre una app Flask."""
    desarrollo = es_desarrollo()

    if not desarrollo:
        # Detras de un proxy que termina TLS, Flask ve la peticion interna como
        # http y genera URLs absolutas con ese esquema. Sin esto quedarian mal:
        # el <link rel="canonical">, las etiquetas og:url y sobre todo el enlace
        # del correo de recuperacion de contrasena, que llegaria como http://.
        # ProxyFix reescribe el esquema y el host desde X-Forwarded-*.
        # Solo en produccion: en local no hay proxy y confiar en esas cabeceras
        # permitiria falsificarlas desde el navegador.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Una instancia de Talisman POR app, no un singleton de modulo: Talisman
    # guarda su configuracion (y `self.app`) en atributos de la instancia, asi
    # que un segundo init_app sobre el mismo objeto pisaria los ajustes de la
    # primera app (los scripts de scripts/ crean varias en un proceso).
    Talisman().init_app(
        app,
        # En produccion: HTTP -> HTTPS + HSTS + cookie Secure.
        force_https=not desarrollo,
        force_https_permanent=not desarrollo,
        strict_transport_security=not desarrollo,
        strict_transport_security_max_age=31536000,  # 1 anio
        strict_transport_security_include_subdomains=True,
        session_cookie_secure=not desarrollo,
        session_cookie_http_only=True,
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
        # Las pantallas de Chef no usan CDN de scripts ni JS inline: todo sale
        # de /static. Solo Google Fonts (CSS + archivos de fuente).
        content_security_policy={
            "default-src": "'self'",
            "style-src": ["'self'", "https://fonts.googleapis.com"],
            "font-src": ["'self'", "https://fonts.gstatic.com"],
            "img-src": ["'self'", "data:"],
            "frame-ancestors": "'none'",
            "form-action": "'self'",
            "base-uri": "'self'",
        },
    )

    # Talisman solo pone Secure dentro de su before_request (perezoso). Se fija
    # aqui para que la config sea cierta desde el arranque.
    app.config["SESSION_COOKIE_SECURE"] = not desarrollo

    @app.after_request
    def _no_cachear_paginas_privadas(response):
        """Impide que el boton "atras" reviva una pagina de una sesion cerrada."""
        ruta = request.path or ""
        if ruta.startswith(_PREFIJOS_PUBLICOS):
            return response
        # Solo importa cuando la respuesta pudo depender de una sesion.
        if session or "id_usuario" in session:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.config["CHEF_DEV_MODE"] = desarrollo
