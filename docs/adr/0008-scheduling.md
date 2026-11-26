# 0008 Scheduling

**Status:** Accepted

**Decision:** CLI commands run by the OS scheduler (cron / Windows Task Scheduler). Every run is recorded in the `run` table. No Celery or Redis.

**Consequences:** No distributed queue. Concurrency is bounded per run kind (ADR 0012).
