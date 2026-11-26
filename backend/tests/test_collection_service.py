"""Collection runs end to end: fake pages -> spool -> import pipeline -> run history.

No browser and no network. Runs on SQLite and PostgreSQL through the shared fixtures.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from reviewhub.collection.engine import Page, SourceInfo
from reviewhub.collection.feeds import fetch_feed, import_feed, import_inbox
from reviewhub.collection.policy import RetryPolicy
from reviewhub.collection.service import CollectOptions, collect
from reviewhub.collection.signals import CollectionError, Outcome
from reviewhub.core.settings import Settings
from reviewhub.persistence.models import Property, Review, Run, RunProperty
from reviewhub.persistence.session import create_session_factory

SLUGS = ["olympic-paddington", "chateau-de-venus"]


def rec(slug: str, day: int, text: str | None = None) -> dict[str, Any]:
    return {
        "property": slug,
        "rating": 8,
        "review_date": f"2026-11-{day:02d}",
        "title": f"Title {day}",
        "liked": text or f"Invented praise from day {day} at {slug}.",
        "disliked": "",
    }


class ScriptedSource:
    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        fail: CollectionError | None = None,
        die_after: int | None = None,
    ):
        self.pages, self.fail, self.die_after = pages, fail, die_after
        self.i = 0
        self.start_pages: list[int] = []

    def open(self, start_page: int = 1) -> SourceInfo:
        self.start_pages.append(start_page)
        if self.fail:
            raise self.fail
        self.i = start_page - 1
        return SourceInfo(headline_total=50, headline_score=8.1, sorted_newest=True)

    def read_current(self) -> Page:
        if self.die_after is not None and self.i + 1 > self.die_after:
            raise KeyboardInterrupt  # the process is killed between pages
        return Page(self.i + 1, self.pages[self.i], has_next=self.i + 1 < len(self.pages))

    def advance(self) -> bool:
        if self.i + 1 >= len(self.pages):
            return False
        self.i += 1
        return True

    def close(self) -> None:
        pass


class Harness:
    def __init__(self, engine: Engine, tmp_path: Path) -> None:
        self.factory: sessionmaker[Session] = create_session_factory(engine)
        self.settings = Settings(
            _env_file=None,
            collect_spool_dir=str(tmp_path / "spool"),
            collect_log_dir=str(tmp_path / "logs"),
            collect_inbox_dir=str(tmp_path / "inbox"),
            collect_profile_dir="",
            collect_page_delay_min=0,
            collect_page_delay_max=0,
            collect_property_delay_min=0,
            collect_property_delay_max=0,
            collect_backoff_base_seconds=0,
        )
        self.sources: dict[str, ScriptedSource] = {}
        self.created: list[str] = []

    def worker_factory(self) -> Any:
        @contextmanager
        def worker(idx: int) -> Iterator[Any]:
            def make(prop: Property) -> ScriptedSource:
                self.created.append(prop.slug)
                return self.sources[prop.slug]

            yield make

        return worker

    def run(self, **opts: Any) -> Any:
        options = CollectOptions(slugs=opts.pop("slugs", SLUGS), **opts)
        return collect(
            self.factory,
            self.settings,
            options,
            worker_factory=self.worker_factory(),
            sleep=lambda _: None,
        )

    def count(self) -> int:
        with self.factory() as s:
            return int(s.scalar(select(func.count()).select_from(Review)) or 0)


@pytest.fixture
def h(engine: Engine, tmp_path: Path) -> Harness:
    return Harness(engine, tmp_path)


def test_collects_imports_and_records_history(h: Harness, tmp_path: Path) -> None:
    for slug in SLUGS:
        h.sources[slug] = ScriptedSource([[rec(slug, 20), rec(slug, 19)], [rec(slug, 18)]])
    rep = h.run()
    assert rep.status == "success"
    assert h.count() == 6
    with h.factory() as s:
        run = s.get(Run, rep.run_id)
        assert run is not None
        assert (run.status, run.source, run.items_ok) == ("success", "booking_public", 2)
        rows = s.scalars(select(RunProperty).where(RunProperty.run_id == rep.run_id)).all()
        assert {r.status for r in rows} == {"success"}
        assert all(r.pages == 2 and r.new == 3 and r.reported_headline_total == 50 for r in rows)
    assert list((tmp_path / "spool").glob("*.jsonl")) == []  # imported -> spool cleaned
    log = (tmp_path / "logs" / f"run-{rep.run_id}.jsonl").read_text().splitlines()
    events = [json.loads(line)["event"] for line in log]
    assert events[0] == "run_start" and events[-1] == "run_end" and "page" in events


def test_rerun_and_overlapping_pages_create_no_duplicates(h: Harness) -> None:
    slug = SLUGS[0]
    # A new review published mid-walk shifts page 2 so it repeats the last card of page 1.
    pages = [[rec(slug, 20), rec(slug, 19)], [rec(slug, 19), rec(slug, 18)]]
    h.sources[slug] = ScriptedSource(pages)
    h.run(slugs=[slug])
    assert h.count() == 3
    h.sources[slug] = ScriptedSource(pages)
    rep = h.run(slugs=[slug])
    assert h.count() == 3
    assert rep.properties[0]["new"] == 0
    assert rep.properties[0]["unchanged"] == 3


def test_interrupted_run_loses_nothing_and_resumes(h: Harness, tmp_path: Path) -> None:
    slug = SLUGS[0]
    pages = [[rec(slug, d)] for d in (20, 19, 18, 17)]
    h.sources[slug] = ScriptedSource(pages, die_after=2)
    with pytest.raises(KeyboardInterrupt):
        h.run(slugs=[slug], max_pages=10, full=True)
    assert h.count() == 0
    assert len(list((tmp_path / "spool").glob("*.jsonl"))) == 1  # pages 1-2 are on disk
    with h.factory() as s:
        assert s.scalars(select(Run.status)).all() == ["failed"]

    h.sources[slug] = ScriptedSource(pages)
    rep = h.run(slugs=[slug], max_pages=10, full=True)
    assert rep.status == "success"
    assert h.count() == 4  # pages 1-2 recovered from the spool, 3-4 collected now
    assert h.sources[slug].start_pages == [2]  # resumed at the last page read, not page 1
    assert list((tmp_path / "spool").glob("*.jsonl")) == []


def test_no_resume_flag_starts_from_the_top(h: Harness) -> None:
    slug = SLUGS[0]
    pages = [[rec(slug, d)] for d in (20, 19, 18)]
    h.sources[slug] = ScriptedSource(pages, die_after=2)
    with pytest.raises(KeyboardInterrupt):
        h.run(slugs=[slug], full=True)
    h.sources[slug] = ScriptedSource(pages)
    h.run(slugs=[slug], full=True, resume=False)
    assert h.sources[slug].start_pages == [1]
    assert h.count() == 3


def test_refusal_opens_the_circuit_and_nothing_else_is_requested(h: Harness) -> None:
    refusal = CollectionError(Outcome.BLOCKED, "Request not supported")
    h.sources[SLUGS[0]] = ScriptedSource([], fail=refusal)
    h.sources[SLUGS[1]] = ScriptedSource([[rec(SLUGS[1], 20)]])
    rep = h.run()
    assert rep.status == "blocked"
    assert [p["status"] for p in rep.properties] == ["blocked", "blocked"]
    assert h.sources[SLUGS[1]].start_pages == []  # never contacted
    assert h.count() == 0


def test_cooldown_prevents_hammering_and_is_scoped_to_browser_mode(h: Harness) -> None:
    h.sources[SLUGS[0]] = ScriptedSource([], fail=CollectionError(Outcome.CAPTCHA, "captcha shown"))
    h.sources[SLUGS[1]] = ScriptedSource([[rec(SLUGS[1], 20)]])
    assert h.run(mode="headless").status == "blocked"

    h.created.clear()
    again = h.run(mode="headless")
    assert again.status == "blocked"
    assert "no request made" in (again.note or "")
    assert h.created == []  # a scheduled retry did not touch the site

    # A skipped run must not extend the cooldown, and another mode is judged separately.
    h.sources[SLUGS[0]] = ScriptedSource([[rec(SLUGS[0], 20)]])
    ok = h.run(mode="headed")
    assert ok.status == "success"
    with h.factory() as s:
        skipped = [r for r in s.scalars(select(Run)) if (r.params or {}).get("cooldown_skip")]
        assert len(skipped) == 1


def test_one_failing_property_does_not_stop_the_others(h: Harness) -> None:
    h.sources[SLUGS[0]] = ScriptedSource(
        [], fail=CollectionError(Outcome.STRUCTURE_CHANGED, "layout changed")
    )
    h.sources[SLUGS[1]] = ScriptedSource([[rec(SLUGS[1], 20)]])
    rep = h.run()
    assert rep.status == "partial"
    assert {p["property"]: p["status"] for p in rep.properties} == {
        SLUGS[0]: "failed",
        SLUGS[1]: "success",
    }
    assert h.count() == 1


def test_parallel_workers_collect_every_property(h: Harness) -> None:
    for slug in SLUGS:
        h.sources[slug] = ScriptedSource([[rec(slug, 20)], [rec(slug, 19)]])
    rep = h.run(concurrency=2)
    assert rep.status == "success"
    assert h.count() == 4


# --------------------------------------------------------------------------- authorised sources
def test_inbox_imports_then_files_away_and_rejects_bad_files(h: Harness, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    good = [
        {**rec(SLUGS[0], 20), "review_id": "ext-1"},
        {**rec(SLUGS[0], 19), "review_id": "ext-2"},
    ]
    (inbox / "export.json").write_text(json.dumps(good))
    (inbox / "broken.csv").write_bytes(b"")
    (inbox / "notes.txt").write_text("ignored")
    results = import_inbox(h.factory, inbox, on_event=lambda *_: None)
    by_name = {r.file: r for r in results}
    assert by_name["export.json"].status == "imported" and by_name["export.json"].new == 2
    assert by_name["broken.csv"].status == "rejected"
    assert (inbox / "notes.txt").exists()
    assert not (inbox / "export.json").exists()
    assert len(list((inbox / "processed").glob("*export.json"))) == 1
    assert list((inbox / "rejected").glob("*broken.csv.reason.txt"))
    assert h.count() == 2


def _client_for(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_feed_retries_transient_errors_and_imports(h: Harness) -> None:
    calls = {"n": 0}
    body = [{**rec(SLUGS[0], 20), "review_id": "f-1"}]

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["authorization"] == "Bearer s3cret"
        if calls["n"] == 1:
            return httpx.Response(503)
        if calls["n"] == 2:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json=body)

    waits: list[float] = []
    content = fetch_feed(
        "https://feeds.example.test/reviews?key=abc",
        token="s3cret",
        policy=RetryPolicy(max_attempts=4, base_delay=0, jitter=0),
        timeout_s=5,
        on_event=lambda *_: None,
        sleep=waits.append,
        transport=_client_for(handler),
    )
    assert calls["n"] == 3 and waits[1] == 1  # Retry-After honoured
    assert import_feed(h.factory, content).new == 1
    assert import_feed(h.factory, content).new == 0  # re-reading the feed is harmless


def test_feed_rejected_credentials_are_a_stop_not_a_retry_loop() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401)

    with pytest.raises(CollectionError) as info:
        fetch_feed(
            "https://feeds.example.test/r?key=abc",
            token="bad",
            policy=RetryPolicy(max_attempts=5),
            timeout_s=5,
            on_event=lambda *_: None,
            sleep=lambda _: None,
            transport=_client_for(handler),
        )
    assert calls["n"] == 1
    assert info.value.site_decision
    assert "abc" not in str(info.value)  # query-string secrets never reach messages/logs


def test_heartbeats_every_page_and_a_reaped_run_stops_without_overwriting(h: Harness) -> None:
    from sqlalchemy import update

    slug = SLUGS[0]
    pages = [[rec(slug, d)] for d in (20, 19, 18, 17)]
    src = ScriptedSource(pages)
    original = src.read_current
    seen_heartbeats: list[Any] = []

    def read_current() -> Page:
        with h.factory() as s:
            run = s.scalars(select(Run).where(Run.status == "running")).one()
            seen_heartbeats.append(run.heartbeat_at)
            if src.i == 1:  # an operator / the reaper marks the run abandoned after page 1
                s.execute(update(Run).where(Run.id == run.id).values(status="abandoned"))
                s.commit()
        return original()

    src.read_current = read_current  # type: ignore[method-assign]
    h.sources[slug] = src
    rep = h.run(slugs=[slug], max_pages=10, full=True)

    assert len(seen_heartbeats) == 2  # stopped at the page after the reap, not at page 4
    assert all(hb is not None for hb in seen_heartbeats)
    with h.factory() as s:
        run = s.get(Run, rep.run_id)
        assert run is not None and run.status == "abandoned"  # finish did not overwrite it
        row = s.get(
            RunProperty,
            (rep.run_id, s.scalars(select(Property.id).where(Property.slug == slug)).one()),
        )
        assert row is not None and row.next_offset >= 1 and row.heartbeat_at is not None
    assert h.count() == 2  # what was read before the stop is kept
