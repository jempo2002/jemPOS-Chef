from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone

# Hora del negocio. Un servidor en UTC (lo normal en la nube) cortaba "hoy" a
# las 7 p.m. de Colombia: la venta de las 11:30 p.m. caia en el dia siguiente.
# La app calcula con esta zona y la sesion de MySQL usa la misma (database.py),
# asi NOW()/CURDATE() y las columnas timestamp hablan la misma hora.
# ponytail: desfase fijo (Colombia no tiene horario de verano); pasar a
# zoneinfo + tzdata si se vende en un pais que cambie la hora.
UTC_OFFSET_HORAS = float(os.getenv("APP_UTC_OFFSET", "-5"))
ZONA_NEGOCIO = timezone(timedelta(hours=UTC_OFFSET_HORAS))
_signo = "-" if UTC_OFFSET_HORAS < 0 else "+"
_minutos = round(abs(UTC_OFFSET_HORAS) * 60)
ZONA_MYSQL = f"{_signo}{_minutos // 60:02d}:{_minutos % 60:02d}"


def ahora_local() -> datetime:
    """Fecha y hora del negocio, sin tzinfo (como las guarda y compara MySQL)."""
    return datetime.now(ZONA_NEGOCIO).replace(tzinfo=None)


def hoy_local() -> date:
    return ahora_local().date()


def avatar_iniciales(nombre: str) -> str:
    """Return initials for profile avatar fallback."""
    partes = nombre.strip().split()
    if len(partes) >= 2:
        return (partes[0][0] + partes[1][0]).upper()
    if partes:
        return partes[0][:2].upper()
    return "??"


def fmt_money(value: float) -> str:
    """Simple COP formatting for UI labels."""
    entero = int(round(value))
    return f"{'-' if entero < 0 else ''}${abs(entero):,}".replace(",", ".")


def fmt_numero(value, decimales: int = 3) -> str:
    """Cantidades y costos al estilo colombiano: 2.267,96 y 3,8 (sin ceros
    de sobra)."""
    texto = f"{float(value or 0):,.{decimales}f}"
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def only_digits(raw_value: str | None, max_len: int | None = None) -> str:
    """Keep only digits from user input with optional max length."""
    digits = re.sub(r"\D", "", str(raw_value or "").strip())
    if max_len is not None:
        digits = digits[:max_len]
    return digits


def normalize_phone(raw_value: str | None, max_len: int = 10) -> str | None:
    """Return normalized phone digits or None when empty."""
    digits = only_digits(raw_value, max_len=max_len)
    return digits or None
