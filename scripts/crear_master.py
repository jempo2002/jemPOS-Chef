"""Crea (o reactiva) el usuario Master que administra los restaurantes.

Sin registro publico: el Master es quien crea cada restaurante desde
/panel-master. Toma las credenciales de la base del .env.

    python scripts/crear_master.py "Nombre" correo@dominio.com

Pide la contrasena por consola (no queda en el historial del shell).
"""
from __future__ import annotations

import getpass
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

import mysql.connector  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from app.services.auth_service import first_password_policy_error, is_valid_email  # noqa: E402


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    nombre, correo = sys.argv[1].strip(), sys.argv[2].strip().lower()
    if not nombre or not is_valid_email(correo):
        print("Nombre o correo invalido.")
        return 2
    clave = getpass.getpass("Contrasena: ")
    error = first_password_policy_error(clave)
    if error:
        print(error)
        return 2
    if clave != getpass.getpass("Repitela: "):
        print("Las contrasenas no coinciden.")
        return 2

    load_dotenv(os.path.join(RAIZ, ".env"))
    conn = mysql.connector.connect(
        host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD", ""), database=os.environ["DB_NAME"],
    )
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO usuarios (id_tienda, nombre_completo, correo, clave_hash, rol) "
            "VALUES (NULL, %s, %s, %s, 'Master') "
            "ON DUPLICATE KEY UPDATE nombre_completo = VALUES(nombre_completo), "
            "clave_hash = VALUES(clave_hash), rol = 'Master', id_tienda = NULL, estado_activo = 1",
            (nombre, correo, generate_password_hash(clave)),
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Master listo: {correo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
