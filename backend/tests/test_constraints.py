"""Database constraint tests (run on SQLite and PostgreSQL).

Each violation is attempted in its own session so one failed statement cannot
poison the next assertion.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from reviewhub.persistence.models import Review, ReviewAnalysis, ReviewAspect, Run
from tests import factories as f


def rejected(engine: Engine, fn: Callable[[Session], Any]) -> None:
    """Assert the database refuses what ``fn`` does."""
    with Session(engine) as s:
        with pytest.raises(IntegrityError):
            fn(s)
            s.flush()
        s.rollback()


def committed(engine: Engine, fn: Callable[[Session], Any]) -> Any:
    return f.commit_in_new_session(engine, fn)


# --------------------------------------------------------------------- reviews
def test_review_dedupe_key_is_unique_per_property(engine: Engine) -> None:
    committed(engine, lambda s: f.review(s, dedupe_key="ext:booking:abc"))
    rejected(engine, lambda s: f.review(s, dedupe_key="ext:booking:abc"))


def test_same_dedupe_key_allowed_at_a_different_property(engine: Engine) -> None:
    committed(engine, lambda s: f.review(s, dedupe_key="ext:booking:abc"))
    committed(
        engine,
        lambda s: f.review(
            s, dedupe_key="ext:booking:abc", property_id=f.property_id(s, "venus-surry-hills")
        ),
    )


@pytest.mark.parametrize("rating", [0, 0.9, 10.1, 11, -3])
def test_rating_outside_1_to_10_rejected(engine: Engine, rating: float) -> None:
    rejected(engine, lambda s: f.review(s, rating=rating))


@pytest.mark.parametrize("rating", [1, 7.5, 10])
def test_rating_inside_range_accepted(engine: Engine, rating: float) -> None:
    committed(engine, lambda s: f.review(s, rating=rating))


def test_unknown_source_rejected(engine: Engine) -> None:
    rejected(engine, lambda s: f.review(s, source="scraped_somewhere"))


def test_foreign_keys_are_enforced(engine: Engine) -> None:
    # On SQLite this only passes if PRAGMA foreign_keys=ON is applied per connection.
    rejected(engine, lambda s: f.review(s, property_id=999_999))


def test_published_at_round_trips_as_utc(engine: Engine) -> None:
    sydney = timezone(timedelta(hours=11))
    stamp = datetime(2026, 11, 23, 0, 30, tzinfo=sydney)  # = 2026-11-22 13:30 UTC
    rid = committed(engine, lambda s: f.review(s, published_at=stamp).id)
    with Session(engine) as s:
        loaded = s.get(Review, rid)
        assert loaded is not None
        assert loaded.published_at == stamp
        assert loaded.published_at.utcoffset() == timedelta(0)
        assert loaded.published_at == datetime(2026, 11, 22, 13, 30, tzinfo=UTC)


def test_naive_datetime_is_refused(engine: Engine) -> None:
    with Session(engine) as s, pytest.raises((ValueError, StatementError)):
        f.review(s, published_at=datetime(2026, 11, 26, 1, 0))


def test_multilingual_text_round_trips(engine: Engine) -> None:
    text_ja = "駅から近くて便利でした。"
    rid = committed(engine, lambda s: f.review(s, positive_text=text_ja, language="ja").id)
    with Session(engine) as s:
        loaded = s.get(Review, rid)
        assert loaded is not None
        assert loaded.positive_text == text_ja


# --------------------------------------------------------------------- analysis idempotency (ADR 0011)
def test_duplicate_llm_analysis_rejected(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    committed(engine, lambda s: f.analysis(s, rid))
    rejected(engine, lambda s: f.analysis(s, rid))


def test_duplicate_lexicon_analysis_rejected(engine: Engine) -> None:
    """The regression ADR 0011 exists for: no NULL in the key, so duplicates cannot slip through."""
    rid = committed(engine, lambda s: f.review(s).id)
    committed(engine, lambda s: f.lexicon_analysis(s, rid))
    rejected(engine, lambda s: f.lexicon_analysis(s, rid))


@pytest.mark.parametrize(
    "change",
    [
        {"input_hash": "b" * 64},  # review text edited
        {"model_id": "other-model"},  # model changed
        {"prompt_version": "2"},  # prompt changed
        {"taxonomy_version": "2"},  # taxonomy changed
    ],
)
def test_new_version_triple_or_hash_allows_a_new_analysis(
    engine: Engine, change: dict[str, str]
) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    committed(engine, lambda s: f.analysis(s, rid))
    committed(engine, lambda s: f.analysis(s, rid, **change))


def test_key_columns_cannot_be_null(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    for column in ("model_id", "provider", "prompt_version", "taxonomy_version", "input_hash"):
        rejected(engine, lambda s, c=column: f.analysis(s, rid, **{c: None}))


def test_lexicon_analysis_must_use_builtin_provider(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    rejected(engine, lambda s: f.lexicon_analysis(s, rid, provider="anthropic"))


def test_ok_analysis_requires_a_sentiment_but_failed_does_not(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    rejected(engine, lambda s: f.analysis(s, rid, status="ok", overall_sentiment=None))
    committed(
        engine, lambda s: f.analysis(s, rid, status="failed", overall_sentiment=None, error="boom")
    )


def test_invalid_sentiment_and_confidence_rejected(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    rejected(engine, lambda s: f.analysis(s, rid, overall_sentiment="angry"))
    rejected(engine, lambda s: f.analysis(s, rid, confidence=1.5))


def test_at_most_one_current_analysis_per_review(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    committed(engine, lambda s: f.lexicon_analysis(s, rid, is_current=True))
    # A second, non-current analysis is fine ...
    committed(engine, lambda s: f.analysis(s, rid))
    # ... a second current one is not.
    rejected(engine, lambda s: f.analysis(s, rid, model_id="other", is_current=True))


def test_current_flag_can_move_to_a_newer_analysis(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    old_id = committed(engine, lambda s: f.lexicon_analysis(s, rid, is_current=True).id)

    def promote(s: Session) -> None:
        old = s.get(ReviewAnalysis, old_id)
        assert old is not None
        old.is_current = False
        s.flush()
        f.analysis(s, rid, is_current=True)

    committed(engine, promote)
    with Session(engine) as s:
        current = s.scalars(select(ReviewAnalysis).where(ReviewAnalysis.is_current)).all()
        assert [a.analyzer for a in current] == ["llm"]


# --------------------------------------------------------------------- aspects: consistency with parent
def test_aspect_must_match_its_analysis_review(engine: Engine) -> None:
    def setup(s: Session) -> tuple[int, int, int]:
        r1, r2 = f.review(s), f.review(s)
        a1 = f.analysis(s, r1.id)
        return a1.id, r1.id, r2.id

    analysis_id, review_1, review_2 = committed(engine, setup)
    committed(engine, lambda s: f.aspect(s, analysis_id, review_1))  # consistent
    rejected(
        engine, lambda s: f.aspect(s, analysis_id, review_2, topic="noise")
    )  # review_id disagrees


def test_aspect_cannot_reference_missing_analysis(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    rejected(engine, lambda s: f.aspect(s, 999_999, rid))


def test_aspect_topic_and_sentiment_values_checked(engine: Engine) -> None:
    def setup(s: Session) -> tuple[int, int]:
        r = f.review(s)
        return f.analysis(s, r.id).id, r.id

    aid, rid = committed(engine, setup)
    rejected(engine, lambda s: f.aspect(s, aid, rid, topic="parking"))
    rejected(engine, lambda s: f.aspect(s, aid, rid, sentiment="meh"))


def test_one_aspect_per_topic_per_analysis(engine: Engine) -> None:
    def setup(s: Session) -> tuple[int, int]:
        r = f.review(s)
        return f.analysis(s, r.id).id, r.id

    aid, rid = committed(engine, setup)
    committed(engine, lambda s: f.aspect(s, aid, rid, topic="noise"))
    rejected(engine, lambda s: f.aspect(s, aid, rid, topic="noise", sentiment="positive"))


def test_deleting_an_analysis_deletes_its_aspects(engine: Engine) -> None:
    def setup(s: Session) -> tuple[int, int]:
        r = f.review(s)
        a = f.analysis(s, r.id)
        f.aspect(s, a.id, r.id)
        return a.id, r.id

    aid, _ = committed(engine, setup)
    committed(engine, lambda s: s.execute(delete(ReviewAnalysis).where(ReviewAnalysis.id == aid)))
    with Session(engine) as s:
        assert s.scalars(select(ReviewAspect)).all() == []


def test_deleting_a_review_cascades_to_analyses_and_aspects(engine: Engine) -> None:
    def setup(s: Session) -> int:
        r = f.review(s)
        a = f.analysis(s, r.id)
        f.aspect(s, a.id, r.id)
        return r.id

    rid = committed(engine, setup)
    committed(engine, lambda s: s.execute(delete(Review).where(Review.id == rid)))
    with Session(engine) as s:
        assert s.scalars(select(ReviewAnalysis)).all() == []
        assert s.scalars(select(ReviewAspect)).all() == []


def test_aspect_evidence_keeps_unicode(engine: Engine) -> None:
    def setup(s: Session) -> int:
        r = f.review(s)
        a = f.analysis(s, r.id)
        return f.aspect(
            s, a.id, r.id, topic="location", evidence=[{"field": "liked", "quote": "駅から近い"}]
        ).id

    aspect_id = committed(engine, setup)
    with Session(engine) as s:
        loaded = s.get(ReviewAspect, aspect_id)
        assert loaded is not None
        assert loaded.evidence[0]["quote"] == "駅から近い"


# --------------------------------------------------------------------- single-flight (ADR 0012)
def test_same_scope_cannot_run_twice(engine: Engine) -> None:
    committed(engine, lambda s: f.run(s, scope_key="import:aaa"))
    rejected(engine, lambda s: f.run(s, scope_key="import:aaa"))


def test_different_import_files_run_in_parallel(engine: Engine) -> None:
    committed(engine, lambda s: f.run(s, scope_key="import:aaa"))
    committed(engine, lambda s: f.run(s, scope_key="import:bbb"))  # unrelated file is not blocked


def test_collect_import_and_analyse_do_not_block_each_other(engine: Engine) -> None:
    committed(
        engine,
        lambda s: f.run(
            s, kind="collect", source="booking_public", scope_key="collect:booking_public"
        ),
    )
    committed(engine, lambda s: f.run(s, kind="import", scope_key="import:aaa"))
    committed(
        engine, lambda s: f.run(s, kind="analyse", source="llm", scope_key="analyse:m1:p1:t1")
    )
    committed(
        engine, lambda s: f.run(s, kind="analyse", source="llm", scope_key="analyse:m2:p1:t1")
    )


@pytest.mark.parametrize("final", ["success", "partial", "failed", "blocked", "abandoned"])
def test_finished_run_frees_its_scope(engine: Engine, final: str) -> None:
    run_id = committed(
        engine, lambda s: f.run(s, scope_key="collect:booking_public", kind="collect").id
    )

    def finish(s: Session) -> None:
        r = s.get(Run, run_id)
        assert r is not None
        r.status = final

    committed(engine, finish)
    committed(engine, lambda s: f.run(s, scope_key="collect:booking_public", kind="collect"))


def test_run_kind_and_status_values_checked(engine: Engine) -> None:
    rejected(engine, lambda s: f.run(s, kind="scrape", scope_key="x:1"))
    rejected(engine, lambda s: f.run(s, status="paused", scope_key="x:2"))


def test_run_scope_key_is_required(engine: Engine) -> None:
    rejected(engine, lambda s: f.run(s, scope_key=None))


# --------------------------------------------------------------------- data quality
def test_only_one_open_issue_per_fingerprint(engine: Engine) -> None:
    issue_id = committed(engine, lambda s: f.issue(s, fingerprint="dup-1").id)
    rejected(engine, lambda s: f.issue(s, fingerprint="dup-1"))

    from reviewhub.persistence.models import DataQualityIssue

    def resolve(s: Session) -> None:
        obj = s.get(DataQualityIssue, issue_id)
        assert obj is not None
        obj.resolved_at = f.NOW

    committed(engine, resolve)
    committed(
        engine, lambda s: f.issue(s, fingerprint="dup-1")
    )  # may be raised again once resolved


def test_issue_severity_checked(engine: Engine) -> None:
    rejected(engine, lambda s: f.issue(s, severity="catastrophic"))


# --------------------------------------------------------------------- failed analyses never reported (F4)
def test_failed_analysis_cannot_be_current(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    rejected(
        engine,
        lambda s: f.analysis(
            s, rid, status="failed", overall_sentiment=None, error="boom", is_current=True
        ),
    )


def test_needs_review_analysis_may_be_current(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    committed(engine, lambda s: f.analysis(s, rid, status="needs_review", is_current=True))


def test_failed_retry_does_not_displace_the_current_analysis(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    good = committed(engine, lambda s: f.analysis(s, rid, is_current=True).id)
    committed(
        engine,
        lambda s: f.analysis(
            s, rid, model_id="retry", status="failed", overall_sentiment=None, error="timeout"
        ),
    )
    with Session(engine) as s:
        current = s.scalars(select(ReviewAnalysis).where(ReviewAnalysis.is_current)).all()
        assert [a.id for a in current] == [good]


def test_cannot_promote_an_existing_analysis_to_failed_while_current(engine: Engine) -> None:
    rid = committed(engine, lambda s: f.review(s).id)
    aid = committed(engine, lambda s: f.analysis(s, rid, is_current=True).id)

    def degrade(s: Session) -> None:
        a = s.get(ReviewAnalysis, aid)
        assert a is not None
        a.status = "failed"
        a.overall_sentiment = None
        s.flush()

    rejected(engine, degrade)
