"""Playwright I/O for the public-page collector. Stock browser, no identity changes (ADR 0002).

* Headless by default; ``headed`` is an explicit alternative for machines with a display.
* Persistent profile (cookies / storage) when a profile directory is configured. The profile is
  the browser's own ordinary session state: nothing is injected into it.
* Every failure is turned into a classified :class:`CollectionError` from observed signals.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .engine import Page, SourceInfo
from .public import CARD, EXTRACT_JS, TRIGGER, card_to_record
from .signals import CollectionError, Outcome, Signals, classify, looks_like_waf_challenge
from .structured import extract_structured

_TOTAL_JS = (
    "() => { const s=[...document.querySelectorAll('select')]"
    ".find(x => /^All \\(/.test(x.innerText.trim()));"
    " return s ? s.innerText.trim().split('\\n')[0] : '' }"
)


class BrowserUnavailable(CollectionError):
    """The browser itself cannot start (not installed, missing system libraries)."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            Outcome.STRUCTURE_CHANGED,
            f"browser could not start: {detail[:200]}. Install it with "
            "'python -m playwright install --with-deps chromium'",
        )

    @property
    def status(self) -> str:
        return "failed"


def _require_display() -> None:
    """Headed mode on Linux needs a display. Fail clearly rather than with a Chromium crash."""
    if (
        sys.platform.startswith("linux")
        and not os.environ.get("DISPLAY")
        and not os.environ.get("WAYLAND_DISPLAY")
    ):
        raise BrowserUnavailable("headed mode needs a display (DISPLAY is not set); use headless")


@contextmanager
def browser_session(
    *,
    headless: bool,
    profile_dir: Path | None,
    nav_timeout_s: float,
    action_timeout_s: float,
) -> Iterator[Any]:
    """Yield a Playwright browser context; own Playwright instance, so safe per worker thread."""
    from playwright.sync_api import sync_playwright

    if not headless:
        _require_display()
    with sync_playwright() as pw:
        opts: dict[str, Any] = {
            "locale": "en-GB",
            "timezone_id": "Australia/Sydney",
            "viewport": {"width": 1366, "height": 900},
        }
        try:
            if profile_dir is not None:
                profile_dir.mkdir(parents=True, exist_ok=True)
                ctx: Any = pw.chromium.launch_persistent_context(
                    str(profile_dir), headless=headless, **opts
                )
                closer: Any = ctx
            else:
                browser = pw.chromium.launch(headless=headless)
                ctx = browser.new_context(**opts)
                closer = browser
        except Exception as exc:
            raise BrowserUnavailable(str(exc).splitlines()[0]) from exc
        ctx.set_default_navigation_timeout(nav_timeout_s * 1000)
        ctx.set_default_timeout(action_timeout_s * 1000)
        try:
            yield ctx
        finally:
            with contextlib.suppress(Exception):
                closer.close()


