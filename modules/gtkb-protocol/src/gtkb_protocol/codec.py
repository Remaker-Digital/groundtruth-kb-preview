"""Original GT-KB software. Copyright 2026 Remaker Digital. See NOTICE."""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal, DecimalException
from typing import Any

from .errors import ProtocolError


def canonical_json_bytes(value: object) -> bytes:
    """Return compact, key-sorted RFC 8259 JSON followed by exactly one LF."""
    try:
        _validate_json_value(value)
        rendered = _render_canonical_json(value)
    except (RecursionError, TypeError, ValueError) as exc:
        raise ProtocolError("invalid_json", "Value cannot be represented as canonical JSON") from exc
    return (rendered + "\n").encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_text(value: str, *, label: str = "text") -> None:
    if "\x00" in value:
        raise ProtocolError("invalid_text", f"{label} contains NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProtocolError("invalid_text", f"{label} contains an unpaired surrogate") from exc


def _validate_json_value(value: object) -> None:
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 512:
            raise ProtocolError("invalid_json", "JSON nesting exceeds the supported limit")
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, int):
            _canonical_number_text(item)
            continue
        if isinstance(item, (Decimal, float)):
            is_finite = item.is_finite() if isinstance(item, Decimal) else math.isfinite(item)
            if not is_finite:
                raise ProtocolError("invalid_json", "NaN and Infinity are not valid RFC 8259 numbers")
            _canonical_number_text(item)
            continue
        if isinstance(item, str):
            _validate_text(item)
            continue
        if isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
            continue
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ProtocolError("invalid_json", "JSON object keys must be strings")
                _validate_text(key, label="JSON object key")
                pending.append((child, depth + 1))
            continue
        raise ProtocolError("invalid_json", f"Unsupported JSON value type: {type(item).__name__}")


def _canonical_number_text(value: int | float | Decimal) -> str:
    if isinstance(value, int):
        rendered = _integer_text(value)
        if len(rendered.removeprefix("-")) > 131_072:
            raise ProtocolError("invalid_json", "JSON number exceeds PostgreSQL numeric range")
        return rendered
    decimal_value = value if isinstance(value, Decimal) else Decimal(repr(value))
    if not decimal_value.is_finite():
        raise ProtocolError("invalid_json", "NaN and Infinity are not valid RFC 8259 numbers")
    sign, digits_tuple, exponent = decimal_value.as_tuple()
    if not isinstance(exponent, int):
        raise ProtocolError("invalid_json", "NaN and Infinity are not valid RFC 8259 numbers")
    digits = "".join(str(digit) for digit in digits_tuple) or "0"
    if not any(digits_tuple):
        if exponent < 0:
            fractional_digits = -exponent
            if fractional_digits > 16_383:
                raise ProtocolError("invalid_json", "JSON number exceeds PostgreSQL numeric range")
            return "0." + ("0" * fractional_digits)
        return "0"
    digits_before = len(digits) + exponent
    fractional_digits = max(-exponent, 0)
    if max(digits_before, 0) > 131_072 or fractional_digits > 16_383:
        raise ProtocolError("invalid_json", "JSON number exceeds PostgreSQL numeric range")
    if exponent >= 0:
        rendered = digits + ("0" * exponent)
    elif digits_before > 0:
        rendered = digits[:digits_before] + "." + digits[digits_before:]
    else:
        rendered = "0." + ("0" * -digits_before) + digits
    return ("-" if sign else "") + rendered


def _integer_text(value: int) -> str:
    """Render an integer without Python's process-configurable digit limit."""
    if value == 0:
        return "0"
    negative = value < 0
    remaining = -value if negative else value
    chunks: list[int] = []
    while remaining:
        remaining, chunk = divmod(remaining, 1_000_000_000)
        chunks.append(chunk)
    rendered = str(chunks.pop())
    rendered += "".join(f"{chunk:09d}" for chunk in reversed(chunks))
    return "-" + rendered if negative else rendered


def _parse_integer_token(token: str) -> int:
    negative = token.startswith("-")
    digits = token[1:] if negative else token
    if len(digits) > 131_072:
        raise ProtocolError("invalid_json", "JSON number exceeds PostgreSQL numeric range")
    first_width = len(digits) % 9 or 9
    value = int(digits[:first_width])
    for offset in range(first_width, len(digits), 9):
        value = value * 1_000_000_000 + int(digits[offset : offset + 9])
    return -value if negative else value


def _parse_decimal_token(token: str) -> Decimal:
    try:
        return Decimal(token)
    except (DecimalException, ValueError) as exc:
        raise ProtocolError("invalid_json", "JSON number exceeds the supported decimal range") from exc


def _render_canonical_json(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float, Decimal)):
        return _canonical_number_text(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if isinstance(value, list):
        return "[" + ",".join(_render_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{json.dumps(key, ensure_ascii=False, allow_nan=False)}:{_render_canonical_json(value[key])}"
                for key in sorted(value)
            )
            + "}"
        )
    raise ProtocolError("invalid_json", f"Unsupported JSON value type: {type(value).__name__}")


def _reject_constant(value: str) -> None:
    raise ProtocolError("invalid_json", f"Non-RFC-8259 number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_json_key", f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json_bytes(raw: bytes) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ProtocolError("invalid_json", "UTF-8 BOM is forbidden")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_json", "Input is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_parse_decimal_token,
            parse_int=_parse_integer_token,
        )
        _validate_json_value(value)
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ProtocolError("invalid_json", "Input is not valid JSON") from exc
    return value
