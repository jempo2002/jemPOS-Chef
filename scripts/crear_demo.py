"""Crea un restaurante de demostracion listo para probar mesas y comandas.

Plan Completo, sede Principal, 2 zonas con 10 mesas, una carta corta (cocina y
bar, con inventario en las bebidas), insumos con compras y recetas para
cuatro platos (uno pasa del 35 % de costo, para ver la alerta), tres
adicionales con receta (una salsa gratis y dos extras cobrados), dos
domiciliarios (Pedro y Luisa) y un usuario
por rol:

    admin@demo.chef    Admin   (configura, cobra, anula)
    mesero@demo.chef   Mesero  (toma pedidos y manda comandas)
    cocina@demo.chef   Cocina  (pantalla de cocina y bar)
    cajero@demo.chef   Cajero  (abre caja y cobra)

Todos con la misma contrasena, que se pide por consola (o DEMO_CLAVE).
Usa la base del .env. Se puede correr varias veces: si el restaurante "Demo
Chef" ya existe, crea los usuarios que falten, les pone a los cuatro la
contrasena dada (y los reactiva) y agrega insumos y recetas si aun no tiene.

    python scripts/crear_demo.py
"""
from __future__ import annotations

import getpass
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from app.services import (  # noqa: E402
    carta_service,
    domicilios_service,
    inventario_service,
    master_service,
    mesas_service,
    recetas_service,
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

# nombre, unidad base, minimo, compra (cantidad, unidad, precio total)
INSUMOS = [
    ("Arroz", "Gramo", 2000, (10, "kg", 38000)),
    ("Frijol", "Gramo", 1000, (5, "lb", 21000)),
    ("Carne de res", "Gramo", 2000, (5, "kg", 145000)),
    ("Pechuga de pollo", "Gramo", 2000, (4, "kg", 64000)),
    ("Chicharron", "Gramo", 1000, (2, "kg", 44000)),
    ("Huevo", "Unidad", 12, (30, "unidad", 15000)),
    ("Platano verde", "Unidad", 6, (20, "unidad", 16000)),
    ("Limon", "Unidad", 10, (50, "unidad", 10000)),
    ("Azucar", "Gramo", 500, (2, "kg", 7600)),
]

# plato -> [(insumo, cantidad en unidad base)]
RECETAS = {
    "Bandeja paisa": [("Arroz", 150), ("Frijol", 120), ("Carne de res", 150), ("Chicharron", 100), ("Huevo", 1),
                      ("Platano verde", 1)],
    "Churrasco": [("Carne de res", 350), ("Arroz", 100), ("Platano verde", 1)],
    "Arroz con pollo": [("Arroz", 200), ("Pechuga de pollo", 180)],
    "Limonada natural": [("Limon", 3), ("Azucar", 40)],
}

# adicionales: nombre, categoria, precio como adicional (0 = gratis), receta
ADICIONALES = [
    ("Salsa de la casa", "Salsas", 0, [("Huevo", 1), ("Limon", 1)]),
    ("Huevo frito", "Extras", 2000, [("Huevo", 1)]),
    ("Porcion de chicharron", "Extras", 6000, [("Chicharron", 100)]),
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
            cur.execute("SELECT id_tienda FROM tiendas WHERE nombre_negocio = %s AND estado_suscripcion <> 'eliminada' LIMIT 1", (NOMBRE,))
            existente = cur.fetchone()
        finally:
            conn.close()
        if existente:
            id_tienda, id_sede, id_admin = _ids(NOMBRE)
            asegurar_usuarios(id_tienda, id_sede, clave)
            print("Usuarios listos con la contrasena dada: " + ", ".join(_correos()))
            if sembrar_domiciliarios(id_tienda, id_sede):
                print(f'"{NOMBRE}": le agregué los domiciliarios {", ".join(DOMICILIARIOS)}.')
            recetas = sembrar_recetas(id_tienda, id_sede, id_admin)
            if recetas:
                print(f'"{NOMBRE}" ya existía: le agregué {len(INSUMOS)} insumos y {len(RECETAS)} recetas.')
            if sembrar_adicionales(id_tienda, id_sede):
                print(f'"{NOMBRE}": le agregué los adicionales {", ".join(a[0] for a in ADICIONALES)}.')
            elif not recetas:
                print(f'"{NOMBRE}" ya existe. Entra con admin@{DOMINIO}.')
            return 0

        id_tienda = master_service.crear_restaurante({
            "nombre_negocio": NOMBRE, "plan_id": "completo", "telefono": "3000000000",
            "sede_nombre": "Principal", "admin_nombre": "Admin Demo", "admin_cc": "1000000001",
            "admin_correo": f"admin@{DOMINIO}", "admin_password": clave,
        })
        _, id_sede, id_admin = _ids(NOMBRE)

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

        sembrar_recetas(id_tienda, id_sede, id_admin)
        sembrar_adicionales(id_tienda, id_sede)
        sembrar_domiciliarios(id_tienda, id_sede)

    print(f'Listo: "{NOMBRE}" con {sum(map(len, ZONAS.values()))} mesas, {len(CARTA)} productos y {len(RECETAS)} recetas.')
    print("Usuarios: " + ", ".join(_correos()))
    return 0


def _correos() -> list[str]:
    return [f"{p}@{DOMINIO}" for p in ["admin"] + [u[1] for u in USUARIOS]]


def asegurar_usuarios(id_tienda: int, id_sede: int, clave: str) -> None:
    """Crea los usuarios por rol que falten y deja a los cuatro activos y con
    la contrasena dada, hasheada igual que la app."""
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT correo FROM usuarios WHERE id_tienda = %s AND estado_activo = 1", (id_tienda,))
        existentes = {fila[0] for fila in cur.fetchall()}
    finally:
        conn.close()
    for nombre, prefijo, rol, cc in USUARIOS:
        if f"{prefijo}@{DOMINIO}" not in existentes:
            usuario_service.crear_usuario(id_tienda, {
                "nombre": nombre, "cc": cc, "rol": rol, "id_sede": id_sede,
                "correo": f"{prefijo}@{DOMINIO}", "password": clave, "confirm_password": clave,
            })
    correos = _correos()
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE usuarios SET clave_hash = %s, estado_activo = 1 "
            f"WHERE id_tienda = %s AND correo IN ({', '.join(['%s'] * len(correos))})",
            (generate_password_hash(clave), id_tienda, *correos),
        )
        conn.commit()
    finally:
        conn.close()


DOMICILIARIOS = ("Pedro", "Luisa")


def sembrar_domiciliarios(id_tienda: int, id_sede: int) -> bool:
    """Dos domiciliarios para probar despachos y recaudo, si no hay ninguno."""
    if domicilios_service.listar_domiciliarios(id_sede):
        return False
    for nombre in DOMICILIARIOS:
        domicilios_service.crear_domiciliario(id_tienda, id_sede, {"nombre": nombre})
    return True


def _ids(nombre: str) -> tuple[int, int, int]:
    """Tienda, sede principal y Admin del restaurante demo."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT t.id_tienda, s.id_sede, u.id_usuario FROM tiendas t "
            "JOIN sedes s ON s.id_tienda = t.id_tienda AND s.es_principal = 1 "
            "JOIN usuarios u ON u.id_tienda = t.id_tienda AND u.rol = 'Admin' "
            "WHERE t.nombre_negocio = %s AND t.estado_suscripcion <> 'eliminada' LIMIT 1",
            (nombre,),
        )
        fila = cur.fetchone()
    finally:
        conn.close()
    return fila["id_tienda"], fila["id_sede"], fila["id_usuario"]


def sembrar_recetas(id_tienda: int, id_sede: int, id_admin: int) -> bool:
    """Insumos con su compra inicial y las recetas de RECETAS. No hace nada si
    el restaurante ya tiene insumos."""
    if recetas_service.listar_insumos(id_tienda, id_sede):
        return False
    ids = {}
    for nombre, unidad, minimo, (cantidad, unidad_compra, precio) in INSUMOS:
        ids[nombre] = recetas_service.crear_insumo(
            id_tienda, {"nombre": nombre, "unidad_medida": unidad, "stock_minimo_alerta": minimo})
        recetas_service.registrar_movimiento_insumo(id_tienda, id_sede, id_admin, ids[nombre], {
            "tipo": "Entrada", "cantidad": cantidad, "unidad": unidad_compra, "precio_total": precio,
            "motivo": "Inventario inicial demo",
        })
    platos = {p["nombre"]: p["id_producto"] for p in carta_service.listar(id_tienda, id_sede)}
    for plato, lineas in RECETAS.items():
        if plato in platos:
            recetas_service.guardar_receta(id_tienda, platos[plato], {
                "lineas": [{"id_insumo": ids[i], "cantidad": q} for i, q in lineas]})
    return True


def sembrar_adicionales(id_tienda: int, id_sede: int) -> bool:
    """Salsas y extras para agregarle a los platos, con su receta. No hace
    nada si la carta ya tiene algun adicional."""
    if any(p["es_adicional"] for p in carta_service.listar(id_tienda, id_sede)):
        return False
    insumos = {i["nombre"]: i["id_insumo"] for i in recetas_service.listar_insumos(id_tienda, id_sede)}
    for nombre, categoria, precio, receta in ADICIONALES:
        id_producto = carta_service.crear(id_tienda, {
            "nombre": nombre, "categoria": categoria, "precio_venta": precio, "es_adicional": True})
        lineas = [{"id_insumo": insumos[i], "cantidad": q} for i, q in receta if i in insumos]
        if lineas:
            recetas_service.guardar_receta(id_tienda, id_producto, {"lineas": lineas})
    return True


if __name__ == "__main__":
    raise SystemExit(main())