class BookingPageSource:
    """One property's review list in one browser tab."""

    def __init__(self, ctx: Any, slug: str, url: str, *, action_timeout_s: float = 45) -> None:
        self._ctx, self._slug, self._url = ctx, slug, url
        self._timeout_ms = action_timeout_s * 1000
        self._page: Any = None
        self._main_status: int | None = None
        self._main_headers: dict[str, str] = {}
        self._refusal: str | None = None
        self._last_first = ""

    # -- lifecycle ------------------------------------------------------------------------
    def close(self) -> None:
        if self._page is not None:
            try:
                self._page.close()
            finally:
                self._page = None

    def _fresh_page(self) -> Any:
        self.close()
        self._refusal = None
        page = self._ctx.new_page()

        def on_response(r: Any) -> None:
            try:
                if r.request.resource_type == "document" and r.frame == page.main_frame:
                    self._main_status, self._main_headers = r.status, dict(r.headers)
                elif "graphql" in r.url:
                    body = r.text()
                    if "reviewListFrontend" in body and "Request not supported" in body:
                        self._refusal = "Request not supported"
            except Exception:
                return

        page.on("response", on_response)
        self._page = page
        return page

    # -- signals --------------------------------------------------------------------------
    def _fail(self, exc: Exception | None, what: str) -> CollectionError:
        """Turn the current page state into a classified error."""
        page = self._page
        sig = Signals(
            http_status=self._main_status,
            headers=self._main_headers,
            api_refusal=self._refusal,
            timed_out=type(exc).__name__ == "TimeoutError",
            network_error=None if type(exc).__name__ == "TimeoutError" else str(exc)[:200],
            retry_after=_retry_after(self._main_headers),
        )
        try:
            sig.page_text = page.inner_text("body", timeout=3000)[:4000]
            sig.cards_found = page.locator(CARD).count()
            if not sig.cards_found:
                sig.waf_challenge = looks_like_waf_challenge(
                    page.content()[:50000], self._main_headers
                )
        except Exception:
            pass
        outcome = classify(sig)
        messages = {
            Outcome.BLOCKED: 'the site refused the review list to this browser ("Request not supported")'
            if self._refusal
            else f"access refused (HTTP {self._main_status})",
            Outcome.CAPTCHA: "a human-verification challenge (CAPTCHA) is being shown",
            Outcome.WAF_CHALLENGE: "an AWS WAF challenge page was served instead of the property page",
            Outcome.RATE_LIMITED: "rate limited (HTTP 429)",
        }
        msg = messages.get(outcome, f"{what}: {outcome.value}")
        if outcome in (Outcome.TRANSIENT, Outcome.STRUCTURE_CHANGED) and exc is not None:
            msg += f" ({type(exc).__name__})"
        return CollectionError(outcome, msg, retry_after=sig.retry_after)

    # -- PageSource -----------------------------------------------------------------------
    def open(self, start_page: int = 1) -> SourceInfo:
        page = self._fresh_page()
        info = SourceInfo()
        try:
            page.goto(self._url, wait_until="load")
            trigger = page.locator(TRIGGER).first
            trigger.wait_for(timeout=self._timeout_ms)
            page.wait_for_timeout(2500)  # let the page settle before interacting
            info.headline_score = _headline_score(page)
            trigger.click()
            page.wait_for_selector(CARD, timeout=self._timeout_ms)
            page.wait_for_timeout(1500)
            m = re.search(r"\((\d[\d,]*)\)", page.evaluate(_TOTAL_JS) or "")
            info.headline_total = int(m.group(1).replace(",", "")) if m else None
            info.sorted_newest = _sort_newest(page)
            for _ in range(start_page - 1):  # resume: pages cannot be skipped, only paged through
                page.wait_for_timeout(1200)
                if not self.advance():
                    break
            self._last_first = ""
        except CollectionError:
            raise
        except Exception as exc:
            raise self._fail(exc, "opening the review list") from exc
        return info

    def read_current(self) -> Page:
        page = self._page
        try:
            cards = page.evaluate(EXTRACT_JS)
        except Exception as exc:
            raise self._fail(exc, "reading reviews") from exc
        if not cards:
            raise self._fail(None, "no review cards")
        records, skipped = [], 0
        for c in cards:
            rec = card_to_record(c, self._slug)
            if rec is None:
                skipped += 1
            else:
                records.append(rec)
        self._last_first = _first_key(cards)
        nxt = page.locator('button[aria-label="Next page"]').first
        has_next = nxt.count() > 0 and not nxt.is_disabled()
        return Page(number=0, records=records, skipped=skipped, has_next=has_next)

    def advance(self) -> bool:
        page = self._page
        try:
            nxt = page.locator('button[aria-label="Next page"]').first
            if nxt.count() == 0 or nxt.is_disabled():
                return False
            before = self._last_first or _first_key(page.evaluate(EXTRACT_JS))
            nxt.click()
            for _ in range(35):  # up to ~20 s for dynamically loaded cards to change
                page.wait_for_timeout(600)
                cards = page.evaluate(EXTRACT_JS)
                if cards and _first_key(cards) != before:
                    return True
            return True  # unchanged content is caught by the engine's pagination check
        except Exception as exc:
            raise self._fail(exc, "turning the page") from exc


def _first_key(cards: list[dict[str, str]]) -> str:
    c = cards[0] if cards else {}
    return c.get("positive", "") + c.get("date", "") + c.get("title", "")


def _retry_after(headers: dict[str, str]) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after" and v.strip().isdigit():
            return float(v)
    return None


def _headline_score(page: Any) -> float | None:
    """Booking.com's own published score (kept separate from our calculated averages).

    Preferred source: the page's schema.org JSON-LD (``aggregateRating``), which is stable
    across layout changes. Fallback: the visible "Scored x.x" text."""
    try:
        data = extract_structured(page.content())
        if data.score is not None:
            return data.score
    except Exception:
        pass
    for sel in ('[data-testid="review-score-component"]', "body"):
        try:
            m = re.search(
                r"Scored\s+(\d+(?:\.\d+)?)", page.locator(sel).first.inner_text(timeout=5000)
            )
        except Exception:
            continue
        if m:
            return float(m.group(1))
    return None


def _sort_newest(page: Any) -> bool:
    try:
        select = page.locator('[data-testid="review-sorter"] select, select[name*="sort" i]').first
        if select.count() == 0:
            return False
        select.select_option(label="Newest first")
        page.wait_for_timeout(2500)
        return True
    except Exception:
        return False
