"""Small builders for test rows. All review text is invented."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from reviewhub.persistence.models import (
    DataQualityIssue,
    Property,
    Review,
    ReviewAnalysis,
    ReviewAspect,
    Run,
)

NOW = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
_counter = itertools.count(1)


def property_id(session: Session, slug: str = "chateau-de-venus") -> int:
    return session.scalars(select(Property.id).where(Property.slug == slug)).one()


def review(session: Session, **overrides: Any) -> Review:
    n = next(_counter)
    values: dict[str, Any] = {
        "property_id": property_id(session),
        "source": "demo_synthetic",
        "dedupe_key": f"ext:test:{n}",
        "external_id": str(n),
        "rating": 8,
        "published_at": NOW,
        "published_date_local": date(2026, 11, 26),
        "published_week_local": date(2026, 11, 23),
        "positive_text": "Invented praise.",
        "content_hash": f"{n:016d}",
        "collected_at": NOW,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    obj = Review(**values)
    session.add(obj)
    session.flush()
    return obj


def analysis(session: Session, review_id: int, **overrides: Any) -> ReviewAnalysis:
    values: dict[str, Any] = {
        "review_id": review_id,
        "analyzer": "llm",
        "provider": "anthropic",
        "model_id": "test-model",
        "prompt_version": "1",
        "taxonomy_version": "1",
        "input_hash": "a" * 64,
        "status": "ok",
        "overall_sentiment": "positive",
        "created_at": NOW,
    }
    values.update(overrides)
    obj = ReviewAnalysis(**values)
    session.add(obj)
    session.flush()
    return obj


def lexicon_analysis(session: Session, review_id: int, **overrides: Any) -> ReviewAnalysis:
    values = {
        "analyzer": "lexicon",
        "provider": "builtin",
        "model_id": "lexicon-1.0",
        "prompt_version": "n/a",
    }
    values.update(overrides)
    return analysis(session, review_id, **values)


def aspect(session: Session, analysis_id: int, review_id: int, **overrides: Any) -> ReviewAspect:
    values: dict[str, Any] = {
        "analysis_id": analysis_id,
        "review_id": review_id,
        "topic": "cleanliness",
        "sentiment": "negative",
        "evidence": [{"field": "disliked", "quote": "invented quote", "start": 0, "end": 14}],
    }
    values.update(overrides)
    obj = ReviewAspect(**values)
    session.add(obj)
    session.flush()
    return obj


def run(session: Session, **overrides: Any) -> Run:
    values: dict[str, Any] = {
        "kind": "import",
        "source": "csv_import",
        "scope_key": "import:" + "0" * 64,
        "mode": "file",
        "status": "running",
        "started_at": NOW,
    }
    values.update(overrides)
    obj = Run(**values)
    session.add(obj)
    session.flush()
    return obj


def issue(session: Session, **overrides: Any) -> DataQualityIssue:
    values: dict[str, Any] = {
        "code": "DQ006",
        "severity": "warning",
        "fingerprint": "fp-1",
        "detected_at": NOW,
    }
    values.update(overrides)
    obj = DataQualityIssue(**values)
    session.add(obj)
    session.flush()
    return obj


def commit_in_new_session(engine: Engine, fn: Callable[[Session], Any]) -> Any:
    """Run ``fn`` in its own session and commit; used so a later violation starts clean."""
    with Session(engine, expire_on_commit=False) as s:
        result = fn(s)
        s.commit()
        return result
