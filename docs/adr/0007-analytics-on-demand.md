# 0007 Analytics computed on demand

**Status:** Accepted

**Decision:** Metrics are computed from review rows per request; there are no materialised aggregates. Periods use Australia/Sydney wall-clock windows converted to UTC instants (proposal §4).

**Consequences:** Always consistent and simple. Revisit if data grows about 100×.
