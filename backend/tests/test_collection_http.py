"""The HTTP + structured-data strategy, WAF/CAPTCHA/denied detection, and collection status.

Restricted external pages are FIXTURES (tests/fixtures/): the AWS WAF challenge page is the live
response captured on 2026-11-26 with its per-session values redacted. No test contacts any site.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from reviewhub.api.app import create_app
from reviewhub.collection.http_page import USER_AGENT, PublicPageCollector, collect_http
from reviewhub.collection.signals import (
    SITE_DECISIONS,
    CollectionError,
    Outcome,
    Signals,
    classify,
    looks_like_waf_challenge,
)
from reviewhub.collection.status import block_reason, collection_status
from reviewhub.collection.structured import extract_structured
from reviewhub.core.settings import Settings
from reviewhub.persistence.models import Run, RunProperty, ScoreSnapshot
from reviewhub.persistence.session import create_session_factory

FIXTURES = Path(__file__).parent / "fixtures"
WAF_PAGE = (FIXTURES / "booking_waf_challenge.html").read_text(encoding="utf-8")
PROPERTY_PAGE = (FIXTURES / "booking_property_jsonld.html").read_text(encoding="utf-8")
URL = "https://www.booking.com/hotel/au/olympic-paddington.en-gb.html"

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def fetch(handler: Handler) -> object:
    return PublicPageCollector(client(handler)).fetch(URL, "olympic-paddington")


def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        collect_log_dir=str(tmp_path / "logs"),
        collect_spool_dir=str(tmp_path / "spool"),
        collect_inbox_dir=str(tmp_path / "inbox"),
        collect_property_delay_min=0,
        collect_property_delay_max=0,
        collect_backoff_base_seconds=0,
        collect_max_attempts=3,
    )


# ============================================================ structured data (pure)
def test_extracts_the_published_score_and_count_from_json_ld() -> None:
    data = extract_structured(PROPERTY_PAGE, "olympic-paddington")
    assert (data.score, data.review_count, data.best_rating) == (6.4, 129, 10.0)
    assert data.name == "Olympic Hotel Paddington"
    assert data.blocks == 1  # the deliberately broken block is skipped, not fatal
    assert data.reviews == []


def test_normalises_other_scales_reads_graphs_and_drops_author_names() -> None:
    html = """<script type="application/ld+json">{"@graph":[
      {"@type":["Hotel","LocalBusiness"],"aggregateRating":{"ratingValue":"4,2","bestRating":5,"ratingCount":"88"}},
      {"@type":"Review","author":{"@type":"Person","name":"SECRET NAME"},"datePublished":"2026-11-02",
       "reviewRating":{"ratingValue":4,"bestRating":5},"reviewBody":"Clean and quiet"}]}</script>"""
    data = extract_structured(html, "p")
    assert data.score == 8.4 and data.review_count == 88
    assert data.reviews == [
        {
            "property": "p",
            "rating": 8.0,
            "review_date": "2026-11-02",
            "title": "",
            "liked": "Clean and quiet",
            "disliked": "",
            "language": "",
        }
    ]
    assert "SECRET NAME" not in json.dumps(data.reviews)


def test_a_page_without_structured_data_yields_nothing() -> None:
    data = extract_structured("<html><body>hello</body></html>")
    assert (data.blocks, data.score, data.reviews) == (0, None, [])


# ============================================================ challenge detection (pure)
def test_live_waf_challenge_page_is_recognised() -> None:
    assert looks_like_waf_challenge(WAF_PAGE)


def test_a_normal_page_that_loads_the_waf_script_is_not_a_challenge() -> None:
    assert not looks_like_waf_challenge(PROPERTY_PAGE)


def test_waf_action_header_alone_is_a_challenge() -> None:
    assert looks_like_waf_challenge("", {"X-Amzn-Waf-Action": "challenge"})
    assert not looks_like_waf_challenge("", {"X-Amzn-Waf-Action": "allow"})


def test_waf_challenge_is_a_site_decision_never_retried() -> None:
    assert classify(Signals(http_status=202, waf_challenge=True)) == Outcome.WAF_CHALLENGE
    err = CollectionError(Outcome.WAF_CHALLENGE, "challenge")
    assert err.site_decision and not err.retryable and err.status == "blocked"
    assert Outcome.WAF_CHALLENGE in SITE_DECISIONS


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("AWS WAF challenge page (HTTP 202) served", "aws_waf_challenge"),
        ("a human-verification challenge (CAPTCHA) is being shown", "captcha"),
        (
            'the site refused the review list to this browser ("Request not supported")',
            "headless_refused",
        ),
        ("access denied (HTTP 403)", "access_denied"),
        ("skipped: ... (cooldown); no request made.", "cooldown"),
        (
            "not requested: the site answered an earlier property with waf_challenge",
            "aws_waf_challenge",
        ),
        ("stopped: another property was refused", "circuit_breaker"),
        (None, None),
    ],
)
def test_block_reasons_are_named_for_the_dashboard(message: str | None, reason: str | None) -> None:
    assert block_reason(message) == reason


# ============================================================ PublicPageCollector (mock transport)
def test_live_shaped_waf_challenge_is_blocked_with_its_reason_and_honest_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(202, html=WAF_PAGE)

    c = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    with pytest.raises(CollectionError) as exc:
        PublicPageCollector(c).fetch(URL, "olympic-paddington")
    assert exc.value.outcome == Outcome.WAF_CHALLENGE
    assert "deliberately does not" in str(exc.value)
    assert seen[0].headers["user-agent"].startswith("ReviewHub/")  # no browser impersonation


def test_property_page_returns_structured_data() -> None:
    page = fetch(lambda r: httpx.Response(200, html=PROPERTY_PAGE))
    assert page.data.score == 6.4  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        (httpx.Response(403, html="<h1>Access denied</h1>"), Outcome.BLOCKED),
        (
            # a CAPTCHA served with HTTP 200 is still a site decision, never "layout changed"
            httpx.Response(200, html="<p>Please verify you are human (captcha)</p>"),
            Outcome.CAPTCHA,
        ),
        (httpx.Response(429, headers={"Retry-After": "30"}, html=""), Outcome.RATE_LIMITED),
        (httpx.Response(503, html="down"), Outcome.TRANSIENT),
        (
            httpx.Response(200, html="<html><body>redesigned</body></html>"),
            Outcome.STRUCTURE_CHANGED,
        ),
    ],
)
def test_every_other_answer_is_classified(response: httpx.Response, outcome: Outcome) -> None:
    with pytest.raises(CollectionError) as exc:
        fetch(lambda r: response)
    assert exc.value.outcome == outcome


def test_captcha_page_with_a_challenge_status_is_captcha() -> None:
    with pytest.raises(CollectionError) as exc:
        fetch(lambda r: httpx.Response(405, html="<p>Please verify you are human</p>"))
    assert exc.value.outcome == Outcome.CAPTCHA


def test_rate_limit_carries_retry_after() -> None:
    with pytest.raises(CollectionError) as exc:
        fetch(lambda r: httpx.Response(429, headers={"Retry-After": "30"}))
    assert exc.value.retryable and exc.value.retry_after == 30.0


def test_unexpected_redirect_is_reported_not_parsed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "olympic" in str(req.url):
            return httpx.Response(
                302, headers={"Location": "https://www.booking.com/searchresults.html"}
            )
        return httpx.Response(200, html=PROPERTY_PAGE)

    with pytest.raises(CollectionError) as exc:
        fetch(handler)
    assert exc.value.outcome == Outcome.STRUCTURE_CHANGED
    assert "unexpected redirect" in str(exc.value)


def test_network_errors_are_transient() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(CollectionError) as exc:
        fetch(boom)
    assert exc.value.outcome == Outcome.TRANSIENT and exc.value.retryable


# ============================================================ collect_http runs (DB)
def test_a_waf_challenge_stops_the_run_after_one_request_and_starts_a_cooldown(
    engine: Engine, tmp_path: Path
) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(202, html=WAF_PAGE)

    f, st = create_session_factory(engine), settings(tmp_path)
    rep = collect_http(f, st, client=client(handler), sleep=lambda _: None)
    assert rep.status == "blocked" and len(calls) == 1  # circuit breaker: nothing else requested
    assert [p["status"] for p in rep.properties] == ["blocked"] * 4
    assert rep.properties[0]["outcome"] == "waf_challenge"
    assert all("not requested" in (p["error"] or "") for p in rep.properties[1:])
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(ScoreSnapshot)) == 0
        assert {rp.status for rp in s.scalars(select(RunProperty))} == {"blocked"}

    again = collect_http(f, st, client=client(handler), sleep=lambda _: None)
    assert again.status == "blocked" and "no request made" in (again.note or "")
    assert len(calls) == 1  # the cooldown made zero requests

    with Session(engine) as s:
        status = collection_status(s, st, datetime.now(UTC))["data"]
    http = next(x for x in status["strategies"] if x["key"] == "http_structured")
    assert http["state"] == "cooling_down" and http["block_reason"] == "aws_waf_challenge"
    assert http["next_eligible_at"] > datetime.now(UTC) + timedelta(hours=23)


def test_success_records_the_published_score_and_retries_a_transient_error(
    engine: Engine, tmp_path: Path
) -> None:
    attempts: dict[str, int] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        key = req.url.path
        attempts[key] = attempts.get(key, 0) + 1
        if attempts[key] == 1 and "chateau" in key:
            return httpx.Response(503, html="busy")  # transient: retried with backoff
        return httpx.Response(200, html=PROPERTY_PAGE)

    f, st = create_session_factory(engine), settings(tmp_path)
    rep = collect_http(f, st, client=client(handler), sleep=lambda _: None)
    assert rep.status == "success"
    assert max(attempts.values()) == 2
    with Session(engine) as s:
        snaps = s.scalars(select(ScoreSnapshot)).all()
        assert len(snaps) == 4 and {x.value for x in snaps} == {6.4}
        run = s.scalars(select(Run).where(Run.id == rep.run_id)).one()
        assert run.status == "success" and (run.params or {})["strategy"] == "http_structured"
        status = collection_status(s, st, datetime.now(UTC))["data"]
    assert all(p["booking_published"]["score"] == 6.4 for p in status["properties"])
    assert all(p["last_success"]["strategy"] == "http_structured" for p in status["properties"])


# ============================================================ API
def test_collection_status_properties_and_analysis_endpoints(
    engine: Engine, tmp_path: Path
) -> None:
    app = create_app(settings(tmp_path), engine=engine)
    with TestClient(app) as c:
        st = c.get("/api/v1/ingestion/collection-status")
        assert st.status_code == 200
        body = st.json()["data"]
        assert [p["property"]["slug"] for p in body["properties"]] == [
            "olympic-paddington",
            "venus-potts-point-sydney",
            "venus-surry-hills",
            "chateau-de-venus",
        ]
        assert {s["key"] for s in body["strategies"]} == {
            "browser_headed",
            "browser_headless",
            "http_structured",
        }
        assert all(s["state"] == "never_run" for s in body["strategies"])
        props = c.get("/api/v1/properties").json()["data"]
        assert len(props) == 4 and {"id", "slug", "name"} <= set(props[0])
        run = c.post("/api/v1/analysis/runs", json={"method": "rule_based"})
        assert run.status_code == 200 and run.json()["data"]["eligible"] == 0


# ============================================================ committed real sample (CLI)
def test_sample_load_is_booking_public_idempotent_and_exports_round_trip(
    db_url: str,
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from reviewhub import cli
    from reviewhub.core.settings import get_settings
    from reviewhub.persistence.models import Review

    sample = tmp_path / "sample.json"
    records = [
        {
            "property": "olympic-paddington",
            "rating": 9,
            "review_date": "2026-11-20",
            "title": "Good",
            "liked": "Great location",
            "disliked": "Small room",
            "traveller_type": "Couple",
            "room_type": "Double Room",
            "nights": 2,
        },
        {
            "property": "chateau-de-venus",
            "rating": 3,
            "review_date": "2026-11-21",
            "title": "",
            "liked": "",
            "disliked": "Dirty bathroom",
            "traveller_type": "",
            "room_type": "",
            "nights": "",
        },
    ]
    sample.write_text(json.dumps(records), encoding="utf-8")
    monkeypatch.setenv("REVIEWHUB_DATABASE_URL", db_url)
    monkeypatch.setenv("REVIEWHUB_LOG_LEVEL", "ERROR")
    get_settings.cache_clear()
    try:
        assert cli.main(["sample", "load", "--file", str(sample)]) == 0
        assert "new=2" in capsys.readouterr().out
        assert cli.main(["sample", "load", "--file", str(sample)]) == 0
        assert "new=0" in capsys.readouterr().out  # de-duplicated on re-load
        with Session(engine) as s:
            assert {r.source for r in s.scalars(select(Review))} == {"booking_public"}
        out = tmp_path / "export.json"
        assert cli.main(["sample", "export", "--file", str(out)]) == 0
        exported = json.loads(out.read_text(encoding="utf-8"))
        assert sorted((r["property"], r["liked"], r["disliked"]) for r in exported) == sorted(
            (r["property"], r["liked"], r["disliked"]) for r in records
        )
        assert not any("name" in k for r in exported for k in r)  # no reviewer identity fields
    finally:
        get_settings.cache_clear()
