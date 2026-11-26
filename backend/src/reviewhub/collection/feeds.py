"""Authorised, unattended sources that need no browser at all (ADR 0002, ADR 0022).

* **Inbox**: a folder that the owner's Extranet / channel-manager export (CSV or JSON) is dropped
  into, by hand or by a scheduled export. Each file is imported through the normal pipeline and
  then moved to ``processed/`` or ``rejected/``.
* **Feed**: an HTTPS endpoint the property owner has authorised us to read (channel-manager or
  partner API), returning the JSON import format. Credentials come from settings, never code.

Both run identically on Windows and on headless Linux, and reuse the same retry/backoff policy.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session, sessionmaker

from reviewhub.ingestion.adapters.files import JsonAdapter
from reviewhub.ingestion.contracts import ImportReport
from reviewhub.ingestion.service import ImportOptions, import_file
from reviewhub.persistence.runs import Clock, utc_now

from .policy import EventHook, RetryPolicy, run_with_retries
from .signals import CollectionError, Outcome

INBOX_SUFFIXES = {".csv", ".json"}


@dataclass
class InboxResult:
    file: str
    status: str  # imported | rejected | skipped
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    error: str | None = None


def import_inbox(
    factory: sessionmaker[Session],
    inbox: Path,
    *,
    on_event: EventHook,
    clock: Clock = utc_now,
) -> list[InboxResult]:
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = clock().strftime("%Y%m%dT%H%M%S")
    results: list[InboxResult] = []
    for path in sorted(p for p in inbox.iterdir() if p.is_file()):
        if path.suffix.lower() not in INBOX_SUFFIXES:
            continue
        try:
            content = path.read_bytes()
        except OSError as exc:  # e.g. still open in Excel: try again next run
            on_event("inbox_skipped", {"file": path.name, "error": str(exc)[:120]})
            results.append(InboxResult(path.name, "skipped", error=str(exc)[:120]))
            continue
        report = import_file(
            factory,
            content=content,
            filename=path.name,
            options=ImportOptions(trigger="collect"),
            clock=clock,
        )
        if report.status == "failed":
            err = report.file_error.message if report.file_error else "import failed"
            _move(path, inbox / "rejected", stamp, note=err)
            results.append(InboxResult(path.name, "rejected", error=err))
        else:
            _move(path, inbox / "processed", stamp)
            results.append(
                InboxResult(path.name, "imported", report.new, report.updated, report.unchanged)
            )
        on_event("inbox_file", {"file": path.name, "status": results[-1].status})
    return results


def _move(path: Path, folder: Path, stamp: str, note: str | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stamp}-{path.name}"
    path.replace(target)
    if note:
        target.with_suffix(target.suffix + ".reason.txt").write_text(note, encoding="utf-8")


def _safe_url(url: str) -> str:
    """Host and path only: query strings can carry secrets and must not reach logs."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def fetch_feed(
    url: str,
    *,
    token: str | None,
    policy: RetryPolicy,
    timeout_s: float,
    on_event: EventHook,
    sleep: Callable[[float], None] = time.sleep,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    headers = {"Accept": "application/json", "User-Agent": "reviewhub-collector"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    where = _safe_url(url)

    with httpx.Client(timeout=timeout_s, transport=transport, follow_redirects=False) as client:

        def once() -> bytes:
            try:
                r = client.get(url, headers=headers)
            except httpx.TimeoutException as exc:
                raise CollectionError(Outcome.TRANSIENT, f"feed timed out: {where}") from exc
            except httpx.TransportError as exc:
                raise CollectionError(
                    Outcome.TRANSIENT, f"network error reaching {where}: {type(exc).__name__}"
                ) from exc
            code = r.status_code
            if code == 429:
                after = r.headers.get("retry-after", "")
                raise CollectionError(
                    Outcome.RATE_LIMITED,
                    "feed rate limited (HTTP 429)",
                    retry_after=float(after) if after.isdigit() else None,
                )
            if code in (401, 403):
                raise CollectionError(
                    Outcome.BLOCKED, f"feed refused our credentials (HTTP {code}); check the token"
                )
            if code >= 500:
                raise CollectionError(Outcome.TRANSIENT, f"feed server error (HTTP {code})")
            if code >= 400:
                raise CollectionError(
                    Outcome.STRUCTURE_CHANGED, f"unexpected HTTP {code} from feed"
                )
            return r.content

        return run_with_retries(once, policy, describe="fetch feed", sleep=sleep, on_event=on_event)


def import_feed(
    factory: sessionmaker[Session],
    content: bytes,
    *,
    clock: Clock = utc_now,
) -> ImportReport:
    return import_file(
        factory,
        content=content,
        filename=f"feed-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.json",
        options=ImportOptions(format="json", trigger="collect"),
        adapter=JsonAdapter(),
        clock=clock,
    )
