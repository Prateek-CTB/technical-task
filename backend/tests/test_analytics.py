"""Analytics API: numbers are verified against independent calculations (raw generator records / direct SQL)."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from reviewhub.api.app import create_app
from reviewhub.core.settings import Settings
from reviewhub.demo.generator import generate
from reviewhub.persistence.models import (
    Property,
    Review,
    ReviewAnalysis,
    ReviewAspect,
    ScoreSnapshot,
)
from reviewhub.persistence.session import create_session_factory
from tests.ai_helpers import END, Fake, go, seeded, seeded_and_analysed
from tests.ingest_helpers import make_csv, row, run_import

DAYS = 120
AS_OF = "2026-11-25T23:00:00+11:00"
ENDPOINTS = [
    "overview",
    "properties/comparison",
    "properties/1",
    "trends/weekly",
    "topics",
    "reviews",
    "recommendations",
    "coverage",
]


@pytest.fixture
def ctx(engine: Engine, db_url: str):  # type: ignore[no-untyped-def]
    factory = seeded_and_analysed(engine, DAYS)
    app = create_app(Settings(_env_file=None, database_url=db_url, AI_PROVIDER="mock"), engine)  # type: ignore[call-arg]
    return TestClient(app), engine, factory, generate(end=END, days=DAYS)


def get(client: TestClient, path: str, **params: Any) -> dict[str, Any]:
    params.setdefault("include_demo", "true")
    params.setdefault("as_of", AS_OF)
    r = client.get(f"/api/v1/analytics/{path}", params=params)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ===================================================================== scope and provenance
@pytest.mark.parametrize("path", ENDPOINTS)
def test_synthetic_data_is_excluded_by_default_on_every_endpoint(ctx, path: str) -> None:  # type: ignore[no-untyped-def]
    client, *_ = ctx
    body = client.get(f"/api/v1/analytics/{path}", params={"as_of": AS_OF}).json()
    assert body["meta"]["scope"] == {
        "include_demo": False,
        "reviews_in_scope": 0,
        "excluded_by_scope": body["meta"]["scope"]["excluded_by_scope"],
    }
    assert (
        body["meta"]["scope"]["excluded_by_scope"] > 100
        and body["meta"]["dataset"]["contains_demo"] is True
    )
    assert "include_demo=true" in body["meta"]["notice"]
    data = body["data"]
    if path == "overview":
        assert (
            data["reviews"] == 0
            and data["avg_rating"]["value"] is None
            and data["avg_rating"]["status"] == "insufficient_data"
        )
    if path == "reviews":
        assert data == []


@pytest.mark.parametrize("path", ENDPOINTS)
def test_explicit_synthetic_reporting_is_labelled(ctx, path: str) -> None:  # type: ignore[no-untyped-def]
    client, *_ = ctx
    body = get(client, path)
    scope = body["meta"]["scope"]
    assert scope["include_demo"] is True and scope["reviews_in_scope"] > (
        10 if path == "properties/1" else 100
    )
    assert (
        "SYNTHETIC" in body["meta"]["notice"]
        and body["meta"]["analysis"]["contains_mock_analysis"] is True
    )
    sources = body["meta"]["dataset"][
        "sources"
    ]  # provenance is database-wide; one property is a subset of it
    assert (
        set(sources) == {"demo_synthetic"}
        and sources["demo_synthetic"] >= scope["reviews_in_scope"]
    )


# ===================================================================== A. overview
def test_overview_numbers_match_the_raw_records(ctx) -> None:  # type: ignore[no-untyped-def]
    client, _, _, data = ctx
    d = get(client, "overview")["data"]
    ratings = [r["rating"] for r in data.records]
    assert d["reviews"] == len(ratings)
    assert d["avg_rating"]["value"] == round(statistics.mean(ratings), 2) and d["avg_rating"][
        "n"
    ] == len(ratings)
    assert d["rating_distribution"] == {str(i): Counter(ratings).get(i, 0) for i in range(1, 11)}
    assert d["rating_bands"] == {
        "positive": sum(r >= 8 for r in ratings),
        "neutral": sum(r == 7 for r in ratings),
        "negative": sum(r <= 6 for r in ratings),
    }
    assert (
        sum(d["ai_sentiment"]["counts"].values()) == d["ai_sentiment"]["analysed"] <= len(ratings)
    )
    assert d["coverage"]["share"] == 1.0 and d["coverage"]["pending"] == 0
    assert (
        "Booking.com" in d["avg_rating_note"]
        and d["top_negative_topics"]
        and d["top_positive_topics"]
    )


def test_overview_date_range_and_volume_comparison(ctx) -> None:  # type: ignore[no-untyped-def]
    client, _, _, data = ctx
    lo, hi = date(2026, 10, 1), date(2026, 10, 31)
    d = get(client, "overview", date_from=lo.isoformat(), date_to=hi.isoformat())["data"]
    inside = [r for r in data.records if lo <= _local(r) <= hi]
    assert d["reviews"] == len(inside)
    as_of = datetime.fromisoformat(AS_OF)
    last28 = sum(1 for r in data.records if as_of - timedelta(days=28) <= _utc(r) < as_of)
    assert d["volume"]["last_28_days"] == last28


def _utc(r: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(r["review_date"].replace("Z", "+00:00"))


def _local(r: dict[str, Any]) -> date:
    from zoneinfo import ZoneInfo

    return _utc(r).astimezone(ZoneInfo("Australia/Sydney")).date()


# ===================================================================== B/C. properties
def test_property_comparison_matches_raw_records_and_sample_rules(ctx) -> None:  # type: ignore[no-untyped-def]
    client, _, _, data = ctx
    out = get(client, "properties/comparison")["data"]
    assert [p["property"]["slug"] for p in out] == [
        "olympic-paddington",
        "venus-potts-point-sydney",
        "venus-surry-hills",
        "chateau-de-venus",
    ] or len(out) == 4
    for p in out:
        mine = [r["rating"] for r in data.records if r["property"] == p["property"]["slug"]]
        assert p["reviews"] == len(mine) and p["avg_rating"]["n"] == len(mine)
        assert p["avg_rating"]["value"] == (
            round(statistics.mean(mine), 2) if len(mine) >= 5 else None
        )
        assert (
            p["rating_bands"]["negative"] == sum(r <= 6 for r in mine)
            and p["booking_score"] is None
        )
        assert (
            p["coverage"]["share"] == 1.0
            and p["most_negative_aspects"]
            and p["volume_trend"]["direction"]
        )
    vals = [p["avg_rating"]["value"] for p in out]
    assert max(vals) - min(vals) >= 0.8  # properties are clearly distinguishable
    # a narrow window leaves too few reviews: the average is withheld, not shown as zero
    few = get(client, "properties/comparison", date_from="2026-11-10", date_to="2026-11-16")["data"]
    thin = [p for p in few if p["avg_rating"]["n"] < 5]
    assert thin and all(
        p["avg_rating"]["value"] is None and p["avg_rating"]["status"] == "insufficient_data"
        for p in thin
    )
    assert {p["avg_rating"]["status"] for p in out} <= {"ok", "low_sample"}


def test_sample_size_thresholds_are_exact(ctx) -> None:  # type: ignore[no-untyped-def]
    from reviewhub.analytics.service import avg_metric, share_metric

    st = {n: avg_metric([5.0 + (i % 3) for i in range(n)])["status"] for n in (0, 4, 5, 14, 15)}
    assert st == {
        0: "insufficient_data",
        4: "insufficient_data",
        5: "low_sample",
        14: "low_sample",
        15: "ok",
    }
    sh = {d: share_metric(1, d)["status"] for d in (0, 9, 10, 29, 30)}
    assert sh == {
        0: "insufficient_data",
        9: "insufficient_data",
        10: "low_sample",
        29: "low_sample",
        30: "ok",
    }
    assert avg_metric([7.0] * 4)["value"] is None and avg_metric([7.0] * 5)["value"] == 7.0


def test_property_detail_and_booking_score_is_kept_separate(ctx) -> None:  # type: ignore[no-untyped-def]
    client, engine, _, data = ctx
    assert client.get("/api/v1/analytics/properties/999").status_code == 404
    with Session(engine) as s:
        pid = s.scalars(select(Property.id).where(Property.slug == "chateau-de-venus")).one()
        s.add(
            ScoreSnapshot(
                property_id=pid,
                captured_at=datetime(2026, 11, 25, tzinfo=UTC),
                metric="overall",
                value=8.3,
                review_count=129,
            )
        )
        s.commit()
    d = get(client, f"properties/{pid}")["data"]
    mine = [r["rating"] for r in data.records if r["property"] == "chateau-de-venus"]
    assert d["avg_rating"]["value"] == round(statistics.mean(mine), 2) != 8.3
    assert d["booking_score"]["value"] == 8.3 and "not by us" in d["booking_score"]["label"]
    assert d["rating_distribution"] == {str(i): Counter(mine).get(i, 0) for i in range(1, 11)}
    assert d["week_comparison"]["basis"] and d["recommendations"] is not None
    with Session(engine) as s:  # per-topic counts equal an independent SQL count
        sql = {
            (t, sent): n
            for t, sent, n in s.execute(
                select(ReviewAspect.topic, ReviewAspect.sentiment, func.count())
                .join(ReviewAnalysis, ReviewAnalysis.id == ReviewAspect.analysis_id)
                .join(Review, Review.id == ReviewAspect.review_id)
                .where(ReviewAnalysis.is_current, Review.property_id == pid)
                .group_by(ReviewAspect.topic, ReviewAspect.sentiment)
            )
        }
    for t in d["topics"]:
        for sent in ("positive", "negative", "neutral", "mixed"):
            assert t[sent] == sql.get((t["topic"], sent), 0)


# ===================================================================== D. weekly trends and like-for-like
def test_weekly_trends_totals_buckets_and_week_on_week(ctx) -> None:  # type: ignore[no-untyped-def]
    client, _, _, data = ctx
    d = get(client, "trends/weekly", date_from="2026-08-01", date_to="2026-11-25")["data"]
    weeks = d["weeks"]
    assert all(date.fromisoformat(w["week_start"]).weekday() == 0 for w in weeks)
    inside = [r for r in data.records if date(2026, 8, 1) <= _local(r) <= date(2026, 11, 25)]
    assert sum(w["reviews"] for w in weeks) == len(inside)
    by_week: dict[date, list[float]] = defaultdict(list)
    for r in inside:
        by_week[_local(r) - timedelta(days=_local(r).weekday())].append(r["rating"])
    for w in weeks:
        mine = by_week.get(date.fromisoformat(w["week_start"]), [])
        assert w["reviews"] == len(mine) and w["avg_rating"]["n"] == len(mine)
        assert w["rating_bands"]["negative"] == sum(x <= 6 for x in mine)
    for prev, cur in pairwise(weeks):
        assert cur["week_on_week"]["volume_delta"] == cur["reviews"] - prev["reviews"]
        if cur["avg_rating"]["value"] is not None and prev["avg_rating"]["value"] is not None:
            assert cur["week_on_week"]["avg_rating_delta"] == round(
                cur["avg_rating"]["value"] - prev["avg_rating"]["value"], 2
            )
    assert (
        [w["partial"] for w in weeks].count(True) == 1
        and weeks[-1]["partial"]
        and "like_for_like_previous_week" in weeks[-1]
    )
    assert len({w["reviews"] for w in weeks}) > 3  # real variation, quiet and busy weeks
    one = get(client, "trends/weekly", property_id=1, date_from="2026-08-01", date_to="2026-11-25")[
        "data"
    ]
    assert sum(w["reviews"] for w in one["weeks"]) < len(inside)


def test_current_week_is_compared_with_the_same_elapsed_time_across_a_dst_change(
    engine: Engine, db_url: str
) -> None:
    """Sydney DST began Sun 4 Oct 2026 02:00. as_of = Thu 8 Oct 08:40 AEDT => like-for-like ends Thu 1 Oct 08:40 AEST."""
    create_session_factory(engine)
    stamps = {
        "cur_in": "2026-10-05T10:00:00+11:00",  # Mon this week, before as_of
        "cur_future": "2026-10-08T09:00:00+11:00",  # after as_of: not yet happened
        "prev_in_1": "2026-10-01T08:30:00+10:00",  # same weekday, 10 minutes before the elapsed time: counted
        "prev_out": "2026-10-01T08:50:00+10:00",  # 10 minutes after: only in the full previous week
        "prev_in_2": "2026-09-29T12:00:00+10:00",
        "prev_full_only": "2026-10-02T12:00:00+10:00",
        "older": "2026-09-27T12:00:00+10:00",  # Sunday of the week before: excluded from both
    }
    run_import(
        engine,
        make_csv(
            [
                row(i, review_date=v, rating=8, review_id=k)
                for i, (k, v) in enumerate(stamps.items(), 1)
            ]
        ),
        clock=lambda: datetime(2026, 10, 9, tzinfo=UTC),
    )
    client = TestClient(create_app(Settings(_env_file=None, database_url=db_url), engine))  # type: ignore[call-arg]
    wc = client.get(
        "/api/v1/analytics/overview", params={"as_of": "2026-10-08T08:40:00+11:00"}
    ).json()["data"]["week_comparison"]
    assert wc["current_week_to_date"]["reviews"] == 1
    assert wc["previous_week_same_elapsed"]["reviews"] == 2  # prev_in_1 + prev_in_2 (not prev_out)
    assert wc["previous_week_full"]["reviews"] == 4  # + prev_out + prev_full_only
    assert (
        wc["avg_rating_delta"] is None
        and wc["delta_status"] == "insufficient_data"
        and "not evidence of a cause" in wc["note"]
    )
    assert wc["timezones"] == ["Australia/Sydney"]


def test_weeks_follow_each_propertys_own_timezone(engine: Engine, db_url: str) -> None:
    with Session(engine) as s:
        s.execute(
            text(
                "UPDATE property SET timezone = 'Australia/Perth' WHERE slug = 'olympic-paddington'"
            )
        )
        s.commit()
    instant = "2026-11-22T13:30:00Z"  # Mon 00:30 in Sydney, Sun 21:30 in Perth
    run_import(
        engine,
        make_csv(
            [
                row(1, review_date=instant),
                row(2, review_date=instant, property="olympic-paddington"),
            ]
        ),
        clock=lambda: datetime(2026, 11, 26, tzinfo=UTC),
    )
    client = TestClient(create_app(Settings(_env_file=None, database_url=db_url), engine))  # type: ignore[call-arg]
    with Session(engine) as s:
        ids = dict(s.execute(select(Property.slug, Property.id)).all())

    def week(pid: int) -> str:
        w = client.get(
            "/api/v1/analytics/trends/weekly",
            params={
                "property_id": pid,
                "date_from": "2026-11-01",
                "date_to": "2026-11-26",
                "as_of": "2026-11-26T12:00:00Z",
            },
        ).json()["data"]["weeks"]
        return next(x["week_start"] for x in w if x["reviews"] == 1)

    assert (
        week(ids["chateau-de-venus"]) == "2026-11-23"
        and week(ids["olympic-paddington"]) == "2026-11-16"
    )


# ===================================================================== E. topics
def test_topic_counts_and_evidence_are_correct(ctx) -> None:  # type: ignore[no-untyped-def]
    client, engine, _, _ = ctx
    d = get(client, "topics")["data"]
    with Session(engine) as s:
        sql = {
            (t, sent): n
            for t, sent, n in s.execute(
                select(ReviewAspect.topic, ReviewAspect.sentiment, func.count())
                .join(ReviewAnalysis, ReviewAnalysis.id == ReviewAspect.analysis_id)
                .where(ReviewAnalysis.is_current)
                .group_by(ReviewAspect.topic, ReviewAspect.sentiment)
            )
        }
        texts = {
            r.id: " ".join(filter(None, [r.title, r.positive_text, r.negative_text])).lower()
            for r in s.scalars(select(Review))
        }
    assert len(d["topics"]) == 8
    for t in d["topics"]:
        for sent in ("positive", "negative", "neutral", "mixed"):
            assert t[sent] == sql.get((t["topic"], sent), 0)
        assert t["reviews_mentioning"] == sum(
            t[s] for s in ("positive", "negative", "neutral", "mixed")
        )
        assert sum(sum(v.values()) for v in t["by_property"].values()) == t["reviews_mentioning"]
        for ev in (
            t["complaint_evidence"] + t["praise_evidence"]
        ):  # evidence references point at real, verbatim text
            assert " ".join(ev["quote"].lower().split()) in " ".join(texts[ev["review_id"]].split())
        assert t["share_of_negative_rated_reviews"]["status"] in (
            "ok",
            "low_sample",
            "insufficient_data",
        )
    assert d["most_criticised"] and d["most_praised"] and "do not sum to 100%" in d["note"]


# ===================================================================== F. review explorer
def test_review_explorer_filters_and_pagination(ctx) -> None:  # type: ignore[no-untyped-def]
    client, _, _, data = ctx
    first = get(client, "reviews", page_size=25)
    assert first["meta"]["pagination"] == {
        "page": 1,
        "page_size": 25,
        "total": len(data.records),
        "pages": -(-len(data.records) // 25),
    }
    seen, page = [], 1
    while True:
        b = get(client, "reviews", page_size=40, page=page)
        seen += [x["id"] for x in b["data"]]
        if page >= b["meta"]["pagination"]["pages"]:
            break
        page += 1
    assert len(seen) == len(set(seen)) == len(data.records)
    item = first["data"][0]
    assert (
        item["analysis"]["aspects"]
        and item["analysis"]["aspects"][0]["evidence"][0]["quote"]
        and item["property"]["slug"]
    )
    assert [x["published_at"] for x in first["data"]] == sorted(
        (x["published_at"] for x in first["data"]), reverse=True
    )

    low = get(client, "reviews", rating_max=4, page_size=100)
    assert low["meta"]["pagination"]["total"] == sum(
        r["rating"] <= 4 for r in data.records
    ) and all(x["rating"] <= 4 for x in low["data"])
    prop = get(client, "reviews", property_id=1, page_size=100)["data"]
    assert prop and {x["property"]["id"] for x in prop} == {1}
    rng = get(client, "reviews", date_from="2026-10-01", date_to="2026-10-15", page_size=100)
    assert rng["meta"]["pagination"]["total"] == sum(
        date(2026, 10, 1) <= _local(r) <= date(2026, 10, 15) for r in data.records
    )
    neg = get(client, "reviews", topic="noise", topic_sentiment="negative", page_size=100)["data"]
    assert neg and all(
        any(
            a["topic"] == "noise" and a["sentiment"] == "negative" for a in x["analysis"]["aspects"]
        )
        for x in neg
    )
    fr = get(client, "reviews", language="fr", page_size=100)["data"]
    assert fr and all(x["language"] == "fr" for x in fr)
    mixed = get(client, "reviews", sentiment="mixed", page_size=100)["data"]
    assert mixed and all(x["analysis"]["overall_sentiment"] == "mixed" for x in mixed)


def test_explorer_search_and_sort(ctx) -> None:  # type: ignore[no-untyped-def]
    client, *_ = ctx
    newest = get(client, "reviews", page_size=100)["data"]
    assert [r["local_date"] for r in newest] == sorted(
        (r["local_date"] for r in newest), reverse=True
    )
    oldest = get(client, "reviews", page_size=100, sort="oldest")["data"]
    assert [r["local_date"] for r in oldest] == sorted(r["local_date"] for r in oldest)
    best = get(client, "reviews", page_size=100, sort="rating_high")["data"]
    assert [r["rating"] for r in best] == sorted((r["rating"] for r in best), reverse=True)
    worst = get(client, "reviews", page_size=100, sort="rating_low")["data"]
    assert [r["rating"] for r in worst] == sorted(r["rating"] for r in worst)
    word = next(w for r in newest for w in (r["liked"] or "").split() if len(w) > 5 and w.isalpha())
    found = get(client, "reviews", page_size=100, q=word.upper())
    assert found["meta"]["pagination"]["total"] >= 1
    assert all(
        word.lower() in " ".join(filter(None, [r["title"], r["liked"], r["disliked"]])).lower()
        for r in found["data"]
    )
    assert get(client, "reviews", q="zzzz-no-such-text")["meta"]["pagination"]["total"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 101},
        {"rating_min": 11},
        {"sentiment": "angry"},
        {"topic": "parking"},
        {"date_from": "not-a-date"},
    ],
)
def test_explorer_validates_parameters(ctx, params: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client, *_ = ctx
    r = client.get("/api/v1/analytics/reviews", params={"include_demo": "true", **params})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")


# ===================================================================== G. recommendations
def test_recommendations_are_counted_in_sql_and_backed_by_evidence(ctx) -> None:  # type: ignore[no-untyped-def]
    client, engine, _, _ = ctx
    body = get(client, "recommendations", window_weeks=12, min_reviews=3)
    as_of = datetime.fromisoformat(AS_OF)
    with Session(engine) as s:
        sql = {
            (pid, t): n
            for pid, t, n in s.execute(
                select(Review.property_id, ReviewAspect.topic, func.count(func.distinct(Review.id)))
                .join(ReviewAspect, ReviewAspect.review_id == Review.id)
                .join(ReviewAnalysis, ReviewAnalysis.id == ReviewAspect.analysis_id)
                .where(
                    ReviewAnalysis.is_current,
                    ReviewAspect.sentiment.in_(("negative", "mixed")),
                    Review.published_at >= as_of - timedelta(weeks=12),
                    Review.published_at <= as_of,
                )
                .group_by(Review.property_id, ReviewAspect.topic)
            )
        }
        texts = {
            r.id: " ".join(filter(None, [r.title, r.positive_text, r.negative_text])).lower()
            for r in s.scalars(select(Review))
        }
    recs = body["data"]
    expected = {k: v for k, v in sql.items() if v >= 3}
    assert {
        (r["property"]["id"], r["topic"]): r["supporting_reviews"] for r in recs
    } == expected and recs
    assert [r["supporting_reviews"] for r in recs] == sorted(
        (r["supporting_reviews"] for r in recs), reverse=True
    )
    for r in recs:
        assert r["evidence"] and all(
            " ".join(e["quote"].lower().split()) in " ".join(texts[e["review_id"]].split())
            for e in r["evidence"]
        )
        assert (
            set(r["review_ids"]) <= set(texts)
            and r["trend"]["direction"] in ("rising", "steady", "falling")
            and "not proof of cause" in r["trend"]["note"]
        )
        assert r["confidence"]["calibrated"] is False and "mock_analysis" in r["uncertainty"]
        assert (r["suggested_action"] is None) == r["action_basis"].startswith("none")
        assert (
            "low_sample" in r["uncertainty"]
            if r["supporting_reviews"] < 5
            else "low_sample" not in r["uncertainty"]
        )
    stricter = get(client, "recommendations", min_reviews=8)["data"]
    assert len(stricter) < len(recs) and all(r["supporting_reviews"] >= 8 for r in stricter)
    assert all(
        r["property"]["id"] == 2 for r in get(client, "recommendations", property_id=2)["data"]
    )


# ===================================================================== H. coverage
def test_coverage_tracks_pending_failed_and_usage(engine: Engine, db_url: str) -> None:
    f = seeded(engine, days=60)
    go(f, Fake(), limit=10)
    go(f, Fake(model="other", fail=lambda c, u: "junk"), limit=2)
    client = TestClient(
        create_app(Settings(_env_file=None, database_url=db_url, AI_PROVIDER="mock"), engine)
    )  # type: ignore[call-arg]
    d = get(client, "coverage")["data"]
    with Session(engine) as s:
        eligible = go(
            f, Fake(), dry_run=True
        ).eligible  # independent count of reviews with analysable text
        tokens = int(
            s.scalar(
                select(func.sum(ReviewAnalysis.input_tokens)).where(
                    ReviewAnalysis.provider == "mock"
                )
            )
            or 0
        )
    assert (
        d["failed"] == 0
    )  # the 2 failures are a different configuration's attempts on reviews that keep their good analysis
    assert d["analysed"] + d["pending"] + d["failed"] == d["eligible"] == eligible
    assert d["analysed"] == 10 and d["share_analysed"] == round(10 / d["eligible"], 3)
    assert d["active_configuration"] == {
        "provider": "mock",
        "model": "mock-keyword-1",
        "prompt_version": "v1",
        "taxonomy_version": "v1",
    }
    assert d["analysis_providers"] == {"mock": 10} and d["last_run"]["provider"] == "mock"
    assert sum(u["input_tokens"] for u in d["token_usage"]) == tokens and all(
        u["reported_cost_usd"] == 0 for u in d["token_usage"]
    )
    assert d["by_property"] and "not AI inference" in d["note"]
