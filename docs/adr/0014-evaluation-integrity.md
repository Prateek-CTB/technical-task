# 0014 Evaluation integrity

**Status:** Accepted

**Decision:**
* The human-labelled set is split once into dev and held-out test, deterministically (hash of review id with a fixed seed). The split is recorded in the label file.
* Prompts, lexicon rules, thresholds and confidence rules are tuned on dev only.
* The test split is scored only through `reviewhub evaluate --split test`, which appends every run (configuration hash, timestamp) to an access log committed with the report, so tuning against test would be visible.
* A configuration is scored on test once. Changing it after seeing test results starts a new, disclosed iteration.
* Labels are produced by humans who are blind to model output.

**Consequences:** Accuracy figures are credible and the process is auditable. Confidence intervals are wide at small samples, and the report says so.
