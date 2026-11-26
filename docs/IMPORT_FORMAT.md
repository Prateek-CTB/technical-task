# Review import format (CSV / JSON)

Import reviews exported from Booking.com's Extranet, a channel manager, or any tool:

```bash
reviewhub import reviews.csv --dry-run      # validate and report; writes nothing
reviewhub import reviews.csv                # load
reviewhub import export.json --property potts-point
```
or `POST /api/v1/ingestion/imports` (multipart: `file`, optional `property`, `dry_run`, `date_format`, `encoding`, `id_namespace`, `format`).

Exit codes: `0` success, `1` failed, `3` partial (some rows rejected), `4` same file already importing, `5` refused to mix dataset kinds.

## Files
* **CSV**: UTF-8 (BOM accepted). Delimiter `,` `;` or tab is detected. Excel: *Save as "CSV UTF-8"*. For old exports use `--encoding cp1252`. Max 20 MB.
* **JSON**: an array of objects, `{"reviews": [...]}`, or JSON Lines (one object per line; a broken line rejects only that line).
* Re-importing a file, or an overlapping/updated export, is safe: reviews are matched by id (see below).

## Columns
Header names are matched ignoring case and punctuation. First match in the list wins.

| Field | Accepted names | Required |
|---|---|---|
| property | property, property_slug, property_name, hotel, hotel_name | yes, unless `--property` is given. A slug or the name used by the team |
| rating | rating, score, review_score, guest_rating | **yes**, a number 1-10 (`7,5` accepted) |
| published | published_at, review_date, published, date, reviewed_at, reviewed, publication_date, created_at | **yes**, ISO 8601 |
| id | review_id, external_id, id, booking_review_id | no (recommended). Without it a content fingerprint is used |
| title | title, review_title, headline | no |
| liked | positive, positive_text, liked, pros, positive_review | no |
| disliked | negative, negative_text, disliked, cons, negative_review | no |
| language | language, language_code, lang | no (stored as the source's tag; real language is determined later) |
| country | guest_country, country, country_code | no, 2-letter code |
| traveller type | traveller_type, traveler_type, guest_type, travel_type, trip_type | no |
| room | room_type, room_name, room | no |
| check-in / out | checkin_date, check_in_date, checkin... / checkout_date... | no |
| nights | nights, number_of_nights, length_of_stay | no |
| stay status | stay_status | no |
| reply | management_reply, property_reply, partner_reply, reply, hotel_response, response | no |
| url | source_url, review_url, url, link | no, http(s) |

**Ignored columns** are listed in the report. Anything that looks like personal data (reviewer or guest names, e-mail, phone, avatar, photo...) is never read or stored. A `source` column is ignored: a file cannot declare itself demo or experimental data.

## Dates
ISO 8601 only: `2026-11-23`, `2026-11-23 14:30`, `2026-11-23T14:30:00+11:00`, `...Z`.
* With an offset/`Z`: exact. Without: the **property's local time**. Date only: local midnight (finding DQ016).
* Other layouts (e.g. `23/11/2026`) are never guessed; use `--date-format "%d/%m/%Y"`.
* A date in the future (more than a day) or before 2010 is rejected.

## What happens to each row
| Finding | Meaning | Effect |
|---|---|---|
| DQ002 rating invalid · DQ003 date out of range · DQ007 row/date unreadable · DQ012 unknown property | error | row **rejected**, listed with line and column |
| DQ005 dates inconsistent · DQ006 same text under another id · DQ017 id repeated in file | warning | loaded (DQ017: first kept), stored for review |
| DQ001 no id · DQ004 no text · DQ016 imprecise time · DQ019 optional value ignored | info | loaded, counted in the report |

A file that cannot be used at all (unreadable encoding, missing required columns, empty, invalid JSON, unknown `--property`, ...) loads **nothing** and records one *failed* run for audit.

## Identity and updates
`ext:<namespace>:<id>` per property (namespace default `booking`, `--id-namespace` to change). If a re-imported review differs in any content field, it is updated and one *revision* records which fields changed (e.g. a management reply added later). Without an id, an edited review cannot be recognised and is added as a new one.
