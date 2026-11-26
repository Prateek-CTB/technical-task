# Project status

Updated 2026-11-26 (final). The [README](README.md) is the entry point; decisions are in [docs/adr/](docs/adr/README.md).

| Area | State |
|---|---|
| Review collection: visible browser, headless, HTTPS + structured data, inbox, feed | **Done.** Live: 973 real reviews; WAF challenge and headless refusal detected and reported as BLOCKED (ADR 0022, 0024) |
| Import pipeline: CSV/JSON, validation, dedupe, partial failures, isolation | **Done**, plus a dashboard workflow (validate → preview → import) |
| Analysis: offline rule-based for every review + OpenRouter LLM, evidence verified | **Done.** 970/970 analysed (29 AI, 941 rule-based); LLM coverage grows within the free quota (ADR 0023) |
| Analytics API: overview, comparison, trends, topics, explorer, recommendations, coverage, collection status | **Done** |
| Next.js dashboard: 5 pages + property detail | **Done.** shadcn-style design, light/dark + 6 colour presets, guided tour, Update data actions (collect, AI, offline), responsive, accessible |
| Tests | Backend 698 (SQLite + PostgreSQL), frontend 22 unit, 60 E2E (real + demo, desktop + mobile) |
| Documentation | README, 24 ADRs, import format, data provenance |

## Open items (need Azzurro)
- **D1:** an owner export (Extranet / channel manager) for complete, unattended review data. The import, inbox and feed are ready for it.
- **D6:** a human-labelled evaluation set, needed before any accuracy figure is claimed.
- **D7:** whether real guest text may routinely go to the hosted AI provider (off by default; one capped run was authorised).
- Booking.com's terms restrict automated access: a business decision on whether to run the public-page collector.

## Not done / known gaps
- The CI workflow has not run on GitHub (all of its steps were run locally).
- No stay-date view (publication-date weeks only).
- An edited public review appears as a new review (no public review ID).
