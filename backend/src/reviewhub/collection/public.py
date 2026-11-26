"""Collector for Booking.com's PUBLIC property pages (ADR 0002, ADR 0022).

What it does is exactly what a person does: open the property page, click "Read all reviews",
choose "Newest first", and page through the list. It reads the rendered review cards.

What it deliberately does NOT do: change the browser's identity (no user-agent, webdriver or
fingerprint changes, no stealth flags), replay Booking's internal API calls, solve CAPTCHAs, or
log in. Booking serves the review list to an ordinary visible browser and refuses it to headless
automation ("Request not supported"); the collector accepts whichever answer the site gives. A
refusal is recorded as ``blocked`` and the run stops (no retries, no workarounds). This module
is parsing only; see browser.py (I/O), engine.py (reliability) and service.py (orchestration).
Requests are slow and jittered. Reviewer names are never read.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

COLLECTOR_VERSION = "booking_public/2.0.0"
TRIGGER = '[data-testid="fr-read-all-reviews"]'
CARD = '[data-testid="review-card"]'

# innerText of the card's data-testid elements. The avatar element (reviewer name) is not read.
EXTRACT_JS = """
() => [...document.querySelectorAll('[data-testid="review-card"]')].map(card => {
  const t = id => { const e = card.querySelector('[data-testid="' + id + '"]'); return e ? e.innerText.trim() : ''; };
  return {date: t('review-date'), title: t('review-title'), score: t('review-score'),
          positive: t('review-positive-text'), negative: t('review-negative-text'),
          room: t('review-room-name'), nights: t('review-num-nights'), traveller: t('review-traveler-type'),
          stay: t('review-stay-date')};
})
"""


# --------------------------------------------------------------------------- parsing (pure)
_DATE = re.compile(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})")
_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def parse_review_date(text: str) -> date | None:
    m = _DATE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").date()
    except ValueError:
        return None


def parse_score(text: str) -> float | None:
    m = _NUM.search(text or "")
    return float(m.group(1).replace(",", ".")) if m else None


def card_to_record(card: dict[str, str], slug: str) -> dict[str, Any] | None:
    """One extracted card -> a flat import record, or None if it lacks a date or a score."""
    published, score = parse_review_date(card.get("date", "")), parse_score(card.get("score", ""))
    if published is None or score is None:
        return None
    nights = _NUM.search(card.get("nights", ""))
    record: dict[str, Any] = {
        "property": slug,
        "rating": score,
        "review_date": published.isoformat(),
        "title": card.get("title") or "",
        "liked": card.get("positive") or "",
        "disliked": card.get("negative") or "",
        "traveller_type": card.get("traveller") or "",
        "room_type": card.get("room") or "",
        "nights": int(float(nights.group(1))) if nights else "",
    }
    return record
