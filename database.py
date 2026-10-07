"""database.py — pool de conexiones MySQL para jemPOS Chef.

Uso:
    from database import init_pool, get_db

    # Al arrancar:
    init_pool(host, port, user, password, database)

    # En cada request:
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute('SELECT ...', (param,))
        rows = cur.fetchall()
    finally:
        conn.close()   # devuelve la conexion al pool
"""

from __future__ import annotations
import os
import time

import mysql.connector
from mysql.connector import pooling

_pool: pooling.MySQLConnectionPool | None = None


def init_pool(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    pool_size: int = 5,
    time_zone: str | None = None,
) -> None:
    """Crea el pool de conexiones. Llama esto una sola vez al iniciar la app."""
    global _pool
    # Zona de la sesion: NOW(), CURDATE() y las columnas timestamp salen en la
    # hora del negocio aunque el servidor MySQL este en UTC.
    extra = {"time_zone": time_zone} if time_zone else {}
    last_error: mysql.connector.Error | None = None
    for attempt in range(4):
        try:
            _pool = pooling.MySQLConnectionPool(
                pool_name="chef",
                pool_size=pool_size,
                pool_reset_session=True,
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                charset="utf8mb4",
                collation="utf8mb4_unicode_ci",
                autocommit=False,
                connection_timeout=10,
                **extra,
            )
            return
        except mysql.connector.Error as error:
            last_error = error
            if attempt < 3:
                time.sleep(1 << attempt)

    raise last_error


def init_pool_from_app(app) -> None:
    """Inicializa el pool usando app.config (patron application factory)."""
    from app.utils.helpers import ZONA_MYSQL

    # El pool no espera: si se agota lanza PoolError al instante. Cada hilo de
    # gunicorn usa una conexion a la vez, asi que basta con hilos + margen.
    # Total contra MySQL = workers x pool_size (4 x 6 = 24 con el Procfile).
    hilos = int(os.getenv("GUNICORN_THREADS") or 4)
    pool_size = min(32, int(os.getenv("DB_POOL_SIZE") or hilos + 2))

    init_pool(
        pool_size=pool_size,
        time_zone=ZONA_MYSQL,
        host=app.config["DB_HOST"],
        port=app.config["DB_PORT"],
        user=app.config["DB_USER"],
        password=app.config["DB_PASSWORD"],
        database=app.config["DB_NAME"],
    )


def get_db() -> mysql.connector.MySQLConnection:
    """Obtiene una conexion del pool. Siempre cierrala en un bloque finally."""
    if _pool is None:
        raise RuntimeError(
            "El pool de conexiones no esta inicializado. Llama init_pool() primero."
        )
    return _pool.get_connection()
