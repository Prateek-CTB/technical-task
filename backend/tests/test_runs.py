"""Run lifecycle and crash recovery (F2): SQLite and PostgreSQL."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from reviewhub.persistence.models import Run, RunProperty
from reviewhub.persistence.runs import (
    RunAlreadyRunning,
    finish_run,
    heartbeat,
    reap_stale_runs,
    start_run,
)
from tests import factories as f

T0 = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
STALE = timedelta(minutes=15)


def begin(s: Session, scope: str, now: datetime = T0, **kw: object) -> Run:
    return start_run(
        s,
        kind="import",
        source="csv_import",
        scope_key=scope,
        mode="file",
        now=now,
        stale_after=STALE,
        **kw,  # type: ignore[arg-type]
    )


def status_of(engine: Engine, run_id: int) -> str:
    with Session(engine) as s:
        run = s.get(Run, run_id)
        assert run is not None
        return run.status


def test_second_run_in_same_scope_is_refused_while_the_first_is_alive(engine: Engine) -> None:
    with Session(engine) as s1, Session(engine) as s2:
        first = begin(s1, "import:aaa")
        with pytest.raises(RunAlreadyRunning) as err:
            begin(s2, "import:aaa", T0 + timedelta(minutes=5))
        assert err.value.run_id == first.id


def test_a_crashed_run_blocks_its_scope_only_until_stale(engine: Engine) -> None:
    with Session(engine) as s:
        dead = begin(s, "import:aaa").id  # worker "crashes": never finishes, never heartbeats
        with pytest.raises(RunAlreadyRunning):
            begin(s, "import:aaa", T0 + timedelta(minutes=14, seconds=59))
        fresh = begin(s, "import:aaa", T0 + timedelta(minutes=15, seconds=1)).id
    assert status_of(engine, dead) == "abandoned"
    assert status_of(engine, fresh) == "running"
    with Session(engine) as s:
        old = s.get(Run, dead)
        assert old is not None and old.finished_at is not None
        assert old.error and "abandoned" in old.error


def test_heartbeat_keeps_a_long_run_alive(engine: Engine) -> None:
    with Session(engine) as s:
        run = begin(s, "import:aaa")
        assert heartbeat(s, run.id, now=T0 + timedelta(minutes=14))
        assert reap_stale_runs(s, now=T0 + timedelta(minutes=20), stale_after=STALE) == []
        with pytest.raises(RunAlreadyRunning):
            begin(s, "import:aaa", T0 + timedelta(minutes=20))
        assert reap_stale_runs(s, now=T0 + timedelta(minutes=30), stale_after=STALE) == [run.id]


def test_reaper_only_touches_stale_running_runs_in_scope(engine: Engine) -> None:
    with Session(engine) as s:
        stale_other = begin(s, "import:other").id
        done = begin(s, "import:done").id
        finish_run(s, done, status="success", now=T0 + timedelta(minutes=1))
        stale_same = begin(s, "import:aaa").id
        assert reap_stale_runs(
            s, now=T0 + timedelta(hours=1), stale_after=STALE, scope_key="import:aaa"
        ) == [stale_same]
    assert status_of(engine, stale_other) == "running"  # different scope: untouched
    assert status_of(engine, done) == "success"  # finished: untouched
    assert status_of(engine, stale_same) == "abandoned"


def test_global_reap_recovers_every_stale_run(engine: Engine) -> None:
    with Session(engine) as s:
        ids = [begin(s, f"import:{n}").id for n in range(3)]
        assert sorted(reap_stale_runs(s, now=T0 + timedelta(hours=2), stale_after=STALE)) == ids


def test_reaping_fails_the_runs_unfinished_property_rows(engine: Engine) -> None:
    with Session(engine) as s:
        run = begin(s, "collect:booking_public")
        pid = f.property_id(s)
        s.add_all(
            [
                RunProperty(run_id=run.id, property_id=pid, status="running", started_at=T0),
                RunProperty(
                    run_id=run.id,
                    property_id=f.property_id(s, "venus-surry-hills"),
                    status="success",
                    started_at=T0,
                ),
            ]
        )
        s.commit()
        reap_stale_runs(s, now=T0 + timedelta(hours=1), stale_after=STALE)
        rows = {r.property_id: r.status for r in s.scalars(select(RunProperty))}
    assert rows[pid] == "failed"
    assert sorted(rows.values()) == ["failed", "success"]  # the finished one is kept


def test_a_reaped_run_cannot_be_resurrected_by_a_late_finish(engine: Engine) -> None:
    with Session(engine) as s:
        run_id = begin(s, "import:aaa").id
        reap_stale_runs(s, now=T0 + timedelta(hours=1), stale_after=STALE)
        assert (
            heartbeat(s, run_id, now=T0 + timedelta(hours=1)) is False
        )  # zombie learns it is dead
        assert finish_run(s, run_id, status="success") is False
    assert status_of(engine, run_id) == "abandoned"


def test_finished_run_frees_its_scope_immediately(engine: Engine) -> None:
    with Session(engine) as s:
        run = begin(s, "import:aaa")
        finish_run(s, run.id, status="success", now=T0 + timedelta(minutes=1))
        begin(s, "import:aaa", T0 + timedelta(minutes=2))


def test_hard_killed_process_is_recovered(db_url: str, engine: Engine) -> None:
    """A real process dies without cleanup (os._exit): the next start recovers the scope."""
    script = textwrap.dedent(
        """
        import os, sys
        from reviewhub.persistence.session import create_db_engine, create_session_factory
        from reviewhub.persistence.runs import start_run
        with create_session_factory(create_db_engine(sys.argv[1]))() as s:
            start_run(s, kind="import", source="csv_import", scope_key="import:crash",
                      mode="file")
        os._exit(9)   # no finally blocks, no atexit, no cleanup
        """
    )
    proc = subprocess.run([sys.executable, "-c", script, db_url], capture_output=True, text=True)
    assert proc.returncode == 9, proc.stderr
    with Session(engine) as s:
        dead = s.scalar(select(Run).where(Run.scope_key == "import:crash"))
        assert dead is not None and dead.status == "running"
        with pytest.raises(RunAlreadyRunning):
            start_run(s, kind="import", source="csv_import", scope_key="import:crash", mode="file")
        recovered = start_run(
            s,
            kind="import",
            source="csv_import",
            scope_key="import:crash",
            mode="file",
            now=datetime.now(UTC) + timedelta(minutes=16),
        )
        assert recovered.id != dead.id
        s.refresh(dead)
        assert dead.status == "abandoned"
