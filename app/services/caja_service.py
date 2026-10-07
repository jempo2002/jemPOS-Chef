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
from app.utils.helpers import ahora_local
from app.utils.validation import parse_float, sanitize_optional_text
from database import get_db

MONTO_MAX = 100_000_000


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
    partes pagadas por Nequi (la transferencia de esa venta puede incluir
    tarjeta); en un Mixto cobrado de una vez, la transferencia es Nequi."""
    cur.execute(
        "SELECT COUNT(*) AS ventas, COALESCE(SUM(v.total_final), 0) AS total, COALESCE(SUM(v.propina), 0) AS propinas, "
        "COALESCE(SUM(CASE v.metodo_pago WHEN 'Efectivo' THEN v.total_final + v.propina "
        "  WHEN 'Mixto' THEN v.monto_efectivo ELSE 0 END), 0) AS efectivo, "
        "COALESCE(SUM(CASE WHEN v.metodo_pago = 'Nequi/Daviplata' THEN v.total_final + v.propina "
        "  WHEN v.metodo_pago = 'Mixto' THEN COALESCE(pc.nequi, v.monto_transferencia, 0) ELSE 0 END), 0) AS nequi "
        "FROM ventas v "
        "LEFT JOIN (SELECT c.id_venta, SUM(CASE WHEN c.metodo_pago = 'Nequi/Daviplata' THEN c.monto + c.propina ELSE 0 END) AS nequi "
        "  FROM pedido_cuentas c JOIN ventas vc ON vc.id_venta = c.id_venta "
        "  WHERE vc.id_turno = %s AND vc.metodo_pago = 'Mixto' GROUP BY c.id_venta) pc ON pc.id_venta = v.id_venta "
        "WHERE v.id_turno = %s AND v.estado_venta = 'Pagada'",
        (turno["id_turno"], turno["id_turno"]),
    )
    r = cur.fetchone()
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
