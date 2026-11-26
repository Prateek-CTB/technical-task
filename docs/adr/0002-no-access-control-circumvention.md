# 0002 No circumvention of access controls

**Status:** Accepted. The choice of real data source is open (D1).

**Context:** Booking.com answers an unmodified automated browser's review-list request with an explicit refusal ("Request not supported"). Review-level data was obtained only during an experiment that used anti-detection measures and replayed an internal API call.

**Decision:** The product never evades bot or access controls: no fingerprint changes, no spoofed identity, no replay of internal endpoints. The public-page adapter reads only what an unmodified browser receives and records a `blocked` run, without retry, when refused. Owner-side sources (Extranet export, channel manager) via CSV/JSON import are the primary route to review-level data. The experimental records are not production data.

**Consequences:** Live review collection from the public pages is not available. The brief's "collect from the four pages" requirement is met with a documented limitation and a permitted alternative.
