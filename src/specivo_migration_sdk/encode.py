"""Turn record dataclasses into bundle JSON, and check values the schemas cannot.

One generic converter, driven by each dataclass's type hints, rather than a
function per record type: a field added to a model needs nothing here. The
hints say which strings are timestamps or dates, so a value given as text is
normalised exactly like one given as a ``datetime``.
"""

from __future__ import annotations

import dataclasses
import types
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, TypeAliasType, Union, get_args, get_origin, get_type_hints

from specivo_migration_sdk.helpers import (
    DateValue,
    DecimalValue,
    Timestamp,
    date_value,
    decimal_value,
    timestamp,
)

_HINTS: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    """Return *cls*'s resolved type hints, worked out once per class."""
    hints = _HINTS.get(cls)
    if hints is None:
        hints = _HINTS[cls] = get_type_hints(cls)
    return hints


def _unwrap(hint: Any) -> Any:
    """Strip ``X | None`` and plain aliases, keeping the three value aliases recognisable."""
    while True:
        if hint in (Timestamp, DateValue, DecimalValue):
            return hint
        if isinstance(hint, TypeAliasType):
            hint = hint.__value__
            continue
        if get_origin(hint) in (Union, types.UnionType):
            args = [arg for arg in get_args(hint) if arg is not type(None)]
            if len(args) == 1:
                hint = args[0]
                continue
        return hint


def _default(field: dataclasses.Field[Any]) -> Any:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    return dataclasses.MISSING


def to_record(obj: Any) -> dict[str, Any]:
    """Return the JSON object for record dataclass *obj*.

    ``None`` and values equal to the field's default are left out: the format
    reads an absent property as its default.
    """
    record: dict[str, Any] = {}
    hints = _hints(type(obj))
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        if value is None:
            continue
        default = _default(field)
        if default is not dataclasses.MISSING and type(value) is type(default) and value == default:
            continue
        try:
            record[field.name] = _encode(hints[field.name], value)
        except (ValueError, TypeError) as exc:
            kind = TypeError if isinstance(exc, TypeError) else ValueError
            raise kind(f"{type(obj).__name__}.{field.name}: {exc}") from None
    return record


def _encode(hint: Any, value: Any) -> Any:
    hint = _unwrap(hint)
    if hint is Timestamp:
        return timestamp(value)
    if hint is DateValue:
        return date_value(value)
    if hint is DecimalValue:
        return decimal_value(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_record(value)
    origin = get_origin(hint)
    if isinstance(value, list | tuple):
        item_hint = get_args(hint)[0] if origin is list else Any
        return [_encode(item_hint, item) for item in value]
    if isinstance(value, dict):
        value_hint = get_args(hint)[1] if origin is dict else Any
        return {str(key): _encode(value_hint, item) for key, item in value.items()}
    return _plain(value)


def _plain(value: Any) -> Any:
    """Encode a value with no hint to go by: one inside ``extra`` or a custom value."""
    if isinstance(value, datetime):
        # Inside extra the format asks nothing of it; keep what the source had.
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return decimal_value(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value


# --------------------------------------------------------------------------
# Checking values read from a bundle
# --------------------------------------------------------------------------


def value_problems(cls: type, record: dict[str, Any], prefix: str = "") -> list[str]:
    """Return what in *record* is not a value *cls* can hold, one message per value.

    The schemas check the shape of a date or a timestamp but not that it
    exists (``2024-02-30``), nor that a decimal is finite; this parses every
    one, so a bad value is reported with its place in the record.
    """
    hints = _hints(cls)
    problems: list[str] = []
    for field in dataclasses.fields(cls):
        if record.get(field.name) is not None:
            problems.extend(_value_problems(hints[field.name], record[field.name], f"{prefix}/{field.name}"))
    return problems


def _value_problems(hint: Any, value: Any, pointer: str) -> list[str]:
    hint = _unwrap(hint)
    origin = get_origin(hint)
    if origin is list and isinstance(value, list):
        (item_hint,) = get_args(hint)
        return [p for index, item in enumerate(value) for p in _value_problems(item_hint, item, f"{pointer}/{index}")]
    if isinstance(hint, type) and dataclasses.is_dataclass(hint) and isinstance(value, dict):
        return value_problems(hint, value, pointer)
    try:
        if hint is Timestamp:
            datetime.fromisoformat(value)
        elif hint is DateValue:
            date.fromisoformat(value)
        elif hint is DecimalValue and not Decimal(str(value)).is_finite():
            return [f"{pointer}: {value!r} is not a finite number"]
    except (ValueError, TypeError, ArithmeticError):
        kind = "timestamp" if hint is Timestamp else "date" if hint is DateValue else "number"
        return [f"{pointer}: {value!r} is not a valid {kind}"]
    return []
