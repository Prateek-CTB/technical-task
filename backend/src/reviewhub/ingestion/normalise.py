"""Row normalisation and validation: raw file values -> ``ReviewIn`` or rejection issues."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from reviewhub.core.timeutil import InvalidTimezone, get_zone, local_dates, parse_day, parse_instant

from .columns import ColumnMap
from .contracts import FileRejected, PropertyInfo, RawRow, ReviewIn
from .dedupe import content_hash, dedupe_key
from .quality import RowIssue, issue
from .text import clean_text, fit, is_filler

EARLIEST = datetime(2010, 1, 1, tzinfo=UTC)
_COUNTRY_OK = 2


@dataclass(frozen=True)
class NormaliseContext:
    properties: Mapping[str, PropertyInfo]  # lower-cased slug and name -> property
    default_property: PropertyInfo | None
    date_format: str | None
    id_namespace: str
    now: datetime


def _s(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalise_row(
    raw: RawRow, columns: ColumnMap, ctx: NormaliseContext
) -> tuple[ReviewIn | None, list[RowIssue]]:
    issues: list[RowIssue] = []
    line = raw.line

    def get(field: str) -> Any:
        header = columns.mapping.get(field)
        return raw.data.get(header) if header is not None else None

    def add(code: str, message: str, column: str | None = None, slug: str | None = None) -> None:
        issues.append(issue(line, code, message, column, slug))

    # --- property ------------------------------------------------------------------
    prop: PropertyInfo | None = ctx.default_property
    prop_value = _s(get("property"))
    if prop_value is not None:
        prop = ctx.properties.get(prop_value.lower())
        if prop is None:
            add("DQ012", f"unknown property {prop_value!r}", columns.mapping.get("property"))
    elif prop is None:
        add(
            "DQ012",
            "no property given and no default property selected",
            columns.mapping.get("property"),
        )
    try:
        if prop is not None:
            get_zone(prop.timezone)
    except InvalidTimezone as exc:
        raise FileRejected(
            "invalid_property_timezone",
            f"Property {prop.slug if prop else '?'} has an invalid timezone configured ({exc}); "
            "fix the property before importing.",
        ) from exc
    slug = prop.slug if prop else None

    # --- rating --------------------------------------------------------------------
    rating: float | None = None
    rating_raw = get("rating")
    try:
        rating = round(float(str(rating_raw).strip().replace(",", ".")), 1)
        if not math.isfinite(rating) or not 1 <= rating <= 10:
            raise ValueError
    except (TypeError, ValueError):
        rating = None
        add(
            "DQ002",
            f"rating {rating_raw!r} is missing or not a number from 1 to 10",
            columns.mapping.get("rating"),
            slug,
        )

    # --- publication time ----------------------------------------------------------
    published_at: datetime | None = None
    precision = "instant"
    published_raw = _s(get("published"))
    if published_raw is None:
        add("DQ007", "publication date is missing", columns.mapping.get("published"), slug)
    elif prop is not None:
        try:
            published_at, precision = parse_instant(published_raw, prop.timezone, ctx.date_format)
        except (ValueError, OverflowError):
            add(
                "DQ007",
                f"publication date {published_raw!r} is not understood "
                "(use ISO 8601, e.g. 2026-11-23 or 2026-11-23T14:30:00+11:00, or --date-format)",
                columns.mapping.get("published"),
                slug,
            )
    if published_at is not None and not EARLIEST <= published_at <= ctx.now + timedelta(days=1):
        add(
            "DQ003",
            f"publication date {published_raw!r} is in the future or before 2010",
            columns.mapping.get("published"),
            slug,
        )
        published_at = None

    if any(i.rejects_row for i in issues) or prop is None or rating is None or published_at is None:
        return None, issues
    assert published_raw is not None

    # --- optional fields (a bad value is dropped, never a reason to lose the review) ----
    def optional(canonical: str, parse: Any) -> Any:
        value = _s(get(canonical))
        if value is None:
            return None
        try:
            return parse(value)
        except (ValueError, OverflowError):
            add(
                "DQ019",
                f"{canonical} value {value!r} not understood; ignored",
                columns.mapping.get(canonical),
                slug,
            )
            return None

    def parse_country(value: str) -> str:
        if len(value) == _COUNTRY_OK and value.isalpha():
            return value.upper()
        raise ValueError(value)

    def parse_nights(value: str) -> int:
        n = int(float(value))
        if not 0 <= n <= 365:
            raise ValueError(value)
        return n

    def parse_url(value: str) -> str:
        if not value.lower().startswith(("http://", "https://")) or len(value) > 500:
            raise ValueError(value)
        return value

    checkin: date | None = optional("checkin", lambda v: parse_day(v, ctx.date_format))
    checkout: date | None = optional("checkout", lambda v: parse_day(v, ctx.date_format))
    nights: int | None = optional("nights", parse_nights)
    country: str | None = optional("country", parse_country)
    url: str | None = optional("url", parse_url)

    title = clean_text(get("title"))
    positive = clean_text(get("positive"))
    negative = clean_text(get("negative"))
    reply = clean_text(get("reply"))
    external_id = fit(_s(get("external_id")), 100)
    if external_id is not None and len(str(_s(get("external_id")))) > 100:
        add(
            "DQ019",
            "external id longer than 100 characters was truncated",
            columns.mapping.get("external_id"),
            slug,
        )

    local_day, local_week = local_dates(published_at, prop.timezone)

    # --- findings that do not reject ---------------------------------------------------
    if external_id is None:
        add(
            "DQ001",
            "no external id; a content fingerprint is used",
            columns.mapping.get("external_id"),
            slug,
        )
    if not (title or positive or negative):
        add("DQ004", "no review text", None, slug)
    if precision != "instant":
        add(
            "DQ016",
            f"publication time {published_raw!r} has no timezone/time; property-local time assumed",
            columns.mapping.get("published"),
            slug,
        )
    if checkout is not None and local_day < checkout:
        add(
            "DQ005",
            f"published ({local_day}) before check-out ({checkout})",
            columns.mapping.get("published"),
            slug,
        )
    if checkin is not None and checkout is not None and checkin > checkout:
        add(
            "DQ005",
            f"check-in ({checkin}) is after check-out ({checkout})",
            columns.mapping.get("checkin"),
            slug,
        )

    payload: dict[str, Any] = {
        "line": line,
        "fields": {
            canonical: str(raw.data.get(header))
            for canonical, header in columns.mapping.items()
            if raw.data.get(header) not in (None, "")
        },
    }
    key = dedupe_key(
        namespace=ctx.id_namespace,
        external_id=external_id,
        property_id=prop.id,
        raw_published=published_raw,
        rating=rating,
        positive=positive,
        negative=negative,
        title=title,
    )
    review = ReviewIn(
        line=line,
        property=prop,
        external_id=external_id,
        rating=rating,
        published_at=published_at,
        published_date_local=local_day,
        published_week_local=local_week,
        title=title,
        positive_text=positive,
        negative_text=negative,
        negative_is_filler=is_filler(negative) if negative is not None else False,
        language_source=fit((_s(get("language")) or "").lower() or None, 8),
        guest_country=country,
        traveller_type=fit(_s(get("traveller_type")), 64),
        room_type=fit(_s(get("room_type")), 200),
        checkin_date=checkin,
        checkout_date=checkout,
        nights=nights,
        stay_status=fit(_s(get("stay_status")), 64),
        management_reply=reply,
        source_url=url,
        dedupe_key=key,
        content_hash=content_hash(rating, title, positive, negative, reply),
        search_text="\n".join(p for p in (title, positive, negative) if p),
        payload=payload,
    )
    return review, issues
