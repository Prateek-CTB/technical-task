# 0020 Local dates are derived from each property's timezone

**Status:** Accepted

**Context:** The P1 code derived local dates and weeks from one global Sydney zone (`collector/parser.py`), although `property.timezone` exists. All four properties are in Sydney today, so the result was right by coincidence only.

**Decision:**
* Instants are stored in UTC. `published_date_local` and `published_week_local` (Monday) are derived at ingestion from `property.timezone` and stored for indexing and grouping.
* Timestamps in files: explicit offset or `Z` is exact; date-and-time without an offset is read as **property-local** time; a date alone is local midnight. Only ISO 8601 is understood unless the operator supplies `--date-format`, because `03/04/2026` is ambiguous. The last two cases raise an info finding (DQ016). DST edge cases (both transitions, Sunday/Monday boundaries) are tested.
* An invalid zone on a property rejects imports for that property (nothing is guessed).
* Stored values can go stale if a zone is corrected. `find_local_date_mismatches` is the invariant check (every row's stored values must equal a fresh derivation) and `recompute_local_dates` repairs them. The invariant is asserted after imports in tests.
* The fallback review fingerprint uses the published value *as written in the file*, so correcting a zone does not change review identity.
* **Portfolio aggregates** (all properties together) use the reporting timezone from settings (Australia/Sydney, per the brief). While every property shares it, per-property and portfolio weeks coincide. If properties in different zones are ever added, portfolio weeks need an explicit decision; this ADR does not make one.

**Consequences:** Local dates are consistent with each property's configuration and verifiable. A timezone change requires running the repair command.
