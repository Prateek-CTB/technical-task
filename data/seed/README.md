# Sample review data

`sample_reviews.json`: **973 genuine guest reviews** from the four public Booking.com property pages.

| | |
|---|---|
| Collected | 2026-11-26, by this project's collector (`reviewhub collect --headed --max-pages 30 --full`): an unmodified, visible Chromium browser, reading "Newest first", paced 2–4 s per page |
| Per property | Olympic Hotel Paddington 74 · Potts Point 300 · Central Sydney 299 · Darling Harbour 300 |
| Date range | 13 May 2025 – 25 Nov 2026 (publication dates, Australia/Sydney) |
| Fields | `property`, `rating` (1–10), `review_date`, `title`, `liked`, `disliked`, `traveller_type`, `room_type`, `nights` |
| Not included | reviewer names, avatars, countries or any other personal identifiers (never collected) |
| Text | verbatim, in the guest's original language. Guests' own free text can mention names (e.g. staff); this is public review text and is kept as written |

Load it with `reviewhub sample load`. It is recorded as `booking_public` (its true origin) through the normal import pipeline, so validation and de-duplication apply and re-loading adds nothing. `reviewhub sample export` regenerates the file from a database.

These are real reviews, not synthetic. The fictional demo dataset is generated separately (`reviewhub seed-demo`), labelled `demo_synthetic`, and is never mixed with this data.
