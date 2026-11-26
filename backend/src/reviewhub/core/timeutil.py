"""Time handling: every local date and week is derived from the *property's* timezone.

Instants are stored in UTC. The local date and the Monday-based week are derived
once at ingestion from the property's configured IANA zone and stored (they are
indexed and used for grouping). ``recompute_local_dates`` re-derives them if a
property's zone changes, and ``find_local_date_mismatches`` is the invariant
check that proves stored values still agree with the configured zone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

Precision = Literal["instant", "local", "date"]


class InvalidTimezone(ValueError):
    """The configured zone name is not a valid IANA zone."""


@lru_cache(maxsize=64)
def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise InvalidTimezone(f"invalid timezone: {name!r}") from exc


def week_start(day: date) -> date:
    """Monday of the ISO week containing ``day``."""
    return day - timedelta(days=day.weekday())


def local_dates(instant: datetime, tz_name: str) -> tuple[date, date]:
    """(local date, Monday of its week) of a UTC instant in the given zone."""
    if instant.tzinfo is None:
        raise ValueError("naive datetime: provide a timezone-aware instant")
    day = instant.astimezone(get_zone(tz_name)).date()
    return day, week_start(day)


_TIME_DIRECTIVES = ("%H", "%I", "%M", "%S", "%f", "%p", "%X", "%c")


def parse_instant(
    value: str, tz_name: str, date_format: str | None = None
) -> tuple[datetime, Precision]:
    """Parse a publication timestamp into a UTC instant.

    * explicit offset / ``Z``            -> exact instant, precision ``instant``
    * date and time without an offset    -> property-local time, precision ``local``
    * date only                          -> local midnight of that date, precision ``date``

    Only ISO 8601 is understood unless ``date_format`` (``strptime`` syntax) is given,
    because ``03/04/2026`` is ambiguous and guessing would silently corrupt weeks.
    """
    text = value.strip()
    zone = get_zone(tz_name)
    if date_format:
        parsed = datetime.strptime(text, date_format)
        has_time = any(d in date_format for d in _TIME_DIRECTIVES)
    else:
        if len(text) == 10:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
            has_time = False
        else:
            parsed = datetime.fromisoformat(text)
            has_time = True
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC), "instant"
    local = parsed.replace(tzinfo=zone)
    return local.astimezone(UTC), ("local" if has_time else "date")


def parse_day(value: str, date_format: str | None = None) -> date:
    text = value.strip()
    if date_format:
        return datetime.strptime(text, date_format).date()
    return date.fromisoformat(text[:10])
