"""Run lifecycle: single-flight start, heartbeat, finish, and stale-run recovery.

A *run* is one collection, import or analysis (table ``run``). Two guarantees:

* **Single-flight** per scope key (ADR 0012), enforced by the database through a
  partial unique index, not by checking first and inserting second.
* **Crash recovery** (ADR 0019): a worker that dies leaves its row ``running``.
  Any run with no heartbeat for ``stale_after`` is marked ``abandoned``; this
  happens automatically before a new run of the same scope starts, so a crash
  can block that scope for at most ``stale_after`` and never forever. Long runs
  must call :func:`heartbeat`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Run, RunProperty

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]
DEFAULT_STALE_AFTER = timedelta(minutes=15)


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunAlreadyRunning(RuntimeError):
    """Another run with the same scope key is in progress."""

    def __init__(self, scope_key: str, run_id: int | None) -> None:
        super().__init__(f"a run for scope {scope_key!r} is already in progress (run {run_id})")
        self.scope_key = scope_key
        self.run_id = run_id


def reap_stale_runs(
    session: Session,
    *,
    now: datetime,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    scope_key: str | None = None,
) -> list[int]:
    """Mark runs with no heartbeat for ``stale_after`` as ``abandoned``. Commits.

    The UPDATEs are conditional on ``status = 'running'``, so a run that finishes
    between the SELECT and the UPDATE is never overwritten.
    """
    cutoff = now - stale_after
    query = select(Run.id).where(
        Run.status == "running", func.coalesce(Run.heartbeat_at, Run.started_at) < cutoff
    )
    if scope_key is not None:
        query = query.where(Run.scope_key == scope_key)
    candidates = list(session.scalars(query))
    reaped: list[int] = []
    for run_id in candidates:
        result = session.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "running")
            .values(
                status="abandoned",
                finished_at=now,
                error=f"abandoned: no heartbeat for more than {int(stale_after.total_seconds() // 60)} min",
            )
        )
        if result.rowcount:  # type: ignore[attr-defined]
            reaped.append(run_id)
            session.execute(
                update(RunProperty)
                .where(RunProperty.run_id == run_id, RunProperty.status == "running")
                .values(status="failed", finished_at=now, error="run abandoned")
            )
    session.commit()
    if reaped:
        log.warning("marked stale runs abandoned", extra={"run_ids": reaped})
    return reaped


def start_run(
    session: Session,
    *,
    kind: str,
    source: str,
    scope_key: str,
    mode: str,
    trigger: str = "cli",
    version: str | None = None,
    params: dict[str, Any] | None = None,
    file_sha256: str | None = None,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> Run:
    """Create a ``running`` run, or raise :class:`RunAlreadyRunning`. Commits."""
    now = now or utc_now()
    reap_stale_runs(session, now=now, stale_after=stale_after, scope_key=scope_key)
    run = Run(
        kind=kind,
        source=source,
        scope_key=scope_key,
        mode=mode,
        status="running",
        trigger=trigger,
        version=version,
        params=params,
        file_sha256=file_sha256,
        started_at=now,
        heartbeat_at=now,
    )
    session.add(run)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        holder = session.scalar(
            select(Run.id).where(Run.scope_key == scope_key, Run.status == "running")
        )
        if holder is None:  # not the single-flight index: a genuine bug, don't mask it
            raise
        raise RunAlreadyRunning(scope_key, holder) from None
    return run


def heartbeat(
    session: Session, run_id: int, *, now: datetime | None = None, commit: bool = True
) -> bool:
    """Record liveness. Returns False if the run is no longer ``running`` (it was reaped).

    ``commit=False`` performs the update inside the caller's transaction: used at the
    start of a data transaction so that (a) a reaped "zombie" learns it before writing
    and (b) SQLite takes its write lock before any read, avoiding a snapshot-upgrade failure.
    """
    result = session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == "running")
        .values(heartbeat_at=now or utc_now())
    )
    if commit:
        session.commit()
    return bool(result.rowcount)  # type: ignore[attr-defined]


def finish_run(
    session: Session,
    run_id: int,
    *,
    status: str,
    now: datetime | None = None,
    items_total: int | None = None,
    items_ok: int | None = None,
    items_failed: int | None = None,
    error: str | None = None,
    params: dict[str, Any] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> bool:
    """Close a run. A run that was already reaped stays ``abandoned`` (returns False)."""
    values: dict[str, Any] = {
        "status": status,
        "finished_at": now or utc_now(),
        "items_total": items_total,
        "items_ok": items_ok,
        "items_failed": items_failed,
        "error": error,
    }
    if params is not None:
        values["params"] = params
    for key, val in (
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("cost_usd", cost_usd),
    ):
        if val is not None:
            values[key] = val
    result = session.execute(
        update(Run).where(Run.id == run_id, Run.status == "running").values(**values)
    )
    session.commit()
    if not result.rowcount:  # type: ignore[attr-defined]
        log.warning("run was no longer running when finished", extra={"run_id": run_id})
    return bool(result.rowcount)  # type: ignore[attr-defined]
