# 0013 Synthetic demo data

**Status:** Accepted. Enforcement mechanism in [ADR 0017](0017-dataset-isolation.md).

**Decision:**
* Generated deterministically from a fixed seed; the same seed gives byte-identical output.
* All review text is invented from templates. No real guest text is used or paraphrased.
* Rows carry `source='demo_synthetic'`.
* The generator refuses to write into a database that contains any non-demo review.
* Reporting endpoints exclude `demo_synthetic` unless asked (`include_demo=true`), and every response reports dataset provenance (`meta.dataset.sources`).
* The UI shows a persistent banner whenever demo rows are included.
* Demo data is never presented as genuine guest feedback and is never mixed silently with imported data.

**Consequences:** A fresh install shows an empty dashboard until demo data is loaded on purpose (`reviewhub seed-demo`). That is intended.
