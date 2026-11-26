"""Dashboard actions: "Get latest reviews" and "Analyse with AI" (background jobs).

Background jobs are run synchronously here, the collector and the AI provider are fakes:
no test contacts Booking.com or OpenRouter.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from reviewhub.api import background
from reviewhub.api.app import create_app
from reviewhub.core.settings import Settings
from reviewhub.persistence.models import ReviewAnalysis, Run
from reviewhub.persistence.runs import utc_now

from . import factories as f
from .ai_helpers import Fake


def settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(
        _env_file=None,
        collect_log_dir=str(tmp_path / "logs"),
        collect_spool_dir=str(tmp_path / "spool"),
        collect_inbox_dir=str(tmp_path / "inbox"),
        **kw,
    )


AI = {"openrouter_api_key": SecretStr("sk-or-v1-test"), "openrouter_model": "vendor/m:free"}


@pytest.fixture
def sync_jobs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run background jobs inline and record their names."""
    started: list[str] = []

    def run_now(name: str, fn: Callable[[], object]) -> None:
        started.append(name)
        fn()

    monkeypatch.setattr(background, "start_job", run_now)
    return started


def real_review(engine: Engine, **kw: Any) -> None:
    with Session(engine) as s:
        f.review(s, source="booking_public", positive_text="Clean and quiet room", **kw)
        s.commit()


# ============================================================ AI analysis
def test_ai_is_unavailable_without_a_key_and_says_why(engine: Engine, tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), engine=engine)) as c:
        cfg = c.get("/api/v1/analysis/config").json()["data"]
        assert cfg["rule_based"]["can_run"] is True
        assert cfg["ai"]["can_run"] is False and "OPENROUTER_API_KEY" in cfg["ai"]["reason"]
        r = c.post("/api/v1/analysis/runs", json={"method": "ai"})
        assert r.status_code == 409
        assert "sk-or" not in r.text  # never echoes secrets


def test_real_guest_text_needs_the_operator_switch(engine: Engine, tmp_path: Path) -> None:
    real_review(engine)
    with TestClient(create_app(settings(tmp_path, **AI), engine=engine)) as c:
        ai = c.get("/api/v1/analysis/config").json()["data"]["ai"]
        assert ai["configured"] is True and ai["can_run"] is False
        assert "AI_ALLOW_REAL_GUEST_TEXT" in ai["reason"]
        assert "sk-or" not in str(ai)
        assert c.post("/api/v1/analysis/runs", json={"method": "ai"}).status_code == 409


def test_ai_run_starts_in_the_background_and_respects_the_limit(
    engine: Engine, tmp_path: Path, sync_jobs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(4):
        real_review(engine, external_id=f"ai-{i}", dedupe_key=f"ext:ai:{i}")
    fake = Fake(provider="openrouter", model="vendor/m:free")
    monkeypatch.setattr("reviewhub.api.v1.analysis.build_provider", lambda s, name: fake)
    st = settings(tmp_path, ai_allow_real_guest_text=True, ai_requests_per_minute=600, **AI)
    with TestClient(create_app(st, engine=engine)) as c:
        assert c.get("/api/v1/analysis/config").json()["data"]["ai"]["can_run"] is True
        r = c.post("/api/v1/analysis/runs", json={"method": "ai", "limit": 2})
        assert r.status_code == 202 and r.json()["data"]["limit"] == 2
    assert sync_jobs == ["ai-analysis"]
    with Session(engine) as s:
        done = s.scalars(
            select(ReviewAnalysis).where(ReviewAnalysis.provider == "openrouter")
        ).all()
        assert len(done) == 2  # exactly the requested number, not the whole backlog


def test_ai_limit_is_bounded(engine: Engine, tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path, **AI), engine=engine)) as c:
        r = c.post("/api/v1/analysis/runs", json={"method": "ai", "limit": 500})
        assert r.status_code == 422


def test_rule_based_still_runs_inline(engine: Engine, tmp_path: Path) -> None:
    real_review(engine)
    with TestClient(create_app(settings(tmp_path), engine=engine)) as c:
        r = c.post("/api/v1/analysis/runs", json={"method": "rule_based"})
        assert r.status_code == 200 and r.json()["data"]["succeeded"] == 1


# ============================================================ collection
def test_collection_starts_with_the_safe_defaults_then_analyses_new_reviews(
    engine: Engine, tmp_path: Path, sync_jobs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def fake_collect(factory: Any, st: Settings, options: Any, **kw: Any) -> None:
        calls.append(options)
        real_review(engine)  # pretend one new review was collected

    monkeypatch.setattr("reviewhub.collection.service.collect", fake_collect)
    with TestClient(create_app(settings(tmp_path), engine=engine)) as c:
        r = c.post("/api/v1/collection/runs", json={})
        assert r.status_code == 202
    assert sync_jobs == ["collection"]
    (opts,) = calls
    assert (opts.mode, opts.max_pages, opts.full, opts.concurrency) == ("headed", 5, False, 1)
    with Session(engine) as s:  # the new review was analysed offline straight away
        assert s.scalars(select(ReviewAnalysis).where(ReviewAnalysis.is_current)).all()


def test_collection_refuses_to_start_twice_but_ignores_a_dead_run(
    engine: Engine, tmp_path: Path, sync_jobs: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("reviewhub.collection.service.collect", lambda *a, **k: None)
    with Session(engine) as s:
        live = f.run(s, kind="collect", status="running", heartbeat_at=utc_now())
        s.commit()
        live_id = live.id
    with TestClient(create_app(settings(tmp_path), engine=engine)) as c:
        r = c.post("/api/v1/collection/runs", json={"max_pages": 3})
        assert r.status_code == 409 and r.json()["run_id"] == live_id
        with Session(engine) as s:  # a run whose heartbeat stopped long ago does not block
            run = s.get(Run, live_id)
            assert run is not None
            run.heartbeat_at = utc_now() - timedelta(hours=2)
            run.started_at = utc_now() - timedelta(hours=2)
            s.commit()
        assert c.post("/api/v1/collection/runs", json={"max_pages": 3}).status_code == 202


def test_collection_validates_its_options(engine: Engine, tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), engine=engine)) as c:
        assert c.post("/api/v1/collection/runs", json={"max_pages": 0}).status_code == 422
        assert c.post("/api/v1/collection/runs", json={"mode": "stealth"}).status_code == 422
