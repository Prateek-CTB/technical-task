"""What the site told us, reduced to one decision (ADR 0022).

The browser layer gathers raw :class:`Signals`; :func:`classify` is a pure function so every
branch is unit-tested without a browser. The important distinction is between conditions that
*retrying can fix* (network errors, 5xx, rate limits) and conditions where the site has made a
decision about us (CAPTCHA, explicit refusal), where retrying would be pressing against a control
and the correct action is to stop and report.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Outcome(StrEnum):
    OK = "ok"
    TRANSIENT = "transient"  # network error, timeout, 5xx: retry with backoff
    RATE_LIMITED = "rate_limited"  # 429: retry after the server's Retry-After / backoff
    CAPTCHA = "captcha"  # a human-verification challenge is being shown: stop
    WAF_CHALLENGE = "waf_challenge"  # AWS WAF JavaScript challenge instead of content: stop
    BLOCKED = "blocked"  # explicit refusal / access denied: stop
    STRUCTURE_CHANGED = "structure_changed"  # loaded fine but not what we expect: stop, report


#: Text that means a human-verification challenge is on screen. Deliberately not "awswaf" or
#: "challenge": the WAF's background script is present on ordinary pages that load fine.
CAPTCHA_MARKERS = (
    "captcha",
    "verify you are human",
    "verify that you are human",
    "are you a robot",
    "confirm you are not a robot",
    "unusual traffic",
    "press & hold",
)

#: Markers of an AWS WAF challenge *page* (served instead of the content). Matched on raw HTML
#: only together with "no review content", because Booking's normal pages also load the WAF
#: integration script.
WAF_CHALLENGE_MARKERS = (
    "awswafintegration",
    "challenge-container",
    "/challenge.js",
    "awswafcookiedomainlist",
)


def looks_like_waf_challenge(html: str, headers: Mapping[str, str] | None = None) -> bool:
    """True for an AWS WAF challenge interstitial (verified live 2026-11-26: HTTP 202, ~4 KB,
    ``challenge.js`` + ``AwsWafIntegration.getToken()``, no page content)."""
    lowered = {k.lower(): v.lower() for k, v in (headers or {}).items()}
    if lowered.get("x-amzn-waf-action") in ("challenge", "captcha"):
        return True
    low = html.lower()
    hits = sum(m in low for m in WAF_CHALLENGE_MARKERS)
    has_content = "application/ld+json" in low or 'data-testid="review-card"' in low
    return hits >= 2 and not has_content


#: GraphQL/API bodies that are an explicit "no" to this client.
REFUSAL_MARKERS = ("request not supported", "access denied", "forbidden")


@dataclass
class Signals:
    http_status: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    page_text: str = ""
    api_refusal: str | None = None  # body fragment of an API refusal, if one was seen
    network_error: str | None = None
    timed_out: bool = False
    cards_found: int = 0
    retry_after: float | None = None
    waf_challenge: bool = False  # the raw page was an AWS WAF challenge interstitial


def classify(sig: Signals) -> Outcome:
    if sig.cards_found > 0:
        return Outcome.OK
    if sig.api_refusal and any(m in sig.api_refusal.lower() for m in REFUSAL_MARKERS):
        return Outcome.BLOCKED
    headers = {k.lower(): v.lower() for k, v in sig.headers.items()}
    waf_action = headers.get("x-amzn-waf-action", "")
    text = sig.page_text.lower()
    if "captcha" in waf_action or any(m in text for m in CAPTCHA_MARKERS):
        return Outcome.CAPTCHA
    if sig.waf_challenge or waf_action == "challenge":
        return Outcome.WAF_CHALLENGE
    status = sig.http_status
    if status == 429:
        return Outcome.RATE_LIMITED
    if status in (401, 403, 405, 451):
        return Outcome.BLOCKED
    if status is not None and status >= 500:
        return Outcome.TRANSIENT
    if sig.network_error:
        return Outcome.TRANSIENT
    if sig.timed_out:
        # Loaded (2xx/3xx) with real content but the expected element never appeared: the page
        # changed. Without content it is more likely a slow or broken response: retry.
        loaded = status is not None and status < 400 and len(text.strip()) > 200
        return Outcome.STRUCTURE_CHANGED if loaded else Outcome.TRANSIENT
    return Outcome.STRUCTURE_CHANGED


RETRYABLE = frozenset({Outcome.TRANSIENT, Outcome.RATE_LIMITED})
#: Outcomes where the site made a decision about this client: stop the whole run.
SITE_DECISIONS = frozenset({Outcome.CAPTCHA, Outcome.BLOCKED, Outcome.WAF_CHALLENGE})


class CollectionError(Exception):
    """A classified failure. ``status`` is the value stored in ``run_property.status``."""

    def __init__(self, outcome: Outcome, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.outcome in RETRYABLE

    @property
    def site_decision(self) -> bool:
        return self.outcome in SITE_DECISIONS

    @property
    def status(self) -> str:
        return "blocked" if self.site_decision else "failed"
