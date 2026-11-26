# P1 verification review and P2 report

Date: 2026-11-26. Scope: the five verification requests, the AI-provider decision, and P2 (ingestion core + CSV/JSON adapters).
Method: every claim below was checked by running something; "found" means demonstrated, not suspected.

## 1. Findings

| # | Request | Finding (before) | Fix | Evidence now |
|---|---|---|---|---|
| F1 | Migrations vs models: constraints, indexes, FKs, partial indexes on both engines | **The P1 parity test was weaker than reported.** `compare_metadata` ignores CHECKs, partial-index predicates, defaults and FK actions; dropping the predicate of `ux_run_scope_running` produced an empty diff. No actual drift existed, but the test could not have shown it. | Reflection-based comparison of the migrated DB against a DB built from the models, plus named anchors and a negative control (`tests/test_schema_equivalence.py`). ADR 0016 corrects the claim. | 12 tests pass (6 on each engine); no migration/model difference found; negative control fails as it should |
| F2 | Safe recovery of stale running jobs | **Not implemented.** Schema columns existed (`heartbeat_at`, status `abandoned`); no code used them. A crash would block its scope forever. | `persistence/runs.py`: heartbeat, conditional reaping at start of same-scope runs, `reviewhub runs reap`, zombie protection (ADR 0019) | 18 tests on both engines, incl. a **real process killed with `os._exit`** and recovered; import crash test: nothing committed, scope recovers, re-import idempotent |
| F3 | Local dates/weeks from each property's timezone | **Global Sydney zone** used (`collector/parser.py:137`); `property.timezone` ignored. Correct today only by coincidence. | `core/timeutil.py`; ingestion derives dates from the row's property zone; invariant check + repair; invalid zone blocks import (ADR 0020) | Tests: same instant gives different date/week at Sydney vs Perth; both DST transitions; Sun/Mon boundaries; zone change is detected then repaired; every imported row satisfies the invariant |
| F4 | Failed/incomplete analysis cannot become current | **Not prevented:** `status='failed', is_current=true` was accepted. | CHECK `NOT is_current OR status IN ('ok','needs_review')` (migration 0001 amended, ADR 0016); a failed retry cannot displace a good analysis | 4 new constraint tests on both engines |
| F5 | Synthetic data isolation; experimental records can't silently reach reporting | **Only a column and prose.** Nothing stopped mixing or unscoped reporting. | `persistence/scopes.py`: `reportable()` default = production only; `assert_can_load` refuses mixing; imports cannot choose their source; provenance in every report (ADR 0017) | Tests: isolation matrix, forged-source adapters refused before a run is created, reporting scope, source-set partition, `source` column ignored |

Not found / unchanged: unique-key null handling (ADR 0011) and composite FK consistency re-verified; FK enforcement on SQLite verified; NUL-byte and over-long values handled so one row cannot abort a PostgreSQL import.

### ADR review
* ADR 0004 and the P1 report overstated schema verification → ADR 0016.
* ADR 0012 described `abandoned` recovery that did not exist → ADR 0019.
* ADR 0013 stated isolation rules with no enforcement → ADR 0017.
* ADR 0006 (Anthropic as default provider) conflicts with the new constraint → superseded by ADR 0018.
* Local-date derivation was undocumented → ADR 0020.
* A new open decision, **D7** (may real guest text go to a hosted AI provider?), was identified.

### Things I want to be explicit about
* Migration 0001 was **amended**, not extended. ADR 0016 states the policy: allowed only because nothing outside test runs was created from it. If you created `data/reviewhub.db` yourself, delete it and run `reviewhub db upgrade`.
* A free hosted AI API still requires a free-tier key; only a local model needs none. I have not verified any provider's current terms or limits (ADR 0018).

## 2. P2 delivered
`reviewhub import`, `POST /api/v1/ingestion/imports`, `reviewhub runs reap`, documented in [IMPORT_FORMAT.md](IMPORT_FORMAT.md).

| Acceptance criterion | Evidence (all on SQLite and PostgreSQL) |
|---|---|
| Same file twice → 0 new / 0 updated | `test_importing_the_same_file_twice_changes_nothing`, 1,200-row variant |
| One edited row → exactly 1 update + 1 revision | `test_an_edited_row_is_one_update_and_one_revision` (+ reply added later, date corrected) |
| Bad rows reported with line, column, code; valid rows commit | `test_bad_rows_are_reported_precisely...` (exact `(line, code, column)` list) |
| Whole-file errors write nothing | 8 file-level cases: no reviews, no issues, no run-property rows; one *failed run row* is kept for audit |
| Same file concurrently refused; different files run in parallel | tests + API 409 + CLI exit 4; a different file proceeds |
| Unique-race between parallel imports resolves | threaded overlapping imports (no duplicates, each review "new" once) **and** a deterministic stale-read race that is reconciled |
| Crash leaves DB consistent and resumable | killed-process test (see F2) |
| PII absent from stored data | sentinel names/e-mail/avatar scanned across **every table**; none found |

Also: dry-run reports exactly what a real run does (compared field by field) and writes nothing; DQ001-DQ007, DQ012, DQ016, DQ017, DQ019 implemented (DQ006 duplicate-text detection included); `info` findings are counted, not stored.

## 3. Known limitations (P2)
* A review **without an id** cannot be recognised if its text is edited; it appears as a new review (documented).
* Only ISO 8601 dates are understood without `--date-format`; no auto-detection of other layouts, by design.
* Ratings are 1-10 only; a 1-5 export would be accepted as very poor scores. A scale option is not implemented.
* On SQLite, the write phase of concurrent imports serialises at the database level (single writer); "parallel" is exact on PostgreSQL.
* Free-text fields can still contain names the guest typed themselves; only structured personal-data columns are excluded.
* DQ006 only compares reviews within the same property and same rating.
* Not implemented in P2 (later phases): analysis queueing after import (P4), analytics (P4/P5), the Booking public-page adapter (P8), `seed-demo` (P3).
* Legacy experimental modules (`config.py`, `db.py`, `collector/*`, `insights/*`) are still in the tree, excluded from lint/type checks, and unused by the new code. They are retired in P4/P8.
