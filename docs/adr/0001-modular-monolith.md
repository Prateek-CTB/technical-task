# 0001 Modular monolith

**Status:** Accepted

**Context:** About 10 new reviews a week across four properties, one team, one operator group. No independent scaling or deployment needs.

**Decision:** One Python package (`reviewhub`) with modules `core → persistence → {ingestion, analytics, intelligence} → api`, plus a separately built SPA. Dependencies point downwards only; source adapters never touch the database.

**Consequences:** Simple operations and debugging. Module boundaries are kept by layout and code review, not by process boundaries. Splitting a module out later is possible because the interfaces (adapter, analyzer) are narrow.
