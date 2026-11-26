# 0019 Run liveness and stale-run recovery (implements ADR 0012's "abandoned")

**Status:** Accepted

**Context:** A worker that dies (power loss, kill, OOM) cannot update its own `run` row. Without recovery its `running` row would hold the single-flight scope forever. The P1 schema had `heartbeat_at` and an `abandoned` status, but no code used them.

**Decision** (`persistence/runs.py`):
* Every `running` run has a heartbeat (set at start, refreshed by the worker). A run whose last heartbeat (or start) is older than `REVIEWHUB_RUN_STALE_AFTER_MINUTES` (default 15) is **stale**.
* `start_run` first reaps stale runs **of the same scope**, then inserts. The insert is the single-flight check (partial unique index); the reap is a conditional `UPDATE ... WHERE status='running'`, so a run that finishes in the meantime is never overwritten. A live run is never reaped.
* Reaping sets `abandoned`, `finished_at`, an explanatory `error`, and fails the run's unfinished `run_property` rows. `reviewhub runs reap` does it globally (for cron).
* A worker that was reaped but is still alive ("zombie") learns it: `heartbeat` returns False, `finish_run` will not overwrite `abandoned`, and an import checks its heartbeat at the start of its write transaction and refuses to write.
* Data safety does not depend on recovery: an import's writes are one transaction, so a crash leaves no partial data (tested with a real process killed by `os._exit` on both engines). Re-running is idempotent.
* Heartbeats are sent at stage boundaries and are never sent from inside the data transaction on a second connection, which on SQLite would wait for the write lock held by the first.

**Consequences:** A crash blocks its scope for at most the stale window. The window must exceed the longest gap between heartbeats; an import's write phase is a single transaction lasting seconds. Long collection runs (P8) must heartbeat per page.
