# 0015 Delivery sequencing

**Status:** Accepted

**Decision:** A vertical slice (ingestion → persistence → AI analysis → analytics API) is built and reviewed before frontend work (P4 in the proposal), because it proves the central capability. Phase estimates are planning estimates, not deadlines; correctness, tests and documentation are not traded for them. Each phase ends with actual test results and a stop for review.

**Consequences:** The dashboard starts later but is built against a proven API.
