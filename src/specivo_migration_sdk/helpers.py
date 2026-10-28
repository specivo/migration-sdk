"""Small helpers for the values a bundle holds: timestamps, dates, decimals, source refs.

Sources write time in many ways (``2024-03-01T09:30:00.000+0700``, epoch
milliseconds, a date and a time in the server's zone). A bundle takes exactly
one: ISO 8601 with a UTC offset. :func:`timestamp` turns what a source gives
into that, and refuses to guess an offset that is not there unless told which
zone to assume.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation

# A timestamp: an aware datetime, or a string timestamp() accepts.
type Timestamp = datetime | str
# A calendar date: a date, or an ISO 8601 date string.
type DateValue = date | str
# A decimal: written as an exact string when given as a Decimal or a string.
type DecimalValue = Decimal | int | float | str

# 1 to 255 characters, no whitespace, braces or bar, so a ref fits in a placeholder.
# Always used with fullmatch: with match, "$" would also accept a trailing newline.
SOURCE_REF_RE = re.compile(r"[^\s{}|]{1,255}")
# What the format's schema accepts; anything timestamp() returns matches it.
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DECIMAL_RE = re.compile(r"-?\d+(\.\d+)?")
# Epoch values above this are taken as milliseconds (it is in the year 33658 as seconds).
_EPOCH_MS_FROM = 10**12

# Characters a source ref cannot hold, and the separator helpers put between parts.
_REF_UNSAFE_RE = re.compile(r"[\s{}|%:]")
_REF_SEPARATOR = ":"
_REF_MAX = 255
# A ref over the limit keeps this much of its start and appends a hash of the whole.
_REF_KEEP = 190


def timestamp(value: datetime | str | int | float, *, assume_tz: tzinfo | None = None) -> str:
    """Return *value* as an ISO 8601 timestamp with a UTC offset.

    Accepts an aware ``datetime``, an ISO 8601 string (``Z``, ``+07:00`` and
    ``+0700`` offsets, a space instead of ``T``, any number of fractional
    digits), or seconds or milliseconds since the epoch (taken as UTC). A value
    without an offset raises :class:`ValueError` unless *assume_tz* names the
    zone it was recorded in. UTC is written ``Z``; fractions are kept to the
    microsecond.
    """
    if isinstance(value, bool):
        raise TypeError("A timestamp cannot be a boolean")
    if isinstance(value, int | float):
        seconds = value / 1000 if abs(value) >= _EPOCH_MS_FROM else value
        try:
            moment = datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            raise ValueError(f"{value!r} is not a time since the epoch that a timestamp can hold") from None
    elif isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        moment = _parse_timestamp(value.strip())
    else:
        raise TypeError(f"Expected a datetime, a string or a number, got {type(value).__name__}")

    if moment.tzinfo is None or moment.utcoffset() is None:
        if assume_tz is None:
            raise ValueError(f"Timestamp {value!r} has no UTC offset; pass assume_tz to say which zone it is in")
        moment = moment.replace(tzinfo=assume_tz)
    offset = moment.utcoffset()
    assert offset is not None
    if offset % timedelta(minutes=1):
        # Historical zones have offsets in seconds, which ISO 8601 cannot write.
        moment = moment.astimezone(UTC)
        offset = timedelta(0)
    text = moment.isoformat()
    if offset == timedelta(0):
        text = text.removesuffix("+00:00") + "Z"
    return text


def _parse_timestamp(text: str) -> datetime:
    # fromisoformat takes nearly every ISO 8601 form, but at most six
    # fractional digits; some sources write seven or nine.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{text!r} is not an ISO 8601 timestamp") from None


def date_value(value: date | str) -> str:
    """Return *value* as an ISO 8601 calendar date, ``YYYY-MM-DD``.

    A ``datetime`` gives its own date, in its own zone.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not _DATE_RE.fullmatch(text):
            raise ValueError(f"{value!r} is not an ISO 8601 date (YYYY-MM-DD)")
        return date.fromisoformat(text).isoformat()
    raise TypeError(f"Expected a date or a string, got {type(value).__name__}")


def decimal_value(value: Decimal | int | float | str) -> str | int | float:
    """Return *value* as a bundle decimal: exact digits for a ``Decimal`` or a string, else the number."""
    if isinstance(value, bool):
        raise TypeError("A decimal cannot be a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"{value!r} is not a finite number")
        return value
    try:
        number = Decimal(value.strip()) if isinstance(value, str) else value
    except InvalidOperation:
        raise ValueError(f"{value!r} is not a decimal number") from None
    if not isinstance(number, Decimal):
        raise TypeError(f"Expected a number, got {type(value).__name__}")
    if not number.is_finite():
        raise ValueError(f"{value!r} is not a finite number")
    text = format(number, "f")
    if not _DECIMAL_RE.fullmatch(text):  # pragma: no cover - format(..., "f") always matches
        raise ValueError(f"{value!r} is not a decimal number")
    return text


def is_timestamp(value: str) -> bool:
    """Whether *value* is a timestamp exactly as the format writes one, and exists."""
    if not _TIMESTAMP_RE.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def is_source_ref(value: object) -> bool:
    """Whether *value* can be a ``source_ref``: 1 to 255 characters, no whitespace, braces or ``|``."""
    return isinstance(value, str) and SOURCE_REF_RE.fullmatch(value) is not None


def source_ref(*parts: str | int) -> str:
    """Build a ``source_ref`` from one or more parts of the source's own key.

    Parts are joined with ``:``; in each part, whitespace, braces, ``|``, ``%``
    and ``:`` are percent-encoded, so any source key gives a valid ref and two
    different keys never give the same one. The result is stable: the same
    parts always give the same ref, which is what lets a later export add to
    an earlier import.

    ``source_ref(10042)`` is ``"10042"``; ``source_ref("comment", 10042, 7)`` is
    ``"comment:10042:7"``. A ref longer than 255 characters is shortened to
    its start and a SHA-256 of the whole.
    """
    if not parts:
        raise ValueError("source_ref needs at least one part")
    encoded: list[str] = []
    for part in parts:
        if isinstance(part, bool) or not isinstance(part, str | int):
            raise TypeError(f"A source_ref part must be a string or an integer, got {type(part).__name__}")
        text = str(part)
        if not text:
            raise ValueError("A source_ref part cannot be empty")
        encoded.append(_REF_UNSAFE_RE.sub(lambda match: _percent(match.group(0)), text))
    ref = _REF_SEPARATOR.join(encoded)
    if len(ref) > _REF_MAX:
        ref = f"{ref[:_REF_KEEP]}~{hashlib.sha256(ref.encode()).hexdigest()}"
    return ref


def _percent(char: str) -> str:
    return "".join(f"%{byte:02X}" for byte in char.encode())
