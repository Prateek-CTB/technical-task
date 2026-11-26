"""Per-run structured event log (JSON lines) plus the standard logger.

One file per run: ``<log_dir>/run-<id>.jsonl``. Events carry outcomes, counts and timings only:
never cookies, headers, page HTML or reviewer names.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("reviewhub.collection")


class EventLog:
    def __init__(self, directory: Path | None, run_id: int) -> None:
        self._path = directory / f"run-{run_id}.jsonl" if directory else None
        self.run_id = run_id
        self._lock = threading.Lock()
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path | None:
        return self._path

    def emit(
        self, event: str, *, property: str | None = None, level: str = "info", **data: Any
    ) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "event": event,
            "level": level,
            "property": property,
            **data,
        }
        log.log(
            getattr(logging, level.upper(), logging.INFO),
            "collect.%s",
            event,
            extra={"run_id": self.run_id, "property": property, **data},
        )
        if self._path:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._lock, self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
