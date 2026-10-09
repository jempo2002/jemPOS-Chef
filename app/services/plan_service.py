"""Planes de suscripcion de Chef (tiendas.plan_id) y sus limites.

Fuente unica de precios, topes y funciones por plan. Los precios son los del
landing (index.html, seccion #planes), en COP por mes:

  Basico   $49.000  mesas, propina, caja y turnos. Una sola sede.
  Completo $69.000  + pantalla de cocina, cuenta dividida, recetas, multisede
  Cadena   $99.000  + factura electronica, multisede

Multisede (reglas de jempo, 2026-10-07; tope bajado a 2 el 2026-10-09, igual
en todos sus productos): solo Completo y Cadena, hasta MAX_SEDES sedes
activas (mas sedes = un futuro Plan Corporativo). El mismo Admin maneja todas
las sedes y el restaurante tiene hasta MAX_ADMINS Admin (1 en Basico). Cada sede
extra paga cada mes el 50 % del plan (Completo $34.500, Cadena $49.500) y,
una sola vez, el montaje de COSTO_MONTAJE_SEDE (capacitacion y levantamiento
de inventario inicial). La base lo refuerza con triggers
(migrations/2026-10-07_multisede_reglas.sql).

plan_id NULL (no deberia pasar: el Master siempre elige plan) se trata como
Basico, el plan mas restringido, para que un dato faltante nunca abra
funciones pagas.
"""
from __future__ import annotations

from functools import wraps
from urllib.parse import quote

from flask import flash, g, jsonify, redirect, url_for

WHATSAPP = "https://wa.me/573106152268"

PLAN_POR_DEFECTO = "basico"

MAX_SEDES = 2
MAX_ADMINS = 2
COSTO_MONTAJE_SEDE = 79000
# Mensualidad de cada sede extra como fraccion del precio del plan.
FRACCION_SEDE_EXTRA = 0.5

PLANES: dict[str, dict] = {
    "basico": {
        "nombre": "Básico",
        "precio": 49000,
        "usuarios_por_sede": 4,
        "funciones": frozenset(),
    },
    "completo": {
        "nombre": "Completo",
        "precio": 69000,
        "usuarios_por_sede": 8,
        "funciones": frozenset({"cocina", "cuenta_dividida", "recetas", "multisede"}),
    },
    "cadena": {
        "nombre": "Cadena",
        "precio": 99000,
        "usuarios_por_sede": 8,
        "funciones": frozenset({"cocina", "cuenta_dividida", "recetas", "multisede", "factura"}),
    },
}
for _plan in PLANES.values():
    _multisede = "multisede" in _plan["funciones"]
    _plan["max_sedes"] = MAX_SEDES if _multisede else 1
    _plan["max_admins"] = MAX_ADMINS if _multisede else 1
    # Pesos enteros: 69.000 -> 34.500, 99.000 -> 49.500.
    _plan["sede_extra"] = round(_plan["precio"] * FRACCION_SEDE_EXTRA) if _multisede else None
PLANES_VALIDOS = tuple(PLANES)

# Para el mensaje de "esta funcion no viene en tu plan".
NOMBRE_FUNCION = {
    "cocina": "La pantalla de cocina",
    "cuenta_dividida": "La cuenta dividida",
    "recetas": "Las recetas e inventario de ingredientes",
    "multisede": "Tener varias sedes",
    "factura": "La factura electrónica",
}

# Plan al que se sube desde cada uno y lo que gana: alimenta el aviso de upsell.
_SIGUIENTE = {
    "basico": ("completo", (
        "Pantalla de cocina",
        "Cuenta dividida",
        "Recetas e inventario de ingredientes",
        f"Hasta {MAX_SEDES} sedes y {MAX_ADMINS} administradores",
    )),
    "completo": ("cadena", (
        "Factura electrónica DIAN",
    )),
}


def plan_de(plan_id: str | None) -> dict:
    return PLANES.get(plan_id or "", PLANES[PLAN_POR_DEFECTO])


def normalizar_plan(plan_id: str | None) -> str:
    return plan_id if plan_id in PLANES else PLAN_POR_DEFECTO


def tiene_funcion(plan_id: str | None, funcion: str) -> bool:
    return funcion in plan_de(plan_id)["funciones"]


def tope_sedes(plan_id: str | None) -> int:
    """Maximo de sedes activas: 1 en Basico, MAX_SEDES con multisede."""
    return plan_de(plan_id)["max_sedes"]


def tope_admins(plan_id: str | None) -> int:
    """Admin activos del restaurante (manejan todas sus sedes)."""
    return plan_de(plan_id)["max_admins"]


