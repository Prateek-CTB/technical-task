"""Stable identity and change detection for reviews."""

from __future__ import annotations

import hashlib
import json
import re

#: Fields compared on re-import. A difference in any of them is a *change* and
#: produces a ``review_revision`` row listing exactly which fields moved.
CONTENT_FIELDS = (
    "rating",
    "published_at",
    "title",
    "positive_text",
    "negative_text",
    "language_source",
    "guest_country",
    "traveller_type",
    "room_type",
    "checkin_date",
    "checkout_date",
    "nights",
    "stay_status",
    "management_reply",
    "source_url",
)

_KEY_LIMIT = 80  # review.dedupe_key column length
_NAMESPACE = re.compile(r"^[a-z0-9_-]{1,20}$")


def valid_namespace(namespace: str) -> bool:
    return bool(_NAMESPACE.match(namespace))


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def dedupe_key(
    *,
    namespace: str,
    external_id: str | None,
    property_id: int,
    raw_published: str,
    rating: float,
    positive: str | None,
    negative: str | None,
    title: str | None,
) -> str:
    """Identity of a review *within a property*.

    With a source id: ``ext:<namespace>:<id>`` (hashed if too long for the column).
    Without one: a deterministic fingerprint, so re-importing the same file creates
    nothing. Limitation: without an id, an *edited* review looks like a new one.
    The fingerprint uses the published value exactly as written in the file, so it
    does not change if a property's timezone is corrected later.
    """
    if external_id:
        key = f"ext:{namespace}:{external_id}"
        if len(key) <= _KEY_LIMIT:
            return key
        return f"ext:{namespace}:h:{hashlib.sha256(external_id.encode()).hexdigest()[:40]}"
    material = "|".join(
        [
            str(property_id),
            raw_published.strip(),
            f"{rating:.1f}",
            _norm(positive),
            _norm(negative),
            _norm(title),
        ]
    )
    return "fp:" + hashlib.sha256(material.encode()).hexdigest()[:32]


def content_hash(
    rating: float, title: str | None, positive: str | None, negative: str | None, reply: str | None
) -> str:
    """Hash of the review's words and score: used to spot the same text under two ids."""
    payload = json.dumps([f"{rating:.1f}", title, positive, negative, reply], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
