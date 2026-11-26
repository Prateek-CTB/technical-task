"""Keeping stored local dates and weeks consistent with each property's timezone.

``review.published_date_local`` / ``published_week_local`` are derived from the
UTC instant and the *property's* ``timezone`` at ingestion. They are stored for
indexing and grouping, so they can go stale if a property's timezone is
corrected. :func:`find_local_date_mismatches` is the invariant check and
:func:`recompute_local_dates` is the repair.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from reviewhub.core.timeutil import local_dates
from reviewhub.persistence.models import Property, Review

_CHUNK = 1000


def _scan(
    session: Session, property_id: int | None
) -> Iterator[tuple[int, int, date, date, date, date]]:
    """Yield (review_id, property_id, stored_date, stored_week, expected_date, expected_week) where they differ."""
    last_id = 0
    while True:
        query = (
            select(
                Review.id,
                Review.property_id,
                Review.published_at,
                Review.published_date_local,
                Review.published_week_local,
                Property.timezone,
            )
            .join(Property, Property.id == Review.property_id)
            .where(Review.id > last_id)
            .order_by(Review.id)
            .limit(_CHUNK)
        )
        if property_id is not None:
            query = query.where(Review.property_id == property_id)
        rows = session.execute(query).all()
        if not rows:
            return
        for rid, pid, published_at, day, week, tz in rows:
            exp_day, exp_week = local_dates(published_at, tz)
            if (day, week) != (exp_day, exp_week):
                yield rid, pid, day, week, exp_day, exp_week
        last_id = rows[-1][0]


def find_local_date_mismatches(
    session: Session, property_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rid, pid, day, week, exp_day, exp_week in _scan(session, property_id):
        out.append(
            {
                "review_id": rid,
                "property_id": pid,
                "stored": (day, week),
                "expected": (exp_day, exp_week),
            }
        )
        if len(out) >= limit:
            break
    return out


def recompute_local_dates(session: Session, property_id: int | None = None) -> int:
    """Re-derive stored local dates/weeks from each review's property timezone. Returns rows changed."""
    changed = 0
    for rid, _pid, _day, _week, exp_day, exp_week in list(_scan(session, property_id)):
        session.execute(
            update(Review)
            .where(Review.id == rid)
            .values(published_date_local=exp_day, published_week_local=exp_week)
        )
        changed += 1
    session.flush()
    return changed