def tope_usuarios(plan_id: str | None, sedes_activas: int) -> int:
    """Usuarios activos (sin contar Master) que admite el plan."""
    return plan_de(plan_id)["usuarios_por_sede"] * max(1, int(sedes_activas))


def sedes_extra(plan_id: str | None, sedes_activas: int) -> int:
    """Sedes por encima de la principal (la unica incluida en el precio)."""
    return max(0, int(sedes_activas) - 1)


def mensualidad(plan_id: str | None, sedes_activas: int) -> int:
    """Lo que paga el restaurante al mes: plan + 50 % del plan por sede extra.
    Completo con 2 sedes: 69.000 + 34.500 = 103.500."""
    plan = plan_de(plan_id)
    return plan["precio"] + sedes_extra(plan_id, sedes_activas) * (plan["sede_extra"] or 0)


def _mensaje(recurso: str, plan_id: str, tope: int) -> str:
    nombre = plan_de(plan_id)["nombre"]
    if recurso == "sedes":
        if tope >= MAX_SEDES:
            return (
                f"Llegaste al máximo de {MAX_SEDES} sedes. Para más sedes escríbenos: "
                "lo manejamos con un plan corporativo."
            )
        completo = PLANES["completo"]
        return (
            f"Tu Plan {nombre} es para una sola sede. Con el Plan Completo puedes tener "
            f"hasta {MAX_SEDES} (cada sede extra suma ${completo['sede_extra']:,} al mes "
            f"y un montaje único de ${COSTO_MONTAJE_SEDE:,}).".replace(",", ".")
        )
    if recurso == "admins":
        if tope >= MAX_ADMINS:
            return (
                f"Tu Plan {nombre} permite {MAX_ADMINS} administradores, y cada uno maneja "
                "todas las sedes. Es el máximo: escríbenos si necesitas más."
            )
        return (
            f"Tu Plan {nombre} permite 1 administrador. Con el Plan Completo puedes tener "
            f"{MAX_ADMINS}, y cada uno maneja todas las sedes."
        )
    base = f"Tu Plan {nombre} permite {tope} usuarios activos."
    siguiente = _SIGUIENTE.get(plan_id)
    if plan_id == "cadena":
        return f"{base} Abre otra sede o escríbenos si necesitas más."
    if siguiente:
        return f"{base} Para sumar más personas pásate al Plan {PLANES[siguiente[0]]['nombre']}."
    return base


class LimitePlanError(Exception):
    def __init__(self, recurso: str, plan_id: str, tope: int):
        self.recurso = recurso
        self.plan_id = plan_id
        super().__init__(_mensaje(recurso, plan_id, tope))

    def respuesta(self) -> tuple[dict, int]:
        """Cuerpo JSON + 403. `code` le dice al front que muestre el aviso de
        cambio de plan en vez del error normal."""
        actual = plan_de(self.plan_id)["nombre"]
        if self.recurso in ("sedes", "admins"):
            # Basico -> Completo; con multisede ya esta en el tope.
            destino = None if "multisede" in plan_de(self.plan_id)["funciones"] else "completo"
        else:
            destino = (_SIGUIENTE.get(self.plan_id) or (None,))[0]
        if destino:
            texto = f"Hola, tengo el Plan {actual} de jemPOS Chef y quiero pasarme al Plan {PLANES[destino]['nombre']}."
            cta = f"Pasarme al Plan {PLANES[destino]['nombre']}"
        else:
            falta = {"sedes": "más sedes", "admins": "más administradores"}.get(self.recurso, "más usuarios")
            texto = f"Hola, tengo el Plan {actual} de jemPOS Chef y necesito {falta}."
            cta = "Escribirnos por WhatsApp"
        return {
            "ok": False,
            "code": "limite_plan",
            "recurso": self.recurso,
            "plan": self.plan_id,
            "msg": str(self),
            "accion_url": WHATSAPP + "?text=" + quote(texto),
            "accion_texto": cta,
            "beneficios": list(_SIGUIENTE.get(self.plan_id, (None, ()))[1]),
        }, 403


def _contar_sedes(cur, id_tienda: int) -> int:
    cur.execute(
        "SELECT COUNT(*) AS n FROM sedes WHERE id_tienda = %s AND estado = 'Activa'",
        (id_tienda,),
    )
    return int(cur.fetchone()["n"])


def _contar_usuarios(cur, id_tienda: int) -> int:
    cur.execute(
        "SELECT COUNT(*) AS n FROM usuarios "
        "WHERE id_tienda = %s AND estado_activo = 1 AND rol <> 'Master'",
        (id_tienda,),
    )
    return int(cur.fetchone()["n"])


