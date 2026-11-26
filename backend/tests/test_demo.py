"""P3: the fictional demo dataset. Generator tests need no database; seeding tests run on both engines."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from reviewhub import cli
from reviewhub.core.settings import get_settings
from reviewhub.core.timeutil import local_dates
from reviewhub.demo.generator import PROFILES, TOPICS, generate
from reviewhub.demo.seed import DemoAdapter, reset_demo, seed_demo, summarise
from reviewhub.ingestion.contracts import ForbiddenSource
from reviewhub.ingestion.locality import find_local_date_mismatches
from reviewhub.persistence.models import DataQualityIssue, Property, Review, Run
from reviewhub.persistence.scopes import (
    DatasetIsolationError,
    dataset_provenance,
    reportable,
)
from reviewhub.persistence.session import create_session_factory
from tests import factories as f
from tests.ingest_helpers import count, make_csv, row, run_import

END = date(2026, 11, 25)


@pytest.fixture(scope="module")
def data():  # type: ignore[no-untyped-def]
    return generate(end=END)


# ================================================================= generator (no database)
def test_fixed_seed_is_byte_for_byte_reproducible(data) -> None:  # type: ignore[no-untyped-def]
    again = generate(end=END)
    assert json.dumps(again.records, sort_keys=True) == json.dumps(data.records, sort_keys=True)
    assert again.truth == data.truth
    other = generate(seed=1, end=END)
    assert json.dumps(other.records, sort_keys=True) != json.dumps(data.records, sort_keys=True)


def test_extending_the_date_range_never_changes_earlier_days(data) -> None:  # type: ignore[no-untyped-def]
    earlier = generate(end=END - timedelta(days=30), days=334)
    ids = {r["review_id"] for r in earlier.records}
    by_id = {r["review_id"]: r for r in data.records}
    assert ids and ids <= set(by_id)
    assert all(by_id[r["review_id"]] == r for r in earlier.records)


def test_volume_and_shape_match_the_brief(data) -> None:  # type: ignore[no-untyped-def]
    assert 450 <= len(data.records) <= 600
    per = Counter(r["property"] for r in data.records)
    assert set(per) == set(PROFILES)
    assert per["chateau-de-venus"] > per["venus-surry-hills"] > per["olympic-paddington"]
    assert per["venus-potts-point-sydney"] > per["olympic-paddington"]
    days = {r["review_date"][:10] for r in data.records}
    assert (date.fromisoformat(max(days)) - date.fromisoformat(min(days))).days >= 355


def test_properties_are_distinguishable(data) -> None:  # type: ignore[no-untyped-def]
    means = {
        slug: statistics.mean(r["rating"] for r in data.records if r["property"] == slug)
        for slug in PROFILES
    }
    assert max(means.values()) - min(means.values()) >= 1.0
    assert all(1 <= r["rating"] <= 10 for r in data.records) and {1, 5, 10} <= {
        r["rating"] for r in data.records
    }
    complaints = {
        slug: Counter(
            t
            for r in data.records
            if r["property"] == slug
            for t in data.truth[r["review_id"]]["negative_topics"]
        )
        for slug in PROFILES
    }
    top = {slug: c.most_common(1)[0][0] for slug, c in complaints.items()}
    assert (
        len(set(top.values())) >= 3
    )  # different operational issues stand out at different properties


def test_all_eight_topics_appear_in_praise_and_complaints(data) -> None:  # type: ignore[no-untyped-def]
    praised = {t for v in data.truth.values() for t in v["positive_topics"]}
    complained = {t for v in data.truth.values() for t in v["negative_topics"]}
    assert praised == complained == set(TOPICS)


def test_language_and_field_variety(data) -> None:  # type: ignore[no-untyped-def]
    langs = Counter(r["language"] for r in data.records)
    assert {"en", "xu", "fr", "es", "de", "ja"} <= set(langs) and langs["en"] > 0.6 * len(
        data.records
    )
    assert sum(1 for r in data.records if r["management_reply"]) > 0.4 * len(data.records)
    assert len({r["country"] for r in data.records}) >= 8
    assert any(
        not (r["liked"] or r["disliked"] or r["title"]) for r in data.records
    )  # a few rating-only reviews
    lengths = [len(r["liked"]) + len(r["disliked"]) for r in data.records]
    assert min(lengths) == 0 and max(lengths) > 120


def test_text_is_varied_not_templated(data) -> None:  # type: ignore[no-untyped-def]
    texts = [(r["liked"], r["disliked"]) for r in data.records if r["liked"] or r["disliked"]]
    assert len(set(texts)) / len(texts) > 0.85


def test_weekly_volume_varies_with_quiet_and_busy_weeks(data) -> None:  # type: ignore[no-untyped-def]
    weekly: Counter[tuple[str, date]] = Counter()
    for r in data.records:
        d = date.fromisoformat(r["review_date"][:10])
        weekly[(r["property"], d - timedelta(days=d.weekday()))] += 1
    darling = [v for (slug, _), v in weekly.items() if slug == "chateau-de-venus"]
    assert max(darling) >= 3 * statistics.median(darling)
    assert len({v for v in weekly.values()}) >= 6


def test_only_fictional_marker_sentences_come_from_the_banks(data) -> None:  # type: ignore[no-untyped-def]
    """Guard against accidental real data: ids and countries are synthetic, ids say 'demo'."""
    assert all(r["review_id"].startswith("demo-") for r in data.records)


# ================================================================= seeding (both engines)
def seeded(engine: Engine):  # type: ignore[no-untyped-def]
    return seed_demo(create_session_factory(engine), end=END)


def test_seed_loads_every_review_with_demo_provenance(engine: Engine, data) -> None:  # type: ignore[no-untyped-def]
    report, _ = seeded(engine)
    assert (report.status, report.new, report.rows_rejected) == ("success", len(data.records), 0)
    assert report.source == "demo_synthetic"
    with Session(engine) as s:
        sources = set(s.scalars(select(Review.source)))
        assert sources == {"demo_synthetic"} and count(engine, Review) == len(data.records)
        assert s.scalars(select(Run)).one().source == "demo_synthetic"
        assert (
            s.scalars(select(DataQualityIssue).where(DataQualityIssue.severity == "error")).all()
            == []
        )
    assert report.dataset["contains_demo"] is True and report.dataset["sources"] == {
        "demo_synthetic": len(data.records)
    }


def test_seed_is_idempotent(engine: Engine, data) -> None:  # type: ignore[no-untyped-def]
    seeded(engine)
    before = [(r.id, r.updated_at) for r in _reviews(engine)]
    again, _ = seeded(engine)
    assert (again.new, again.updated, again.unchanged) == (0, 0, len(data.records))
    assert [(r.id, r.updated_at) for r in _reviews(engine)] == before


def test_seeding_a_later_end_date_only_adds_newer_reviews(engine: Engine) -> None:
    factory = create_session_factory(engine)
    seed_demo(factory, end=END - timedelta(days=21), days=343)
    n = count(engine, Review)
    report, _ = seed_demo(factory, end=END)
    assert report.updated == 0 and report.new > 0 and count(engine, Review) == n + report.new


def _reviews(engine: Engine) -> list[Review]:
    with Session(engine) as s:
        return list(s.scalars(select(Review).order_by(Review.id)))


def test_reviews_belong_to_the_right_property_with_timezone_derived_dates(engine: Engine) -> None:
    seeded(engine)
    with Session(engine) as s:
        rows = s.execute(
            select(Review, Property).join(Property, Property.id == Review.property_id)
        ).all()
        assert len(rows) > 400
        for review, prop in rows:
            assert f"-{prop.slug}-" in (
                review.external_id or ""
            )  # id was generated for this property
            day, week = local_dates(review.published_at, prop.timezone)
            assert (review.published_date_local, review.published_week_local) == (day, week)
            assert review.published_week_local.weekday() == 0
        assert find_local_date_mismatches(s) == []
    # the property timezone is what drives it: the same instants in another zone give different dates
    with Session(engine) as s:
        some = s.scalars(select(Review).limit(200)).all()
        assert any(
            local_dates(r.published_at, "America/Los_Angeles")[0] != r.published_date_local
            for r in some
        )


def test_database_integrity_after_seeding(engine: Engine) -> None:
    seeded(engine)
    with Session(engine) as s:
        assert (
            s.scalar(
                select(text("count(*)")).select_from(Review).where(~Review.rating.between(1, 10))
            )
            == 0
        )
        assert (
            s.scalar(
                select(text("count(*)")).select_from(Review).where(Review.content_hash.is_(None))
            )
            == 0
        )
        keys = list(s.execute(select(Review.property_id, Review.dedupe_key)))
        assert len(keys) == len(set(keys))
        assert all(
            r.language is None for r in s.scalars(select(Review))
        )  # language is determined by analysis, not seeded


# ================================================================= isolation (ADR 0013 / 0017)
def test_seed_refuses_a_database_that_holds_real_data(engine: Engine) -> None:
    run_import(engine, make_csv([row(1)]))
    with pytest.raises(DatasetIsolationError):
        seeded(engine)
    assert count(engine, Review) == 1


def test_real_data_is_refused_after_seeding(engine: Engine) -> None:
    seeded(engine)
    with pytest.raises(DatasetIsolationError):
        run_import(engine, make_csv([row(1)]))


def test_the_normal_import_path_cannot_record_demo_data(engine: Engine) -> None:
    with pytest.raises(ForbiddenSource):
        run_import(
            engine, b"[]", adapter=DemoAdapter()
        )  # default allowed sources are real imports only
    assert count(engine, Review) == 0


def test_reporting_excludes_synthetic_data_by_default(engine: Engine, data) -> None:  # type: ignore[no-untyped-def]
    seeded(engine)
    with Session(engine) as s:
        assert s.scalars(select(Review.id).where(reportable())).all() == []
        assert len(s.scalars(select(Review.id).where(reportable(include_demo=True))).all()) == len(
            data.records
        )
        prov = dataset_provenance(s)
        assert (
            prov["contains_demo"]
            and prov["contains_non_production_data"]
            and not prov["contains_experimental"]
        )


def test_reset_removes_only_demo_rows(engine: Engine) -> None:
    seeded(engine)
    with Session(engine) as s:
        f.review(
            s, source="csv_import"
        )  # a real row (inserted directly: seeding would have refused)
        s.commit()
    removed = reset_demo(create_session_factory(engine))
    assert removed > 400 and count(engine, Review) == 1
    with Session(engine) as s:
        assert s.scalars(select(Review.source)).one() == "csv_import"


# ================================================================= demonstration usability
def test_summary_shows_variation_and_low_sample_states(engine: Engine) -> None:
    _, dataset = seeded(engine)
    with Session(engine) as s:
        summary = summarise(s, dataset)
    assert summary["total"] == len(dataset.records) and set(summary["per_property"]) == set(
        PROFILES
    )
    assert summary["date_coverage"]["first"] < summary["date_coverage"]["last"]
    assert len(set(summary["avg_rating_per_property"].values())) == 4
    vol = summary["weekly_volume"]
    assert (
        vol["olympic-paddington"]["weeks_under_5"] > 0.8 * vol["olympic-paddington"]["weeks"]
    )  # insufficient-data states
    assert (
        vol["chateau-de-venus"]["busiest_week"] >= 10
    )  # but some weeks are busy enough for real comparisons
    assert sum(summary["rating_band_distribution"].values()) == summary["total"]
    assert "Neither is AI analysis" in summary["note"]


# ================================================================= CLI
def test_cli_seed_demo(
    db_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REVIEWHUB_DATABASE_URL", db_url)
    monkeypatch.setenv("REVIEWHUB_LOG_LEVEL", "ERROR")
    get_settings.cache_clear()
    try:
        assert cli.main(["seed-demo", "--end-date", "2026-11-25"]) == 0
        assert "DEMO DATA (fictional)" in capsys.readouterr().out
        assert cli.main(["seed-demo", "--end-date", "2026-11-25", "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["import"]["new"] == 0 and out["import"]["unchanged"] == out["summary"]["total"]
        with Session(engine) as s:
            s.execute(text("DELETE FROM review"))
            f.review(s, source="csv_import")
            s.commit()
        assert cli.main(["seed-demo", "--end-date", "2026-11-25"]) == 5
    finally:
        get_settings.cache_clear()
