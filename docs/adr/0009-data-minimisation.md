# 0009 Data minimisation

**Status:** Accepted

**Decision:** Reviewer names, avatars and photos are never stored in any column or JSON payload. Guest country and traveller type are kept: useful in aggregate, not identifying alone.

**Consequences:** The review feed cannot show reviewer names.