def _contar_admins(cur, id_tienda: int) -> int:
    cur.execute(
        "SELECT COUNT(*) AS n FROM usuarios WHERE id_tienda = %s AND estado_activo = 1 AND rol = 'Admin'",
        (id_tienda,),
    )
    return int(cur.fetchone()["n"])


def verificar_limite(cur, id_tienda: int, recurso: str) -> None:
    """Lanza LimitePlanError si agregar uno mas de `recurso` ('sedes',
    'usuarios' o 'admins') pasa el tope del plan.

    `cur` debe ser un cursor dictionary=True dentro de la transaccion del alta.
    El FOR UPDATE bloquea la fila de la tienda hasta el commit del llamador:
    dos altas simultaneas no pueden pasar las dos el conteo."""
    cur.execute("SELECT plan_id FROM tiendas WHERE id_tienda = %s FOR UPDATE", (id_tienda,))
    plan_id = normalizar_plan((cur.fetchone() or {}).get("plan_id"))
    sedes = _contar_sedes(cur, id_tienda)
    if recurso == "sedes":
        tope = tope_sedes(plan_id)
        if sedes >= tope:
            raise LimitePlanError("sedes", plan_id, tope)
        return
    if recurso == "usuarios":
        tope = tope_usuarios(plan_id, sedes)
        if _contar_usuarios(cur, id_tienda) >= tope:
            raise LimitePlanError("usuarios", plan_id, tope)
        return
    if recurso == "admins":
        tope = tope_admins(plan_id)
        if _contar_admins(cur, id_tienda) >= tope:
            raise LimitePlanError("admins", plan_id, tope)
        return
    raise ValueError(f"Recurso desconocido: {recurso}")


def verificar_cambio_plan(cur, id_tienda: int, plan_nuevo: str) -> None:
    """ValueError si el restaurante usa mas de lo que el plan nuevo permite.

    Bajar de Cadena con 3 sedes activas a Completo dejaria 2 sedes fuera de
    plan: primero hay que eliminarlas. Mismo FOR UPDATE que verificar_limite.
    """
    if plan_nuevo not in PLANES:
        raise ValueError("Plan invalido.")
    cur.execute("SELECT 1 FROM tiendas WHERE id_tienda = %s FOR UPDATE", (id_tienda,))
    cur.fetchone()
    sedes = _contar_sedes(cur, id_tienda)
    tope = tope_sedes(plan_nuevo)
    nombre = PLANES[plan_nuevo]["nombre"]
    if sedes > tope:
        raise ValueError(
            f"El restaurante tiene {sedes} sedes activas y el Plan {nombre} permite {tope}. "
            "Elimina las sedes de sobra antes de cambiar de plan."
        )
    usuarios = _contar_usuarios(cur, id_tienda)
    tope_u = tope_usuarios(plan_nuevo, sedes)
    if usuarios > tope_u:
        raise ValueError(
            f"El restaurante tiene {usuarios} usuarios activos y el Plan {nombre} permite {tope_u}. "
            "Desactiva usuarios antes de cambiar de plan."
        )
    admins = _contar_admins(cur, id_tienda)
    tope_a = tope_admins(plan_nuevo)
    if admins > tope_a:
        raise ValueError(
            f"El restaurante tiene {admins} administradores y el Plan {nombre} permite {tope_a}. "
            "Cambia el rol de los que sobran antes de cambiar de plan."
        )


def requiere_funcion(funcion: str):
    """Decorador para rutas de funciones pagas (ej. recetas). Va DESPUES de
    login_required, que deja el plan de la tienda en g.plan_id."""
    if funcion not in NOMBRE_FUNCION:
        raise ValueError(f"Funcion desconocida: {funcion}")

    def decorator(f):
        @wraps(f)
        def _inner(*args, **kwargs):
            plan_id = g.get("plan_id")
            if g.get("es_master") or tiene_funcion(plan_id, funcion):
                return f(*args, **kwargs)
            from app.utils.decorators import _is_api_request, log_seguridad

            log_seguridad("funcion_fuera_de_plan", funcion=funcion, plan=plan_id)
            msg = f"{NOMBRE_FUNCION[funcion]} no viene en tu Plan {plan_de(plan_id)['nombre']}."
            if _is_api_request():
                return jsonify({"ok": False, "code": "funcion_no_incluida", "funcion": funcion, "msg": msg}), 403
            flash(msg, "error")
            return redirect(url_for("core.inicio"))

        return _inner

    return decorator
