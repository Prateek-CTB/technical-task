"""Collection engine: walks a property's review pages with retries, pacing and checkpoints.

Browser-free on purpose. Anything that can read pages (a Playwright session, a test double)
implements :class:`PageSource`; everything about reliability lives here and is unit-tested.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from .policy import EventHook, Pacer, RetryPolicy, run_with_retries
from .signals import CollectionError, Outcome


@dataclass
class Page:
    number: int
    records: list[dict[str, Any]]
    skipped: int = 0  # cards that could not be parsed (no date / score)
    has_next: bool = True

    @property
    def fingerprint(self) -> str:
        """Identity of the page content, to detect a pagination that did not advance."""
        raw = json.dumps(self.records[:3], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class SourceInfo:
    headline_total: int | None = None
    headline_score: float | None = None
    sorted_newest: bool = False


class PageSource(Protocol):
    def open(self, start_page: int = 1) -> SourceInfo:
        """Load the property, open its reviews, sort newest first, go to ``start_page``."""

    def read_current(self) -> Page: ...

    def advance(self) -> bool:
        """Go to the next page. False when there is none."""

    def close(self) -> None: ...


@dataclass
class PropertyResult:
    slug: str
    status: str = "failed"  # success | blocked | failed
    outcome: Outcome | None = None
    pages: int = 0
    received: int = 0
    skipped_cards: int = 0
    last_page: int = 0
    error: str | None = None
    info: SourceInfo = field(default_factory=SourceInfo)
    stopped_early: bool = False  # run-level stop (another property was blocked)


#: Called after every page is spooled. Return False to stop (e.g. the run was reaped).
Checkpoint = Callable[[PropertyResult, Page], bool]


def collect_property(
    slug: str,
    source: PageSource,
    *,
    policy: RetryPolicy,
    pacer: Pacer,
    max_pages: int,
    stop_before: date | None,
    start_page: int = 1,
    spool_page: Callable[[int, list[dict[str, Any]]], None],
    checkpoint: Checkpoint,
    on_event: EventHook,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> PropertyResult:
    """Read up to ``max_pages`` pages. Never raises for site/network problems: returns a result."""
    res = PropertyResult(slug)
    position = start_page

    def retry(op: Callable[[], Any], what: str, *, recover: bool) -> Any:
        return run_with_retries(
            op,
            policy,
            describe=what,
            recover=(lambda: _reopen(source, position)) if recover else None,
            sleep=sleep,
            on_event=on_event,
            should_stop=should_stop,
        )

    try:
        res.info = retry(lambda: source.open(start_page), "open", recover=False)
        if not res.info.sorted_newest and stop_before is not None:
            on_event("warning", {"message": "could not sort newest-first; incremental stop off"})
            stop_before = None
        previous = ""
        while True:
            if should_stop():
                res.stopped_early = True
                res.status, res.error = "blocked", "stopped: another property was refused"
                return res
            page: Page = retry(source.read_current, f"read page {position}", recover=True)
            if not page.records and not page.skipped:
                raise CollectionError(Outcome.STRUCTURE_CHANGED, "review page contained no cards")
            if previous and page.fingerprint == previous:
                raise CollectionError(
                    Outcome.STRUCTURE_CHANGED, "pagination did not advance (same page returned)"
                )
            previous = page.fingerprint
            spool_page(position, page.records)  # durable before anything else
            res.pages += 1
            res.received += len(page.records)
            res.skipped_cards += page.skipped
            res.last_page = position
            if not checkpoint(res, page):
                res.stopped_early = True
                res.error = "stopped: run no longer active"
                return res
            dates = [date.fromisoformat(r["review_date"]) for r in page.records]
            if stop_before is not None and dates and max(dates) < stop_before:
                break  # everything on this page predates what we already hold
            if res.pages >= max_pages or not page.has_next:
                break
            pacer.wait()
            if not retry(source.advance, f"advance from page {position}", recover=True):
                break
            position += 1
        res.status = "success"
    except CollectionError as exc:
        res.status, res.outcome, res.error = exc.status, exc.outcome, str(exc)
    except Exception as exc:  # unexpected: recorded, run continues with other properties
        res.status, res.error = "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        with contextlib.suppress(Exception):
            source.close()
    return res


def _reopen(source: PageSource, position: int) -> None:
    """Recovery after a failed step: reload and return to the page we were reading."""
    source.open(position)
