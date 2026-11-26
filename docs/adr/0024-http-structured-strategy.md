# 0024 HTTP + structured-data strategy, and AWS WAF challenge detection

**Status:** Accepted (extends 0022; reaffirms 0002)

**Context:** Facts verified on the live site on 2026-11-26:

| Client | Result |
|---|---|
| Plain HTTPS GET (honest `ReviewHub/x.y` User-Agent) | **HTTP 202**, ~4 KB **AWS WAF challenge page** (`challenge.js`, `AwsWafIntegration.getToken()`), no content and no JSON-LD |
| Headless browser | Page loads, but the review list API answers `Request not supported` (ADR 0022) |
| Visible, unmodified browser | Page **and** reviews load. The page carries one schema.org `Hotel` JSON-LD block with `aggregateRating` (e.g. Olympic Hotel Paddington 6.4/10 from 129 reviews) |
| Review cards (rendered) | **No stable review ID**: no id attribute and no permalink, only `data-testid` fields |

Booking.com's own headline score was not being captured (a text selector had broken).

**Decision**
1. **A third collection strategy**, `reviewhub collect --source http` (`collection/http_page.py`): one plain GET per property per run, paced, with an honest User-Agent. It reads **only** schema.org data: the published score and count, plus review items if the page ever carries them (it does not today). Everything else is classified with the shared outcome model.
2. **WAF challenge detection** (`signals.looks_like_waf_challenge`): an `x-amzn-waf-action: challenge|captcha` header, or at least two challenge markers in the raw HTML **with no page content**. A normal page also loads the WAF script, so markers alone are not enough. New outcome `waf_challenge`, a *site decision* like `captcha` and `blocked`: never retried, never solved; it trips the circuit breaker (the other properties are **not requested**), is stored as `blocked` with its exact reason, and starts the per-mode cooldown. A CAPTCHA served with HTTP 200 is still a CAPTCHA, not a "layout change".
3. **Unexpected redirects** (another host, or a path that is no longer a property page) are `structure_changed` and are never parsed.
4. **The published score comes from JSON-LD**, in both the browser collector (rendered page) and the HTTP strategy, with the old text match as a fallback. It is stored in `score_snapshot`, apart from our own averages, and shown as "calculated by Booking.com".
5. **Identity stays the content fingerprint** (ADR 0003/0022): verified above, the public cards expose no ID.
6. **Reporting.** `GET /api/v1/ingestion/collection-status` gives, per property and per strategy: last attempt, status, `block_reason` (`aws_waf_challenge`, `captcha`, `headless_refused`, `access_denied`, `cooldown`, `circuit_breaker`), last success, cooldown and next eligible time, and our holdings against Booking's published count. The Data & Collection page shows it, with **BLOCKED** shown as blocked, never as success.
7. **Not done, deliberately** (ADR 0002): no running or replaying the challenge script outside a real browser, no token reuse, no stealth or fingerprint changes, no proxy rotation. On 2026-11-26 this was requested again and declined: it would circumvent the site's access control, and the brief forbids it.

**Consequences:** Today the HTTP strategy records `BLOCKED / aws_waf_challenge` after exactly one request (verified live, then a zero-request cooldown re-run). It costs the site one request per day, documents the access situation automatically, and starts returning the published score with no code change if the site ever serves structured data to plain clients. Review-level data comes from the visible-browser collector or from an authorised export or feed.
