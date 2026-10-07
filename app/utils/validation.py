from __future__ import annotations

import math

from markupsafe import escape


def sanitize_text(
    raw_value: str | None,
    field_label: str,
    *,
    min_len: int = 1,
    max_len: int = 150,
    allow_empty: bool = False,
) -> str:
    value = str(raw_value or "").strip()
    if not value:
        if allow_empty:
            return ""
        raise ValueError(f"{field_label} es requerido.")
    if len(value) < min_len:
        raise ValueError(f"{field_label} debe tener al menos {min_len} caracteres.")
    if len(value) > max_len:
        raise ValueError(f"{field_label} no puede superar {max_len} caracteres.")
    return str(escape(value))


def sanitize_optional_text(
    raw_value: str | None,
    field_label: str,
    *,
    max_len: int = 255,
) -> str | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    if len(value) > max_len:
        raise ValueError(f"{field_label} no puede superar {max_len} caracteres.")
    return str(escape(value))


def parse_int(
    raw_value,
    field_label: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
    allow_zero: bool = True,
) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label} invalido.") from exc
    if not allow_zero and value == 0:
        raise ValueError(f"{field_label} invalido.")
    if min_value is not None and value < min_value:
        raise ValueError(f"{field_label} no puede ser menor a {min_value}.")
    if max_value is not None and value > max_value:
        raise ValueError(f"{field_label} no puede ser mayor a {max_value}.")
    return value


def parse_float(
    raw_value,
    field_label: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    allow_zero: bool = True,
) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label} invalido.") from exc
    # float() acepta "nan" e "inf": NaN pasaba cualquier comparacion de rango.
    if not math.isfinite(value):
        raise ValueError(f"{field_label} invalido.")
    if not allow_zero and value == 0:
        raise ValueError(f"{field_label} invalido.")
    if min_value is not None and value < min_value:
        raise ValueError(f"{field_label} no puede ser menor a {min_value}.")
    if max_value is not None and value > max_value:
        raise ValueError(f"{field_label} no puede ser mayor a {max_value}.")
    return value


def parse_bool(raw_value) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return raw_value != 0
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if value in {"true", "1", "si", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off", ""}:
            return False
    raise ValueError("Valor booleano invalido.")
