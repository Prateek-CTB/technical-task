# P4 end-to-end demonstration

Existing reviews → OpenRouter free model → strict schema → evidence check → database → analytics API.

```bash
cd backend && pip install -e ".[dev]"
# .env (repo root, git-ignored): AI_PROVIDER=openrouter, OPENROUTER_API_KEY=..., OPENROUTER_MODEL=<a :free model>
reviewhub db upgrade
reviewhub seed-demo --end-date 2026-11-25            # 480 fictional reviews
reviewhub ai status --check                           # config valid? model listed and zero-priced? (key never shown)
reviewhub ai analyze --dry-run                        # how many reviews would be sent; no API calls
reviewhub ai analyze --limit 10                       # small batch, spread across the 4 properties
reviewhub ai analyze                                  # resume: only unanalysed reviews; stops safely on quota/limits
reviewhub ai report                                   # coverage, failures, tokens, cost
reviewhub serve                                       # http://127.0.0.1:8000/docs
```

Analytics (synthetic data needs `include_demo=true`; the default scope is production data only):
```
GET /api/v1/analytics/overview            /properties/comparison      /properties/{id}
GET /api/v1/analytics/trends/weekly       /topics                     /reviews
GET /api/v1/analytics/recommendations     /coverage
   ?include_demo=true&as_of=2026-11-25T23:00:00+11:00        (as_of makes a response reproducible)
```
Free quota: roughly 50 `:free` requests per day on this account, so run `reviewhub ai analyze` once a day, or `--limit 40`. Offline/CI: `AI_PROVIDER=mock` (keyword-based, labelled `mock`, not AI).

Rules the API follows: numbers are computed from stored records, never from model text; averages are mean individual ratings (Booking's own score is separate); weeks and dates use each property's timezone; the current week is compared with the same elapsed time of the previous week; sample-size states `insufficient_data` / `low_sample` / `ok`; model confidence is model-reported, not calibrated; trends are associations, not causes.
