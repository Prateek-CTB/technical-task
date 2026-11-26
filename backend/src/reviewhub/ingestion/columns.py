"""Mapping of file columns to canonical review fields.

Headers are matched case- and punctuation-insensitively against the aliases
below. Columns that look like personal data (reviewer names, e-mail, avatars...)
are never read; they are reported as ignored so the operator can see that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# canonical field -> accepted header names, in priority order
ALIASES: dict[str, tuple[str, ...]] = {
    "property": ("property", "property_slug", "property_name", "hotel", "hotel_name"),
    "external_id": ("review_id", "external_id", "id", "booking_review_id", "reviewid"),
    "rating": ("rating", "score", "review_score", "reviewscore", "guest_rating"),
    "published": (
        "published_at",
        "review_date",
        "published",
        "date",
        "reviewed_at",
        "reviewed",
        "publication_date",
        "created_at",
    ),
    "title": ("title", "review_title", "headline"),
    "positive": ("positive", "positive_text", "liked", "pros", "positive_review"),
    "negative": ("negative", "negative_text", "disliked", "cons", "negative_review"),
    "language": ("language", "language_code", "lang"),
    "country": ("guest_country", "country", "country_code"),
    "traveller_type": ("traveller_type", "traveler_type", "guest_type", "travel_type", "trip_type"),
    "room_type": ("room_type", "room_name", "room"),
    "checkin": ("checkin_date", "check_in_date", "checkin", "check_in", "arrival"),
    "checkout": ("checkout_date", "check_out_date", "checkout", "check_out", "departure"),
    "nights": ("nights", "number_of_nights", "length_of_stay"),
    "stay_status": ("stay_status",),
    "reply": (
        "management_reply",
        "property_reply",
        "partner_reply",
        "reply",
        "hotel_response",
        "response",
    ),
    "url": ("source_url", "review_url", "url", "link"),
}
REQUIRED = ("rating", "published")

_PERSONAL = re.compile(
    r"reviewer|author|username|user_name|first_name|last_name|surname|full_name|guest_name|^name$"
    r"|e_?mail|phone|mobile|avatar|photo|picture|image|passport|address"
)


def normalise_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


@dataclass
class ColumnMap:
    mapping: dict[str, str] = field(default_factory=dict)  # canonical -> original header
    ignored: list[tuple[str, str]] = field(default_factory=list)  # (header, reason)

    def has(self, canonical: str) -> bool:
        return canonical in self.mapping


def build_column_map(header: list[str]) -> ColumnMap:
    by_norm: dict[str, str] = {}
    for original in header:
        by_norm.setdefault(normalise_header(original), original)

    result = ColumnMap()
    used: set[str] = set()
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            matched = by_norm.get(alias)
            if matched is None:
                continue
            if canonical in result.mapping:
                result.ignored.append((matched, f"duplicate of {canonical}"))
            else:
                result.mapping[canonical] = matched
            used.add(matched)
    for original in header:
        if original in used:
            continue
        reason = "personal data" if _PERSONAL.search(normalise_header(original)) else "unrecognised"
        result.ignored.append((original, reason))
    return result
