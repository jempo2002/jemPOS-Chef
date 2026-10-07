"""Crea un restaurante de demostracion listo para probar mesas y comandas.

Plan Completo, sede Principal, 2 zonas con 10 mesas, una carta corta (cocina y
bar, con inventario en las bebidas) y un usuario por rol:

    admin@demo.chef    Admin   (configura, cobra, anula)
    mesero@demo.chef   Mesero  (toma pedidos y manda comandas)
    cocina@demo.chef   Cocina  (pantalla de cocina y bar)
    cajero@demo.chef   Cajero  (abre caja y cobra)

Todos con la misma contrasena, que se pide por consola (o DEMO_CLAVE).
Usa la base del .env. Si el restaurante "Demo Chef" ya existe, no hace nada.

    python scripts/crear_demo.py
"""
from __future__ import annotations

import getpass
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from app import create_app  # noqa: E402
from app.services import (  # noqa: E402
    carta_service,
    inventario_service,
    master_service,
    mesas_service,
    usuario_service,
)
from app.services.auth_service import first_password_policy_error  # noqa: E402
from database import get_db  # noqa: E402

NOMBRE = "Demo Chef"
DOMINIO = "demo.chef"

ZONAS = {"Salon": ["Mesa 1", "Mesa 2", "Mesa 3", "Mesa 4", "Mesa 5", "Mesa 6"],
         "Terraza": ["Terraza 1", "Terraza 2", "Terraza 3", "Terraza 4"]}

# nombre, categoria, precio, estacion, stock inicial (None = sin inventario)
CARTA = [
    ("Bandeja paisa", "Platos fuertes", 28000, "cocina", None),
    ("Sancocho de gallina", "Platos fuertes", 22000, "cocina", None),
    ("Churrasco", "Platos fuertes", 32000, "cocina", None),
    ("Arroz con pollo", "Platos fuertes", 18000, "cocina", None),
    ("Empanadas x3", "Entradas", 7000, "cocina", None),
    ("Patacon con hogao", "Entradas", 9000, "cocina", None),
    ("Limonada natural", "Bebidas", 6000, "bar", None),
    ("Jugo de mora", "Bebidas", 6500, "bar", None),
    ("Gaseosa 400 ml", "Bebidas", 4000, "ninguna", 48),
    ("Cerveza", "Bebidas", 5000, "ninguna", 60),
    ("Agua 600 ml", "Bebidas", 3000, "ninguna", 24),
]

USUARIOS = [("Mesero Demo", "mesero", "Mesero", "1000000002"),
            ("Cocina Demo", "cocina", "Cocina", "1000000003"),
            ("Cajero Demo", "cajero", "Cajero", "1000000004")]


def main() -> int:
    clave = os.environ.get("DEMO_CLAVE") or getpass.getpass("Contrasena para los usuarios demo: ")
    error = first_password_policy_error(clave)
    if error:
        print(error)
        return 2

    app = create_app()
    with app.app_context():
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM tiendas WHERE nombre_negocio = %s AND estado_suscripcion <> 'eliminada' LIMIT 1", (NOMBRE,))
            if cur.fetchone():
                print(f'"{NOMBRE}" ya existe. Entra con admin@{DOMINIO}.')
                return 0
        finally:
            conn.close()

        id_tienda = master_service.crear_restaurante({
            "nombre_negocio": NOMBRE, "plan_id": "completo", "telefono": "3000000000",
            "sede_nombre": "Principal", "admin_nombre": "Admin Demo", "admin_cc": "1000000001",
            "admin_correo": f"admin@{DOMINIO}", "admin_password": clave,
        })
        conn = get_db()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT id_sede FROM sedes WHERE id_tienda = %s AND es_principal = 1", (id_tienda,))
            id_sede = cur.fetchone()["id_sede"]
            cur.execute("SELECT id_usuario FROM usuarios WHERE id_tienda = %s AND rol = 'Admin'", (id_tienda,))
            id_admin = cur.fetchone()["id_usuario"]
        finally:
            conn.close()

        for nombre, prefijo, rol, cc in USUARIOS:
            usuario_service.crear_usuario(id_tienda, {
                "nombre": nombre, "cc": cc, "rol": rol, "id_sede": id_sede,
                "correo": f"{prefijo}@{DOMINIO}", "password": clave, "confirm_password": clave,
            })

        for zona, mesas in ZONAS.items():
            id_zona = mesas_service.crear_zona(id_tienda, id_sede, {"nombre": zona})
            for mesa in mesas:
                mesas_service.crear_mesa(id_tienda, id_sede, {"nombre": mesa, "capacidad": 4, "id_zona": id_zona})

        for nombre, categoria, precio, estacion, stock in CARTA:
            id_producto = carta_service.crear(id_tienda, {
                "nombre": nombre, "categoria": categoria, "precio_venta": precio,
                "estacion": estacion, "controla_stock": stock is not None,
            })
            if stock:
                inventario_service.registrar_movimiento(
                    id_tienda, id_sede, id_admin, id_producto,
                    {"tipo": "Entrada", "cantidad": stock, "motivo": "Inventario inicial demo"},
                )

    print(f'Listo: "{NOMBRE}" con {sum(map(len, ZONAS.values()))} mesas y {len(CARTA)} productos.')
    print("Usuarios: " + ", ".join(f"{p}@{DOMINIO}" for p in ["admin"] + [u[1] for u in USUARIOS]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
