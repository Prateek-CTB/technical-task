"""PublicPageCollector: one plain HTTPS request per public property page (ADR 0024).

The cheapest, most server-friendly strategy, tried before a browser is needed:

* exactly one GET per property per run, honest User-Agent, paced like the browser collector;
* reads only schema.org structured data (Booking.com's published score and review count, and
  review items if the page ever carries them);
* classifies every answer: an AWS WAF challenge page, a CAPTCHA, an access-denied status or an
  unexpected redirect is recorded with its exact reason, never parsed as content and never
  "solved". Retries are reserved for network errors, 5xx and 429.

Verified live on 2026-11-26: every property URL answered a non-browser client with HTTP 202 and
an AWS WAF JavaScript challenge (``challenge.js`` + ``AwsWafIntegration.getToken()``), so this
strategy currently records ``blocked / waf_challenge`` and the review data comes from the
browser collector or an authorised import. If the site changes, no code change is needed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from reviewhub import __version__
from reviewhub.core.settings import Settings
from reviewhub.ingestion.contracts import ImportReport
from reviewhub.persistence.models import Property
from reviewhub.persistence.runs import Clock, finish_run, start_run, utc_now

from .engine import PropertyResult, SourceInfo
from .events import EventLog
from .policy import Pacer, RetryPolicy, run_with_retries
from .service import (
    SOURCE,
    CollectionReport,
    _Recorder,
    _skip_for_cooldown,
    active_cooldown,
)
from .signals import (
    SITE_DECISIONS,
    CollectionError,
    Outcome,
    Signals,
    classify,
    looks_like_waf_challenge,
)
from .structured import StructuredData, extract_structured

HTTP_MODE = "http"  # cooldowns are judged per mode, separately from the browser modes
STRATEGY = "http_structured"
VERSION = "booking_http/1.0.0"
USER_AGENT = f"ReviewHub/{__version__} (guest-review insights; one request per property per run)"
_TAGS = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)
MAX_BYTES = 3_000_000


@dataclass
class PageFetch:
    status: int
    final_url: str
    html: str
    data: StructuredData


class PublicPageCollector:
    """Fetch one public property page and return its structured data, or raise a classified
    :class:`CollectionError`. Owns no retry logic (the caller's policy decides)."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch(self, url: str, slug: str) -> PageFetch:
        try:
            resp = self._client.get(url)
        except httpx.TimeoutException as exc:
            raise CollectionError(Outcome.TRANSIENT, "timed out fetching the page") from exc
        except httpx.HTTPError as exc:
            raise CollectionError(
                Outcome.TRANSIENT, f"network error: {type(exc).__name__}"
            ) from exc
        html = resp.text[:MAX_BYTES]
        headers = dict(resp.headers)
        final = urlparse(str(resp.url))
        expected = urlparse(url)
        if final.hostname != expected.hostname or "/hotel/" not in final.path:
            raise CollectionError(
                Outcome.STRUCTURE_CHANGED,
                f"unexpected redirect to {final.hostname}{final.path[:80]} (not the property page)",
            )
        sig = Signals(
            http_status=resp.status_code,
            headers=headers,
            page_text=_TAGS.sub(" ", html)[:4000],
            waf_challenge=looks_like_waf_challenge(html, headers),
            retry_after=_retry_after(headers),
        )
        data = extract_structured(html, slug)
        if resp.status_code == 200 and not sig.waf_challenge and data.blocks:
            return PageFetch(resp.status_code, str(resp.url), html, data)
        outcome = classify(sig)
        messages = {
            Outcome.WAF_CHALLENGE: f"AWS WAF challenge page (HTTP {resp.status_code}) served "
            "instead of the property page; it requires running the site's challenge script, "
            "which this collector deliberately does not do",
            Outcome.CAPTCHA: "a CAPTCHA (human verification) was served",
            Outcome.BLOCKED: f"access denied (HTTP {resp.status_code})",
            Outcome.RATE_LIMITED: "rate limited (HTTP 429)",
            Outcome.TRANSIENT: f"server error (HTTP {resp.status_code})",
        }
        # A 200 page that is a CAPTCHA/challenge is a site decision, not a layout change.
        if outcome == Outcome.STRUCTURE_CHANGED or (
            resp.status_code == 200 and outcome not in SITE_DECISIONS
        ):
            raise CollectionError(
                Outcome.STRUCTURE_CHANGED,
                f"page loaded (HTTP {resp.status_code}) but contains no structured data",
            )
        raise CollectionError(
            outcome, messages.get(outcome, outcome.value), retry_after=sig.retry_after
        )


def _retry_after(headers: dict[str, str]) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after" and v.strip().isdigit():
            return float(v)
    return None


def _emit_warning(events: EventLog, slug: str, event: str, data: dict[str, object]) -> None:
    events.emit(event, property=slug, level="warning", **data)


def default_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.8"},
        timeout=settings.collect_nav_timeout_seconds,
        follow_redirects=True,
    )


