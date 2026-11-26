# 0017 Dataset isolation (implements ADR 0013 for experimental data too)

**Status:** Accepted

**Context:** `review.source` was only a column. Nothing stopped demo or experimental rows from reaching a report, or being loaded next to real ones. ADR 0013 stated the rule for demo data; the withdrawn browser experiment (ADR 0002) needs the same protection.

**Decision:** Three kinds of rows, defined in `persistence/scopes.py`, form a partition of the allowed `source` values (tested):

| Kind | Sources |
|---|---|
| production | `booking_public`, `csv_import`, `json_import` |
| demo | `demo_synthetic` |
| experimental | `booking_experimental` |

1. **Reporting** selects rows only through `reportable(include_demo=False, include_experimental=False)`. The default is production only. Every analytics response carries `dataset_provenance()`.
2. **Loading** calls `assert_can_load(session, source)`: production data is refused into a database that holds demo or experimental rows, and the reverse. The error says how to proceed.
3. **A file cannot choose its source.** Import adapters hard-code `csv_import` / `json_import`; the pipeline refuses any adapter that claims another source (`ForbiddenSource`) before a run is created; a `source` column in a file is ignored. Demo data can only come from the seed command (P3) and experimental data from no command at all.
4. Experimental records are not loaded anywhere in this project (decision D2 is open).

**Consequences:** A database is either real or demo, never silently both. To switch, use a fresh database. Reporting code that bypasses `reportable()` is a defect; P4's tests will assert that every analytics query is scoped.
