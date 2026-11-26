"""Long-running jobs started from the dashboard (collection, AI analysis).

They run in a daemon thread so the HTTP request returns at once; progress and results are
read back from the ``run`` table, which every job already writes (status, counts, heartbeat).
Single-flight is enforced by the run scopes themselves (``RunAlreadyRunning``), and
``active_run`` gives the API a cheap pre-check so the UI can say "already running".
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from reviewhub.persistence.models import Run
from reviewhub.persistence.runs import utc_now

log = logging.getLogger("reviewhub.jobs")


def start_job(name: str, fn: Callable[[], object]) -> None:
    """Run ``fn`` in a background thread; failures are logged (the run row records them too)."""

    def runner() -> None:
        try:
            fn()
        except Exception:
            log.exception("background job %s failed", name)

    threading.Thread(target=runner, name=f"job-{name}", daemon=True).start()


def active_run(s: Session, kind: str, stale_after: timedelta) -> Run | None:
    """The newest run of ``kind`` still marked running with a recent heartbeat, if any."""
    fresh_since = utc_now() - stale_after
    for run in s.scalars(
        select(Run).where(Run.kind == kind, Run.status == "running").order_by(Run.id.desc())
    ):
        beat = run.heartbeat_at or run.started_at
        if beat >= fresh_since:
            return run
    return None
