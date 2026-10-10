"""Gastos del restaurante: categorias, los que registra el Admin fuera de la
caja y la anulacion (borrado suave).

Dos caminos para un gasto:
  - Caja (caja_service.registrar_gasto): lo paga el cajero durante el turno.
    El efectivo sale del cajon y baja lo que debe haber al cerrar.
  - Reportes > Gastos (registrar): el Admin anota lo que no pasa por el
    cajon (arriendo por transferencia, nomina desde la caja fuerte). No toca
    ningun turno.

Un gasto nunca se borra: se anula con motivo. Si era efectivo de un turno
que sigue abierto, ese efectivo vuelve a lo que debe haber en la caja; si el
turno ya cerro, su arqueo queda como se hizo.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.errores import Conflicto, NoEncontrado
from app.utils.helpers import ahora_local
from app.utils.validation import parse_float, parse_int, sanitize_optional_text, sanitize_text
from database import get_db

MONTO_MAX = 100_000_000

# Clave (gastos_caja.categoria) -> nombre en pantalla. En este orden salen
# en los formularios.
CATEGORIAS = {
    "insumos": "Compras de insumos y mercancía",
    "nomina": "Nómina y turnos",
    "arriendo": "Arriendo",
    "servicios": "Servicios públicos e internet",
    "transporte": "Domicilios y transporte",
    "mantenimiento": "Mantenimiento y aseo",
    "impuestos": "Impuestos y trámites",
    "publicidad": "Publicidad",
    "otros": "Otros",
}
# Las compras se vuelven costo cuando el plato se vende (recetas o costo de
# la carta): restarlas tambien como gasto contaria dos veces lo mismo.
CATEGORIAS_NO_OPERATIVAS = frozenset({"insumos"})

# Lo que manda la pantalla de Reportes -> (metodo_pago, fuente_dinero)
METODOS_ADMIN = {
    "transferencia": ("Nequi/Daviplata", "Bancos"),
    "nequi": ("Nequi/Daviplata", "Bancos"),
    "tarjeta": ("Tarjeta", "Bancos"),
    "efectivo": ("Efectivo", "Caja Fuerte"),
}


def categoria_valida(valor) -> str:
    clave = str(valor or "").strip().lower() or "otros"
    if clave not in CATEGORIAS:
        raise ValueError("Categoría de gasto invalida.")
    return clave


def _auditar(cur, id_tienda: int, id_usuario: int, accion: str, detalles: str) -> None:
    cur.execute(
        "INSERT INTO auditoria (id_tienda, id_usuario, accion, detalles) VALUES (%s, %s, %s, %s)",
        (id_tienda, id_usuario, accion, detalles[:2000]),
    )


def registrar(id_tienda: int, id_usuario: int, id_sede: int, data: dict) -> int:
    """Gasto que no sale del cajon. `id_sede` ya validada por quien llama.
    La fecha es siempre la de ahora: el trigger bi_gastos_caja_fecha_creacion
    no deja fechar gastos hacia atras."""
    concepto = sanitize_text(data.get("concepto"), "El concepto", max_len=150)
    descripcion = sanitize_optional_text(data.get("descripcion"), "La nota", max_len=255)
    categoria = categoria_valida(data.get("categoria"))
    monto = Decimal(str(round(parse_float(data.get("monto"), "El monto", min_value=0, max_value=MONTO_MAX,
                                          allow_zero=False), 2)))
    metodo = METODOS_ADMIN.get(str(data.get("metodo") or "").strip().lower())
    if not metodo:
        raise ValueError("Elige cómo se pagó.")
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "INSERT INTO gastos_caja (id_tienda, id_sede, id_turno, id_usuario, concepto, categoria, descripcion, "
            "monto, monto_transferencia, metodo_pago, fuente_dinero) "
            "VALUES (%s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)",
            (id_tienda, id_sede, id_usuario, concepto, categoria, descripcion, monto,
             monto if metodo[1] == "Bancos" else 0, metodo[0], metodo[1]),
        )
        id_gasto = cur.lastrowid
        _auditar(cur, id_tienda, id_usuario, "registrar_gasto",
                 f"Gasto id={id_gasto}, sede={id_sede}, categoria={categoria}, monto={monto}, metodo={metodo[0]}")
        conn.commit()
        return id_gasto
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def anular(id_tienda: int, id_usuario: int, id_gasto: int, data: dict, sedes_permitidas: list[int] | None) -> dict:
    """Anula un gasto con motivo. Devuelve cuanto efectivo volvio a la caja
    abierta (0 si no aplica)."""
    id_gasto = parse_int(id_gasto, "Gasto", min_value=1)
    motivo = sanitize_text(data.get("motivo"), "El motivo", max_len=255)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT g.id_gasto, g.id_sede, g.id_turno, g.monto, g.monto_transferencia, g.fuente_dinero, "
            "g.estado_activo, g.concepto, t.estado_turno "
            "FROM gastos_caja g LEFT JOIN turnos_caja t ON t.id_turno = g.id_turno "
            "WHERE g.id_gasto = %s AND g.id_tienda = %s FOR UPDATE",
            (id_gasto, id_tienda),
        )
        gasto = cur.fetchone()
        if not gasto or (sedes_permitidas is not None and gasto["id_sede"] not in sedes_permitidas):
            raise NoEncontrado("Ese gasto no existe.")
        if not gasto["estado_activo"]:
            raise Conflicto("Ese gasto ya estaba anulado.")
        devuelto = Decimal(0)
        if gasto["id_turno"] and gasto["fuente_dinero"] != "Bancos" and gasto["estado_turno"] == "Abierto":
            # Mismo calculo que caja_service._resumen: lo que salio del cajon.
            devuelto = Decimal(gasto["monto"]) - Decimal(gasto["monto_transferencia"] or 0)
            if devuelto:
                cur.execute(
                    "UPDATE turnos_caja SET monto_final_esperado = COALESCE(monto_final_esperado, monto_inicial, 0) + %s "
                    "WHERE id_turno = %s",
                    (devuelto, gasto["id_turno"]),
                )
        cur.execute(
            "UPDATE gastos_caja SET estado_activo = 0, anulado_por = %s, anulado_en = %s, motivo_anulacion = %s "
            "WHERE id_gasto = %s",
            (id_usuario, ahora_local(), motivo, id_gasto),
        )
        _auditar(cur, id_tienda, id_usuario, "anular_gasto",
                 f"Gasto id={id_gasto} ({gasto['concepto']}, {gasto['monto']}), motivo={motivo}, devuelto_a_caja={devuelto}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"devuelto_a_caja": float(devuelto)}
