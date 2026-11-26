"""Write-ahead spool: every collected page is on disk before anything else happens to it.

A crash, kill or power loss therefore never loses collected reviews. The next run finds the
leftover file, imports it (the import is idempotent: de-duplication by stable fingerprint) and
resumes from the last page that was read. A file is deleted only after its import succeeded.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^(?P<slug>[a-z0-9-]+)__run(?P<run>\d+)\.jsonl$")


@dataclass(frozen=True)
class SpoolFile:
    path: Path
    slug: str
    run_id: int
    records: list[dict[str, Any]]
    last_page: int  # highest page number fully written (0 = none)


class Spool:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, slug: str, run_id: int) -> Path:
        return self.root / f"{slug}__run{run_id}.jsonl"

    def append_page(self, slug: str, run_id: int, page: int, records: list[dict[str, Any]]) -> None:
        """Append one page as a single line and fsync, so a torn write can only be the last line."""
        line = json.dumps({"page": page, "records": records}, ensure_ascii=False) + "\n"
        with self.path_for(slug, run_id).open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    @staticmethod
    def read(path: Path) -> SpoolFile | None:
        m = _NAME.match(path.name)
        if not m:
            return None
        records: list[dict[str, Any]] = []
        last = 0
        with path.open(encoding="utf-8") as fh:
            for raw in fh:
                try:
                    obj = json.loads(raw)
                    page, recs = int(obj["page"]), list(obj["records"])
                except (ValueError, KeyError, TypeError):
                    continue  # torn final line from a crash: ignore, earlier pages are intact
                records.extend(recs)
                last = max(last, page)
        return SpoolFile(path, m["slug"], int(m["run"]), records, last)

    def leftovers(self, *, exclude_run: int | None = None) -> list[SpoolFile]:
        found = []
        for p in sorted(self.root.glob("*.jsonl")):
            sf = self.read(p)
            if sf is not None and sf.run_id != exclude_run:
                found.append(sf)
        return found

    @staticmethod
    def discard(path: Path) -> None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
