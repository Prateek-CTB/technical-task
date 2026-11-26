"""Collection orchestration (ADR 0022).

Flow of one ``collect`` run::

    cooldown check -> start run (single flight) -> recover leftover spool files
      -> per property (1..N workers): pace -> read pages (retry/backoff) -> spool each page
         -> checkpoint -> import spool through the normal pipeline -> record result
      -> circuit breaker: a CAPTCHA/refusal stops every worker; nothing more is requested
      -> finish run

Collected reviews are never written directly: they go through ``import_file`` (validation,
fingerprint de-duplication, revisions, data-quality checks, dataset isolation) as
``booking_public``. History = ``run`` + ``run_property`` rows (queryable) and a per-run JSONL
event log (detailed).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from reviewhub.core.settings import Settings
from reviewhub.ingestion.adapters.files import JsonAdapter
from reviewhub.ingestion.contracts import ImportReport
from reviewhub.ingestion.service import ImportOptions, import_file
from reviewhub.persistence.models import Property, Review, Run, RunProperty, ScoreSnapshot
from reviewhub.persistence.runs import Clock, finish_run, heartbeat, start_run, utc_now

from .engine import Page, PageSource, PropertyResult, collect_property
from .events import EventLog
from .policy import Pacer, RetryPolicy
from .public import COLLECTOR_VERSION
from .signals import SITE_DECISIONS
from .spool import Spool, SpoolFile

SOURCE = "booking_public"
LOOKBACK_DAYS = 14  # re-read recent reviews: late edits and new replies are picked up
RESUME_WINDOW = timedelta(hours=24)


class PublicAdapter(JsonAdapter):
    name = "booking_public"
    source = SOURCE


class SourceMaker(Protocol):
    def __call__(self, prop: Property) -> PageSource: ...


#: ``worker_factory(worker_index)`` yields a function that makes a PageSource per property.
WorkerFactory = Callable[[int], AbstractContextManager[SourceMaker]]


@dataclass
class CollectOptions:
    slugs: list[str] | None = None
    max_pages: int = 3
    full: bool = False
    mode: str = "headless"  # headless | headed
    concurrency: int = 1
    resume: bool = True


@dataclass
class CollectionReport:
    run_id: int
    status: str
    properties: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None
    log_path: str | None = None


# --------------------------------------------------------------------------- helpers
def active_cooldown(
    factory: sessionmaker[Session], mode: str, hours: float, now: datetime
) -> datetime | None:
    """When the latest real refusal in this browser mode is still within its cooldown, until when."""
    if hours <= 0:
        return None
    with factory() as s:
        recent = s.scalars(
            select(Run)
            .where(Run.kind == "collect", Run.source == SOURCE)
            .order_by(Run.started_at.desc())
            .limit(30)
        ).all()
    for run in recent:
        params = run.params or {}
        if params.get("cooldown_skip") or params.get("browser_mode") != mode:
            continue
        if run.status == "running":
            continue
        if run.status != "blocked":
            return None  # a later run in this mode got through: no cooldown
        until = (run.finished_at or run.started_at) + timedelta(hours=hours)
        return until if until > now else None
    return None


def stop_dates(
    factory: sessionmaker[Session], props: list[Property], full: bool
) -> dict[str, date | None]:
    out: dict[str, date | None] = {}
    with factory() as s:
        for p in props:
            newest = s.scalar(
                select(func.max(Review.published_date_local)).where(
                    Review.property_id == p.id, Review.source == SOURCE
                )
            )
            out[p.slug] = None if full or newest is None else newest - timedelta(days=LOOKBACK_DAYS)
    return out


def _import_records(
    factory: sessionmaker[Session], slug: str, records: list[dict[str, Any]], clock: Clock
) -> ImportReport:
    return import_file(
        factory,
        content=json.dumps(records, ensure_ascii=False).encode(),
        filename=f"collect-{slug}.json",
        options=ImportOptions(format="json", id_namespace="booking", trigger="collect"),
        adapter=PublicAdapter(),
        allowed_sources=(SOURCE,),
        clock=clock,
    )


class _Recorder:
    """Serialised database access for worker threads (SQLite allows one writer)."""

    def __init__(self, factory: sessionmaker[Session], run_id: int, clock: Clock) -> None:
        self.factory, self.run_id, self.clock = factory, run_id, clock
        self.lock = threading.Lock()

    def begin_property(self, property_id: int) -> None:
        with self.lock, self.factory() as s:
            s.merge(
                RunProperty(
                    run_id=self.run_id,
                    property_id=property_id,
                    status="running",
                    started_at=self.clock(),
                    heartbeat_at=self.clock(),
                )
            )
            s.commit()

    def checkpoint(self, property_id: int, res: PropertyResult, page: Page) -> bool:
        with self.lock, self.factory() as s:
            row = s.get(RunProperty, (self.run_id, property_id))
            if row is not None:
                row.pages, row.received = res.pages, res.received
                row.next_offset, row.last_page_hash = res.last_page, page.fingerprint
                row.heartbeat_at = self.clock()
            s.commit()
            return heartbeat(s, self.run_id, now=self.clock())

    def import_spool(self, slug: str, records: list[dict[str, Any]]) -> ImportReport:
        with self.lock:
            return _import_records(self.factory, slug, records, self.clock)

    def finish_property(
        self,
        property_id: int,
        res: PropertyResult,
        status: str,
        error: str | None,
        imported: ImportReport | None,
    ) -> None:
        with self.lock, self.factory() as s:
            row = s.get(RunProperty, (self.run_id, property_id))
            if row is None:
                row = RunProperty(
                    run_id=self.run_id, property_id=property_id, started_at=self.clock()
                )
                s.add(row)
            row.status = status if status in ("success", "blocked", "failed") else "failed"
            row.pages, row.received = res.pages, res.received
            row.new = imported.new if imported else 0
            row.updated = imported.updated if imported else 0
            row.unchanged = imported.unchanged if imported else 0
            row.rejected = (imported.rows_rejected if imported else 0) + res.skipped_cards
            row.reported_headline_total = res.info.headline_total
            row.error = error
            row.finished_at = self.clock()
            if res.info.headline_score is not None:
                s.add(
                    ScoreSnapshot(
                        property_id=property_id,
                        captured_at=self.clock(),
                        metric="overall",
                        run_id=self.run_id,
                        value=res.info.headline_score,
                        review_count=res.info.headline_total,
                    )
                )
            s.commit()


def _default_worker_factory(settings: Settings, mode: str) -> WorkerFactory:
    from .browser import BookingPageSource, browser_session

    @contextmanager
    def worker(idx: int) -> Any:
        profile = (
            Path(settings.collect_profile_dir) / f"w{idx}" if settings.collect_profile_dir else None
        )
        with browser_session(
            headless=mode == "headless",
            profile_dir=profile,
            nav_timeout_s=settings.collect_nav_timeout_seconds,
            action_timeout_s=settings.collect_action_timeout_seconds,
        ) as ctx:

            def make(prop: Property) -> PageSource:
                return BookingPageSource(
                    ctx,
                    prop.slug,
                    prop.booking_url or "",
                    action_timeout_s=settings.collect_action_timeout_seconds,
                )

            yield make

    return worker


# --------------------------------------------------------------------------- the run
def collect(
    factory: sessionmaker[Session],
    settings: Settings,
    options: CollectOptions,
    *,
    worker_factory: WorkerFactory | None = None,
    clock: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectionReport:
    with factory() as s:
        props = [
            p
            for p in s.scalars(select(Property).where(Property.active).order_by(Property.id))
            if not options.slugs or p.slug in options.slugs
        ]
        for p in props:
            s.expunge(p)
    params: dict[str, Any] = {
        "browser_mode": options.mode,
        "max_pages": options.max_pages,
        "concurrency": options.concurrency,
        "properties": [p.slug for p in props],
        "full": options.full,
    }
    mode_name = "full" if options.full else "incremental"

    until = active_cooldown(factory, options.mode, settings.collect_block_cooldown_hours, clock())
    if until is not None:
        return _skip_for_cooldown(factory, mode_name, params, until, clock)

    with factory() as s:
        run = start_run(
            s,
            kind="collect",
            source=SOURCE,
            scope_key=f"collect:{SOURCE}",
            mode=mode_name,
            version=COLLECTOR_VERSION,
            params=params,
            now=clock(),
        )
        run_id = run.id
    events = EventLog(Path(settings.collect_log_dir), run_id)
    spool = Spool(Path(settings.collect_spool_dir))
    recorder = _Recorder(factory, run_id, clock)
    report = CollectionReport(run_id, "running", log_path=str(events.path) if events.path else None)
    events.emit("run_start", browser_mode=options.mode, properties=params["properties"])

    try:
        resume_pages = _recover_leftovers(spool, recorder, events, run_id, options.resume, clock)
        stops = stop_dates(factory, props, options.full)
        results = _run_workers(
            props,
            options,
            settings,
            stops,
            resume_pages,
            spool,
            recorder,
            events,
            worker_factory or _default_worker_factory(settings, options.mode),
            sleep,
        )
    except BaseException as exc:
        with factory() as s:
            finish_run(
                s, run_id, status="failed", now=clock(), error=f"{type(exc).__name__}: {exc}"[:500]
            )
        events.emit("run_end", level="error", status="failed", error=str(exc)[:200])
        raise

    report.properties = [r for _, r in sorted(results.items())]
    states = {p["status"] for p in report.properties}
    report.status = (
        "success"
        if states == {"success"}
        else "blocked"
        if "success" not in states and "blocked" in states
        else "failed"
        if "success" not in states
        else "partial"
    )
    errors = "; ".join(f"{p['property']}: {p['error']}" for p in report.properties if p["error"])
    with factory() as s:
        finish_run(
            s,
            run_id,
            status=report.status,
            now=clock(),
            items_total=len(report.properties),
            items_ok=sum(p["status"] == "success" for p in report.properties),
            items_failed=sum(p["status"] != "success" for p in report.properties),
            error=errors[:500] or None,
        )
    events.emit("run_end", status=report.status)
    return report


def _skip_for_cooldown(
    factory: sessionmaker[Session],
    mode_name: str,
    params: dict[str, Any],
    until: datetime,
    clock: Clock,
) -> CollectionReport:
    """Record that a scheduled run chose not to contact a site that recently refused us."""
    note = (
        f"skipped: the site refused this collection mode ({params.get('browser_mode')}) "
        "recently (cooldown); no request made. "
        f"Next attempt allowed after {until.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
    )
    with factory() as s:
        run = start_run(
            s,
            kind="collect",
            source=SOURCE,
            scope_key=f"collect:{SOURCE}",
            mode=mode_name,
            version=COLLECTOR_VERSION,
            params={**params, "cooldown_skip": True},
            now=clock(),
        )
        run_id = run.id
    with factory() as s:
        finish_run(s, run_id, status="blocked", now=clock(), error=note)
    return CollectionReport(run_id, "blocked", note=note)


def _recover_leftovers(
    spool: Spool,
    recorder: _Recorder,
    events: EventLog,
    run_id: int,
    resume: bool,
    clock: Clock,
) -> dict[str, int]:
    """Import pages that an interrupted run had collected; return where to resume per property."""
    resume_pages: dict[str, int] = {}
    for left in spool.leftovers(exclude_run=run_id):
        events.emit(
            "recover_spool",
            property=left.slug,
            file=left.path.name,
            records=len(left.records),
            last_page=left.last_page,
        )
        if left.records:
            rep = recorder.import_spool(left.slug, left.records)
            if rep.status == "failed":
                events.emit("recover_failed", property=left.slug, level="error")
                continue  # keep the file; try again next run
        recent = datetime.fromtimestamp(left.path.stat().st_mtime, UTC) > clock() - RESUME_WINDOW
        if resume and recent and left.last_page >= 2:
            resume_pages[left.slug] = max(resume_pages.get(left.slug, 1), left.last_page)
        spool.discard(left.path)
    return resume_pages


def _run_workers(
    props: list[Property],
    options: CollectOptions,
    settings: Settings,
    stops: dict[str, date | None],
    resume_pages: dict[str, int],
    spool: Spool,
    recorder: _Recorder,
    events: EventLog,
    worker_factory: WorkerFactory,
    sleep: Callable[[float], None],
) -> dict[int, dict[str, Any]]:
    policy = RetryPolicy(
        max_attempts=settings.collect_max_attempts,
        base_delay=settings.collect_backoff_base_seconds,
        max_delay=settings.collect_backoff_max_seconds,
    )
    page_pacer = Pacer(
        settings.collect_page_delay_min, settings.collect_page_delay_max, sleep=sleep
    )
    prop_pacer = Pacer(
        settings.collect_property_delay_min, settings.collect_property_delay_max, sleep=sleep
    )
    refused = threading.Event()  # circuit breaker
    results: dict[int, dict[str, Any]] = {}
    workers = max(1, min(options.concurrency, len(props) or 1))
    shares = [props[i::workers] for i in range(workers)]

    def work(idx: int) -> None:
        share = shares[idx]
        if not share:
            return
        with worker_factory(idx) as make:
            first = True
            for prop in share:
                if refused.is_set():
                    results[prop.id] = _skipped(recorder, events, prop)
                    continue
                if not first:
                    prop_pacer.wait()
                first = False
                results[prop.id] = _one_property(
                    prop,
                    make,
                    options,
                    policy,
                    page_pacer,
                    stops,
                    resume_pages,
                    spool,
                    recorder,
                    events,
                    refused,
                    sleep,
                )

    if workers == 1:
        work(0)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in [pool.submit(work, i) for i in range(workers)]:
                fut.result()
    return results


def _skipped(recorder: _Recorder, events: EventLog, prop: Property) -> dict[str, Any]:
    msg = "skipped: the site refused an earlier request in this run; nothing further was requested"
    recorder.finish_property(prop.id, PropertyResult(prop.slug), "blocked", msg, None)
    events.emit("property_skipped", property=prop.slug, level="warning", reason="circuit_open")
    return {
        "property": prop.slug,
        "status": "blocked",
        "pages": 0,
        "collected": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "headline_total": None,
        "headline_score": None,
        "sorted_newest": False,
        "error": msg,
    }


def _one_property(
    prop: Property,
    make: SourceMaker,
    options: CollectOptions,
    policy: RetryPolicy,
    pacer: Pacer,
    stops: dict[str, date | None],
    resume_pages: dict[str, int],
    spool: Spool,
    recorder: _Recorder,
    events: EventLog,
    refused: threading.Event,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    slug, run_id = prop.slug, recorder.run_id
    start_page = resume_pages.get(slug, 1)
    recorder.begin_property(prop.id)
    events.emit("property_start", property=slug, start_page=start_page)
    source_error: Exception | None = None
    try:
        source = make(prop)
    except Exception as exc:  # e.g. the browser cannot start
        source_error = exc
    if source_error is not None:
        res = PropertyResult(slug, status="failed", error=str(source_error)[:300])
    else:
        res = collect_property(
            slug,
            source,
            policy=policy,
            pacer=pacer,
            max_pages=options.max_pages,
            stop_before=stops.get(slug),
            start_page=start_page,
            spool_page=lambda page_no, recs: spool.append_page(slug, run_id, page_no, recs),
            checkpoint=lambda r, pg: _checkpoint(recorder, events, prop, r, pg),
            on_event=lambda ev, data: events.emit(ev, property=slug, level="warning", **data),
            should_stop=refused.is_set,
            sleep=sleep,
        )
    if res.outcome in SITE_DECISIONS:
        refused.set()  # circuit breaker: no other property is contacted
        events.emit("circuit_open", property=slug, level="warning", outcome=res.outcome.value)

    status, error = res.status, res.error
    imported: ImportReport | None = None
    sf = _spool_file(spool, slug, run_id)
    if sf is not None and sf.records:
        try:
            imported = recorder.import_spool(slug, sf.records)
            if imported.status == "failed":
                status = "failed"
                error = imported.file_error.message if imported.file_error else "import failed"
            else:
                spool.discard(sf.path)
        except Exception as exc:  # isolation refusal etc.; spool kept for the next run
            status, error = "failed", f"{type(exc).__name__}: {exc}"[:400]
    elif sf is not None:
        spool.discard(sf.path)
    recorder.finish_property(prop.id, res, status, error, imported)
    events.emit(
        "property_done",
        property=slug,
        level="info" if status == "success" else "warning",
        status=status,
        pages=res.pages,
        received=res.received,
        new=imported.new if imported else 0,
        error=error,
    )
    return {
        "property": slug,
        "status": status,
        "pages": res.pages,
        "collected": res.received,
        "new": imported.new if imported else 0,
        "updated": imported.updated if imported else 0,
        "unchanged": imported.unchanged if imported else 0,
        "headline_total": res.info.headline_total,
        "headline_score": res.info.headline_score,
        "sorted_newest": res.info.sorted_newest,
        "error": error,
    }


def _checkpoint(
    recorder: _Recorder, events: EventLog, prop: Property, res: PropertyResult, page: Page
) -> bool:
    alive = recorder.checkpoint(prop.id, res, page)
    events.emit(
        "page",
        property=prop.slug,
        page=res.last_page,
        records=len(page.records),
        skipped=page.skipped,
    )
    return alive


def _spool_file(spool: Spool, slug: str, run_id: int) -> SpoolFile | None:
    path = spool.path_for(slug, run_id)
    return spool.read(path) if path.exists() else None
