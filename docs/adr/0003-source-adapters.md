# 0003 Source adapters and one pipeline

**Status:** Accepted

**Context:** Reviews can arrive from a browser adapter, CSV or JSON.

**Decision:** Every adapter yields raw records into one pipeline: normalise → validate → deduplicate → persist → queue analysis. Business rules live in the pipeline, not in the adapters.

**Consequences:** A new source is one adapter. A small interface cost is accepted up front.
