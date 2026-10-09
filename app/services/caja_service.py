"""Turno de caja por sede: abrir, ver como va y cerrar con arqueo.

Copiado en espiritu de jemPOS (sales_service.abrir_turno/cerrar_turno), con
dos cambios: el turno es de la sede, no del restaurante (una caja abierta por
sede, la base lo garantiza con turnos_caja.sede_abierta), y la propina va
aparte. La propina en efectivo esta en el cajon, asi que suma al esperado,
pero no es venta: no entra en total_final.
"""
from __future__ import annotations

from decimal import Decimal

from mysql.connector import IntegrityError

from app.services.errores import Conflicto
from app.services.gastos_service import categoria_valida
from app.utils.helpers import ahora_local
from app.utils.validation import parse_float, sanitize_optional_text, sanitize_text
from database import get_db

MONTO_MAX = 100_000_000
MAX_GASTOS_LISTA = 50
# Lo que manda la pantalla -> gastos_caja.metodo_pago
METODOS_GASTO = {
    "efectivo": "Efectivo",
    "nequi": "Nequi/Daviplata",
    "transferencia": "Nequi/Daviplata",
    "tarjeta": "Tarjeta",
    "mixto": "Mixto",
}


def siguiente_consecutivo(cur, id_tienda: int, clave: str) -> int:
    """Siguiente numero de `clave` ('venta', 'comanda:<sede>') sin carreras:
    la fila queda bloqueada hasta el commit del llamador."""
    cur.execute(
        "INSERT INTO consecutivos (id_tienda, clave, ultimo) VALUES (%s, %s, 1) "
        "ON DUPLICATE KEY UPDATE ultimo = ultimo + 1",
        (id_tienda, clave),
    )
    cur.execute("SELECT ultimo FROM consecutivos WHERE id_tienda = %s AND clave = %s", (id_tienda, clave))
    return int(cur.fetchone()["ultimo"])


def turno_abierto(cur, id_sede: int, bloquear: bool = False) -> dict | None:
    cur.execute(
        "SELECT id_turno, monto_inicial, monto_final_esperado, fecha_apertura, id_usuario_apertura "
        "FROM turnos_caja WHERE id_sede = %s AND estado_turno = 'Abierto' LIMIT 1"
        + (" FOR UPDATE" if bloquear else ""),
        (id_sede,),
    )
    return cur.fetchone()


def _resumen(cur, turno: dict) -> dict:
    """Cifras del turno. Nequi = lo que debe haber entrado por Nequi/Daviplata
    (con su propina). En una venta Mixto de cuenta dividida se suman solo las
    partes pagadas por Nequi y la transferencia de las partes Mixto (la
    transferencia de esa venta puede incluir tarjeta); en un Mixto cobrado de
    una vez, la transferencia es Nequi."""
    cur.execute(
        "SELECT COUNT(*) AS ventas, COALESCE(SUM(v.total_final), 0) AS total, COALESCE(SUM(v.propina), 0) AS propinas, "
        "COALESCE(SUM(CASE v.metodo_pago WHEN 'Efectivo' THEN v.total_final + v.propina "
        "  WHEN 'Mixto' THEN v.monto_efectivo ELSE 0 END), 0) AS efectivo, "
        "COALESCE(SUM(CASE WHEN v.metodo_pago = 'Nequi/Daviplata' THEN v.total_final + v.propina "
        "  WHEN v.metodo_pago = 'Mixto' THEN COALESCE(pc.nequi, v.monto_transferencia, 0) ELSE 0 END), 0) AS nequi "
        "FROM ventas v "
        "LEFT JOIN (SELECT c.id_venta, SUM(CASE WHEN c.metodo_pago = 'Nequi/Daviplata' THEN c.monto + c.propina "
        "    WHEN c.metodo_pago = 'Mixto' THEN c.monto_transferencia ELSE 0 END) AS nequi "
        "  FROM pedido_cuentas c JOIN ventas vc ON vc.id_venta = c.id_venta "
        "  WHERE vc.id_turno = %s AND vc.metodo_pago = 'Mixto' GROUP BY c.id_venta) pc ON pc.id_venta = v.id_venta "
        "WHERE v.id_turno = %s AND v.estado_venta = 'Pagada'",
        (turno["id_turno"], turno["id_turno"]),
    )
    r = cur.fetchone()
    cur.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(monto), 0) AS total, "
        "COALESCE(SUM(CASE WHEN fuente_dinero = 'Bancos' THEN 0 ELSE monto - monto_transferencia END), 0) AS efectivo "
        "FROM gastos_caja WHERE id_turno = %s AND estado_activo = 1",
        (turno["id_turno"],),
    )
    gastos = cur.fetchone()
    cur.execute(
        "SELECT id_gasto, concepto, categoria, monto, monto_transferencia, metodo_pago, fecha_creacion FROM gastos_caja "
        "WHERE id_turno = %s AND estado_activo = 1 ORDER BY id_gasto DESC LIMIT %s",
        (turno["id_turno"], MAX_GASTOS_LISTA),
    )
    lista = [
        {
            "id_gasto": g["id_gasto"],
            "concepto": g["concepto"],
            "categoria": g["categoria"],
            "monto": float(g["monto"]),
            "efectivo": float(Decimal(g["monto"]) - Decimal(g["monto_transferencia"] or 0)),
            "transferencia": float(g["monto_transferencia"] or 0),
            "metodo_pago": g["metodo_pago"] or "",
            "hora": g["fecha_creacion"].strftime("%H:%M") if g["fecha_creacion"] else "",
        }
        for g in cur.fetchall()
    ]
    inicial = Decimal(turno["monto_inicial"] or 0)
    return {
        "id_turno": turno["id_turno"],
        "abierto_desde": turno["fecha_apertura"].strftime("%Y-%m-%d %H:%M") if turno["fecha_apertura"] else None,
        "monto_inicial": float(inicial),
        "ventas": int(r["ventas"]),
        "total_ventas": float(r["total"]),
        "propinas": float(r["propinas"]),
        "efectivo_ventas": float(r["efectivo"]),
        "nequi": float(r["nequi"]),
        "gastos": int(gastos["n"]),
        "total_gastos": float(gastos["total"]),
        "gastos_efectivo": float(gastos["efectivo"]),
        "lista_gastos": lista,
        "esperado_en_caja": float(Decimal(turno["monto_final_esperado"] or inicial)),
    }


