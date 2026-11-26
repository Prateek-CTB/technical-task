"""Seed and summarise the fictional demo dataset through the normal import pipeline."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from reviewhub.ingestion.adapters.files import JsonAdapter
from reviewhub.ingestion.contracts import ImportReport
from reviewhub.ingestion.service import ImportOptions, import_file
from reviewhub.persistence.models import Property, Review
from reviewhub.persistence.scopes import DEMO_SOURCES

from .generator import DEFAULT_SEED, Dataset, generate


class DemoAdapter(JsonAdapter):
    """Same parser as a JSON import, but recorded as ``demo_synthetic``."""

    name = "demo"
    source = "demo_synthetic"


def reset_demo(factory: sessionmaker[Session]) -> int:
    """Delete demo reviews (and their analyses) only. Real data is never touched."""
    with factory() as s:
        n = s.execute(delete(Review).where(Review.source.in_(DEMO_SOURCES))).rowcount  # type: ignore[attr-defined]
        s.commit()
    return int(n or 0)


def seed_demo(
    factory: sessionmaker[Session],
    *,
    seed: int = DEFAULT_SEED,
    end: date | None = None,
    days: int = 364,
    reset: bool = False,
) -> tuple[ImportReport, Dataset]:
    """Idempotent: same (seed, end, days) -> same rows -> nothing new on a second run.

    Goes through ``import_file`` so every validation, constraint, data-quality check
    and the production/demo isolation rule applies exactly as for a real import.
    """
    if reset:
        reset_demo(factory)
    dataset = generate(seed, end, days)
    report = import_file(
        factory,
        content=json.dumps(dataset.records, ensure_ascii=False).encode("utf-8"),
        filename=f"demo-seed-{seed}.json",
        options=ImportOptions(format="json", id_namespace="demo", trigger="seed-demo"),
        adapter=DemoAdapter(),
        allowed_sources=DEMO_SOURCES,
    )
    return report, dataset


def summarise(session: Session, dataset: Dataset | None = None) -> dict[str, Any]:
    """Statistics of the demo rows now in the database."""
    rows = session.execute(
        select(
            Property.slug,
            Review.rating,
            Review.language_source,
            Review.published_date_local,
            Review.published_week_local,
            Review.positive_text,
            Review.negative_text,
            Review.negative_is_filler,
            Review.external_id,
        )
        .join(Property, Property.id == Review.property_id)
        .where(Review.source.in_(DEMO_SOURCES))
    ).all()
    per_prop = Counter(r.slug for r in rows)
    weekly: dict[str, Counter[date]] = defaultdict(Counter)
    ratings: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        weekly[r.slug][r.published_week_local] += 1
        ratings[r.slug].append(r.rating)
    volume: dict[str, Any] = {}
    for slug, counts in sorted(weekly.items()):
        first, last = min(counts), max(counts)
        span = (last - first).days // 7 + 1
        values = [counts.get(first + timedelta(days=7 * i), 0) for i in range(span)]
        volume[slug] = {
            "weeks": span,
            "median_per_week": statistics.median(values),
            "busiest_week": max(values),
            "weeks_under_5": sum(v < 5 for v in values),
            "empty_weeks": sum(v == 0 for v in values),
        }
    bands = Counter(
        "positive" if r.rating >= 8 else "negative" if r.rating <= 6 else "neutral" for r in rows
    )
    out: dict[str, Any] = {
        "total": len(rows),
        "per_property": dict(sorted(per_prop.items())),
        "avg_rating_per_property": {
            k: round(statistics.mean(v), 2) for k, v in sorted(ratings.items())
        },
        "date_coverage": {
            "first": min(r.published_date_local for r in rows).isoformat(),
            "last": max(r.published_date_local for r in rows).isoformat(),
        }
        if rows
        else None,
        "rating_distribution": dict(sorted(Counter(int(r.rating) for r in rows).items())),
        "language_tag_distribution": dict(Counter(r.language_source for r in rows).most_common()),
        "rating_band_distribution": dict(bands),
        "mixed_by_text_rule": sum(
            1 for r in rows if r.positive_text and r.negative_text and not r.negative_is_filler
        ),
        "weekly_volume": volume,
    }
    if dataset is not None:
        in_db = {r.external_id for r in rows}
        mentioned: Counter[str] = Counter()
        complaints: Counter[str] = Counter()
        for rid, info in dataset.truth.items():
            if rid in in_db:
                mentioned.update(info["topics"])
                complaints.update(info["negative_topics"])
        out["generator_topics_mentioned"] = dict(mentioned.most_common())
        out["generator_topics_in_complaints"] = dict(complaints.most_common())
        out["note"] = (
            "band and mixed figures follow simple rating/text rules; topic figures are the "
            "generator's own intent. Neither is AI analysis."
        )
    return out
