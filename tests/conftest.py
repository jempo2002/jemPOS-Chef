"""Pruebas contra una base MariaDB/MySQL real y, si hay, un Redis real.

    TEST_DB_HOST=127.0.0.1 TEST_DB_USER=root TEST_REDIS_URL=redis://localhost:6379/15 pytest

La base TEST_DB_NAME (por defecto chef_pytest) se BORRA y se crea de nuevo
con db/schema.sql + migrations/. Sin base disponible, las pruebas que la
necesitan se saltan; las de plan_service corren igual.
"""
from __future__ import annotations

import glob
import os
import sys

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

DB = {
    "host": os.getenv("TEST_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("TEST_DB_PORT", "3306")),
    "user": os.getenv("TEST_DB_USER", "root"),
    "password": os.getenv("TEST_DB_PASSWORD", ""),
}
DB_NAME = os.getenv("TEST_DB_NAME", "chef_pytest")
CLAVE = "Clave123"

os.environ.update(
    FLASK_ENV="testing",
    SECRET_KEY="clave-de-pruebas",
    DB_HOST=DB["host"],
    DB_PORT=str(DB["port"]),
    DB_USER=DB["user"],
    DB_PASSWORD=DB["password"],
    DB_NAME=DB_NAME,
    REDIS_URL=os.getenv("TEST_REDIS_URL", ""),
)


def _conectar(**extra):
    import mysql.connector

    return mysql.connector.connect(**DB, **extra)


@pytest.fixture(scope="session")
def base():
    try:
        conn = _conectar()
    except Exception as exc:  # sin servidor: se saltan
        pytest.skip(f"Sin base de pruebas: {exc}")
    from scripts.run_migration import _sentencias

    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{DB_NAME}`")
    cur.execute(f"CREATE DATABASE `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    cur.execute(f"USE `{DB_NAME}`")
    archivos = [os.path.join(RAIZ, "db", "schema.sql")] + sorted(glob.glob(os.path.join(RAIZ, "migrations", "*.sql")))
    for ruta in archivos:
        with open(ruta, encoding="utf-8") as fh:
            for sentencia in _sentencias(fh.read()):
                cur.execute(sentencia)
                if cur.with_rows:
                    cur.fetchall()
    conn.commit()
    conn.close()
    return True


@pytest.fixture
def db(base):
    """Conexion a la base de pruebas, vacia al empezar cada prueba."""
    conn = _conectar(database=DB_NAME, autocommit=True)
    cur = conn.cursor()
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for tabla in (
        "usuarios", "sedes", "tiendas", "auditoria", "categorias", "productos", "stock_sedes",
        "movimientos_inventario", "turnos_caja", "ventas", "detalle_ventas", "consecutivos", "zonas",
        "mesas", "pedidos", "pedido_items", "comandas", "mermas", "insumos", "stock_insumos_sedes",
        "recetas_productos", "pedido_cuentas", "pedido_cuenta_items", "gastos_caja", "domiciliarios",
        "pedido_domicilios", "calificaciones",
    ):
        cur.execute(f"TRUNCATE `{tabla}`")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    yield conn
    conn.close()


@pytest.fixture
def app(db):
    from app import create_app, limiter

    aplicacion = create_app()
    aplicacion.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    limiter.reset()
    return aplicacion


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def crear(db):
    """Atajos para sembrar datos con SQL directo."""
    from werkzeug.security import generate_password_hash

    hash_clave = generate_password_hash(CLAVE)
    cur = db.cursor()

    class Sembrar:
        def tienda(self, plan="completo", nombre="Restaurante", sedes=("Principal",)):
            cur.execute(
                "INSERT INTO tiendas (nombre_negocio, plan_id, es_restaurante) VALUES (%s, %s, 1)",
                (nombre, plan),
            )
            id_tienda = cur.lastrowid
            ids = [self.sede(id_tienda, n, principal=(i == 0)) for i, n in enumerate(sedes)]
            return id_tienda, ids

        def sede(self, id_tienda, nombre, principal=False):
            cur.execute(
                "INSERT INTO sedes (id_tienda, nombre, es_principal) VALUES (%s, %s, %s)",
                (id_tienda, nombre, int(principal)),
            )
            return cur.lastrowid

        def usuario(self, correo, rol="Admin", id_tienda=None, id_sede=None):
            cur.execute(
                "INSERT INTO usuarios (id_tienda, id_sede, nombre_completo, correo, clave_hash, rol) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (id_tienda, id_sede, correo.split("@")[0].title(), correo, hash_clave, rol),
            )
            return cur.lastrowid

        def fila(self, sql, params=()):
            c = db.cursor(dictionary=True)
            c.execute(sql, params)
            return c.fetchone()

    return Sembrar()


def entrar(client, correo, clave=CLAVE):
    return client.post("/login", data={"correo": correo, "contrasena": clave})