def collect_http(
    factory: sessionmaker[Session],
    settings: Settings,
    slugs: list[str] | None = None,
    *,
    client: httpx.Client | None = None,
    clock: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectionReport:
    with factory() as s:
        props = [
            p
            for p in s.scalars(select(Property).where(Property.active).order_by(Property.id))
            if not slugs or p.slug in slugs
        ]
        for p in props:
            s.expunge(p)
    params: dict[str, Any] = {
        "browser_mode": HTTP_MODE,
        "strategy": STRATEGY,
        "properties": [p.slug for p in props],
    }
    until = active_cooldown(factory, HTTP_MODE, settings.collect_block_cooldown_hours, clock())
    if until is not None:
        return _skip_for_cooldown(factory, "page-check", params, until, clock)

    with factory() as s:
        run_id = start_run(
            s,
            kind="collect",
            source=SOURCE,
            scope_key=f"collect:{SOURCE}",
            mode="page-check",
            version=VERSION,
            params=params,
            now=clock(),
        ).id
    events = EventLog(Path(settings.collect_log_dir), run_id)
    recorder = _Recorder(factory, run_id, clock)
    policy = RetryPolicy(
        settings.collect_max_attempts,
        settings.collect_backoff_base_seconds,
        settings.collect_backoff_max_seconds,
    )
    pacer = Pacer(
        settings.collect_property_delay_min, settings.collect_property_delay_max, sleep=sleep
    )
    owns_client = client is None
    http = client or default_client(settings)
    source = PublicPageCollector(http)
    events.emit("run_start", strategy=STRATEGY, properties=params["properties"])
    results: list[dict[str, Any]] = []
    refused: Outcome | None = None
    try:
        for i, prop in enumerate(props):
            recorder.begin_property(prop.id)
            res = PropertyResult(prop.slug)
            imported: ImportReport | None = None
            if refused is not None:
                res.status = "blocked"
                res.error = (
                    f"not requested: the site answered an earlier property with {refused.value}"
                )
            else:
                if i:
                    pacer.wait()
                try:
                    page: PageFetch = run_with_retries(
                        partial(source.fetch, prop.booking_url or "", prop.slug),
                        policy,
                        describe="fetch property page",
                        sleep=sleep,
                        on_event=partial(_emit_warning, events, prop.slug),
                    )
                    res.info = SourceInfo(
                        headline_total=page.data.review_count, headline_score=page.data.score
                    )
                    res.pages, res.received = 1, len(page.data.reviews)
                    if page.data.reviews:
                        imported = recorder.import_spool(prop.slug, page.data.reviews)
                    res.status = "success"
                except CollectionError as exc:
                    res.status, res.outcome, res.error = exc.status, exc.outcome, str(exc)
                    if exc.site_decision:
                        refused = exc.outcome
                        events.emit(
                            "circuit_open",
                            property=prop.slug,
                            level="warning",
                            outcome=exc.outcome.value,
                        )
            recorder.finish_property(prop.id, res, res.status, res.error, imported)
            events.emit(
                "property_done",
                property=prop.slug,
                level="info" if res.status == "success" else "warning",
                status=res.status,
                outcome=res.outcome.value if res.outcome else None,
                error=res.error,
            )
            results.append(
                {
                    "property": prop.slug,
                    "status": res.status,
                    "outcome": res.outcome.value if res.outcome else "ok",
                    "pages": res.pages,
                    "collected": res.received,
                    "new": imported.new if imported else 0,
                    "updated": imported.updated if imported else 0,
                    "unchanged": imported.unchanged if imported else 0,
                    "headline_total": res.info.headline_total,
                    "headline_score": res.info.headline_score,
                    "error": res.error,
                }
            )
    except BaseException as exc:
        with factory() as s:
            finish_run(
                s, run_id, status="failed", now=clock(), error=f"{type(exc).__name__}: {exc}"[:500]
            )
        raise
    finally:
        if owns_client:
            http.close()

    states = {r["status"] for r in results}
    status = (
        "success"
        if states == {"success"}
        else "blocked"
        if "success" not in states and "blocked" in states
        else "failed"
        if "success" not in states
        else "partial"
    )
    errors = "; ".join(f"{r['property']}: {r['error']}" for r in results if r["error"])
    with factory() as s:
        finish_run(
            s,
            run_id,
            status=status,
            now=clock(),
            items_total=len(results),
            items_ok=sum(r["status"] == "success" for r in results),
            items_failed=sum(r["status"] != "success" for r in results),
            error=errors[:500] or None,
        )
    events.emit("run_end", status=status)
    report = CollectionReport(run_id, status, log_path=str(events.path) if events.path else None)
    report.properties = results
    return report
