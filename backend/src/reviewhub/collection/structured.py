"""StructuredDataExtractor: schema.org JSON-LD in a public property page (pure parsing).

Booking.com property pages embed a ``Hotel`` object with an ``aggregateRating`` (the published
headline score and review count). That is the most layout-independent source for the score: it
is meant for machines, so it changes far less often than CSS classes or visible wording.

Review-level JSON-LD (``review`` items) is read when present, but Booking.com does not currently
put guest reviews there; the browser collector remains the source of individual reviews.
Nothing here performs I/O.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

_LD_JSON = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S
)


@dataclass
class StructuredData:
    score: float | None = None  # normalised to a 1-10 scale
    best_rating: float | None = None
    review_count: int | None = None
    name: str | None = None
    reviews: list[dict[str, Any]] = field(default_factory=list)  # import-format records
    blocks: int = 0  # JSON-LD blocks found (0 = the page has no structured data at all)


def _objects(node: Any) -> list[dict[str, Any]]:
    """Flatten JSON-LD: top-level lists, ``@graph`` containers and nested objects."""
    out: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            out.append(cur)
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return out


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _types(obj: dict[str, Any]) -> set[str]:
    t = obj.get("@type")
    return (
        {t}
        if isinstance(t, str)
        else {x for x in t if isinstance(x, str)}
        if isinstance(t, list)
        else set()
    )


def extract_structured(html: str, slug: str | None = None) -> StructuredData:
    data = StructuredData()
    objects: list[dict[str, Any]] = []
    for raw in _LD_JSON.findall(html or ""):
        try:
            parsed = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue  # one broken block must not hide the others
        data.blocks += 1
        objects.extend(_objects(parsed))

    lodging = {
        "Hotel",
        "LodgingBusiness",
        "Hostel",
        "Motel",
        "Resort",
        "BedAndBreakfast",
        "Apartment",
    }
    for obj in objects:
        if not (_types(obj) & lodging):
            continue
        data.name = data.name or (obj.get("name") if isinstance(obj.get("name"), str) else None)
        agg = obj.get("aggregateRating")
        if isinstance(agg, dict) and data.score is None:
            value, best = _num(agg.get("ratingValue")), _num(agg.get("bestRating")) or 10.0
            count = _num(agg.get("reviewCount") or agg.get("ratingCount"))
            if value is not None and best and 0 < value <= best:
                data.score = round(value * 10.0 / best, 2) if best != 10 else value
                data.best_rating = best
                data.review_count = int(count) if count is not None else None

    for obj in objects:
        if "Review" not in _types(obj):
            continue
        rec = _review_record(obj, slug)
        if rec is not None:
            data.reviews.append(rec)
    return data


def _review_record(obj: dict[str, Any], slug: str | None) -> dict[str, Any] | None:
    """A schema.org Review -> import-format record. Author names are deliberately dropped."""
    rating = obj.get("reviewRating")
    value = _num(rating.get("ratingValue")) if isinstance(rating, dict) else None
    best = (_num(rating.get("bestRating")) if isinstance(rating, dict) else None) or 10.0
    published = str(obj.get("datePublished") or "")[:10]
    body = obj.get("reviewBody") or obj.get("description") or ""
    if value is None or not published or not isinstance(body, str):
        return None
    try:
        date.fromisoformat(published)
    except ValueError:
        return None
    scaled = value * 10.0 / best if best != 10 else value
    return {
        "property": slug,
        "rating": round(max(1.0, min(10.0, scaled)), 1),
        "review_date": published,
        "title": obj.get("name") if isinstance(obj.get("name"), str) else "",
        "liked": body,
        "disliked": "",
        "language": obj.get("inLanguage") if isinstance(obj.get("inLanguage"), str) else "",
    }
