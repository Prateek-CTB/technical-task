# 0012 Single-flight scope per run kind

**Status:** Accepted. Crash recovery in [ADR 0019](0019-run-liveness-and-recovery.md).

**Context:** A blanket "one running run per kind and source" would block unrelated imports for no reason.

**Decision:** `run.scope_key` is NOT NULL, and a partial unique index allows one `running` run per scope key.

| Kind | `scope_key` | Reason |
|---|---|---|
| collect | `collect:<source>` | One browser session per source; politeness towards the source; shared checkpoints |
| import | `import:<file_sha256>` | Only the *same file* is blocked (e.g. a double click). Different files run in parallel; overlapping reviews are made safe by `UNIQUE(property_id, dedupe_key)` and upsert-on-conflict |
| analyse | `analyse:<model_id>:<prompt_version>:<taxonomy_version>` | Two runs of the same configuration only duplicate paid work; different configurations (e.g. a model comparison) are independent |

**Consequences:** Imports are not serialised. The ingestion code (P2) must handle a unique-constraint race by re-reading and updating, and that is tested. A crashed run is marked `abandoned` by heartbeat timeout, which frees its scope.