def estado(id_sede: int) -> dict | None:
    """Resumen del turno abierto de la sede, o None."""
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        turno = turno_abierto(cur, id_sede)
        return _resumen(cur, turno) if turno else None
    finally:
        conn.close()


def abrir(id_tienda: int, id_sede: int, id_usuario: int, data: dict) -> int:
    monto = round(parse_float(data.get("monto_inicial") or 0, "La base", min_value=0, max_value=MONTO_MAX), 2)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "INSERT INTO turnos_caja (id_tienda, id_sede, id_usuario_apertura, monto_inicial, monto_final_esperado) "
            "VALUES (%s, %s, %s, %s, %s)",
            (id_tienda, id_sede, id_usuario, monto, monto),
        )
        id_turno = cur.lastrowid
        conn.commit()
        return id_turno
    except IntegrityError as exc:
        conn.rollback()
        raise Conflicto("Ya hay una caja abierta en esta sede.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def registrar_gasto(id_tienda: int, id_sede: int, id_usuario: int, data: dict) -> dict:
    """Gasto pagado durante el turno de la sede. Efectivo sale del cajon;
    Nequi/Daviplata o tarjeta salen del banco; Mixto: `monto_efectivo` sale
    del cajon y el resto del banco. Solo lo que sale del cajon baja lo que
    debe haber al cerrar."""
    concepto = sanitize_text(data.get("concepto"), "El concepto", max_len=150)
    categoria = categoria_valida(data.get("categoria"))
    monto = Decimal(str(round(parse_float(data.get("monto"), "El monto", min_value=0, max_value=MONTO_MAX,
                                          allow_zero=False), 2)))
    metodo = METODOS_GASTO.get(str(data.get("metodo") or "").strip().lower())
    if not metodo:
        raise ValueError("Método de pago invalido.")
    if metodo == "Efectivo":
        efectivo = monto
    elif metodo == "Mixto":
        efectivo = Decimal(str(round(parse_float(data.get("monto_efectivo"), "El efectivo", min_value=0,
                                                 allow_zero=False), 2)))
        if efectivo >= monto:
            raise ValueError("En un pago mixto el efectivo debe ser menor que el monto; si es todo en efectivo elige Efectivo.")
    else:
        efectivo = Decimal(0)
    fuente = "Caja Menor" if efectivo else "Bancos"
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        turno = turno_abierto(cur, id_sede, bloquear=True)
        if not turno:
            raise Conflicto("Abre la caja antes de registrar gastos.")
        cur.execute(
            "INSERT INTO gastos_caja (id_tienda, id_sede, id_turno, id_usuario, concepto, categoria, monto, "
            "monto_transferencia, metodo_pago, fuente_dinero) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (id_tienda, id_sede, turno["id_turno"], id_usuario, concepto, categoria, monto, monto - efectivo, metodo,
             fuente),
        )
        id_gasto = cur.lastrowid
        if efectivo:
            cur.execute(
                "UPDATE turnos_caja SET monto_final_esperado = COALESCE(monto_final_esperado, monto_inicial, 0) - %s "
                "WHERE id_turno = %s",
                (efectivo, turno["id_turno"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"id_gasto": id_gasto, "monto": float(monto), "efectivo": float(efectivo), "metodo_pago": metodo}


def cerrar(id_tienda: int, id_sede: int, id_usuario: int, data: dict) -> dict:
    """Cierra con el efectivo contado. Devuelve el resumen y la diferencia
    (positiva = sobra, negativa = falta)."""
    contado = round(parse_float(data.get("monto_final_real"), "El efectivo contado", min_value=0, max_value=MONTO_MAX), 2)
    observaciones = sanitize_optional_text(data.get("observaciones"), "Las observaciones", max_len=255)
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        turno = turno_abierto(cur, id_sede, bloquear=True)
        if not turno:
            raise Conflicto("No hay caja abierta en esta sede.")
        resumen = _resumen(cur, turno)
        cur.execute(
            "UPDATE turnos_caja SET estado_turno = 'Cerrado', fecha_cierre = %s, id_usuario_cierre = %s, "
            "monto_final_real = %s, observaciones = %s WHERE id_turno = %s AND id_tienda = %s",
            (ahora_local(), id_usuario, contado, observaciones, turno["id_turno"], id_tienda),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    resumen["contado"] = float(contado)
    resumen["diferencia"] = round(float(contado) - resumen["esperado_en_caja"], 2)
    return resumen
