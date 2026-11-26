"""Unit tests for the pieces of ingestion that need no database."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from reviewhub.core.timeutil import InvalidTimezone, local_dates, parse_instant, week_start
from reviewhub.ingestion.adapters.files import CsvAdapter, JsonAdapter, choose_adapter
from reviewhub.ingestion.columns import build_column_map
from reviewhub.ingestion.contracts import FileRejected, PropertyInfo, RawRow
from reviewhub.ingestion.dedupe import dedupe_key
from reviewhub.ingestion.normalise import NormaliseContext, normalise_row
from reviewhub.ingestion.text import clean_text, fit, is_filler

NOW = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
SYD = PropertyInfo(1, "chateau-de-venus", "Darling Harbour", "Australia/Sydney")
PER = PropertyInfo(2, "perth-test", "Perth Test", "Australia/Perth")


# ------------------------------------------------------------------ time (F3)
@pytest.mark.parametrize(
    ("utc", "tz", "day", "week"),
    [
        # DST starts Sun 4 Oct 2026 02:00 AEST: 00:30 that day is still +10
        ("2026-10-03T14:30:00+00:00", "Australia/Sydney", date(2026, 10, 4), date(2026, 9, 28)),
        ("2026-10-04T13:30:00+00:00", "Australia/Sydney", date(2026, 10, 5), date(2026, 10, 5)),
        # DST ends Sun 5 Apr 2026
        ("2026-04-04T13:30:00+00:00", "Australia/Sydney", date(2026, 4, 5), date(2026, 3, 30)),
        # still Sunday in UTC, already Monday in Sydney
        ("2026-11-22T13:30:00+00:00", "Australia/Sydney", date(2026, 11, 23), date(2026, 11, 23)),
        # same instant in Perth (UTC+8, no DST): still Sunday, previous week
        ("2026-11-22T13:30:00+00:00", "Australia/Perth", date(2026, 11, 22), date(2026, 11, 16)),
        # Sunday 23:59 local is the last minute of the week; Monday 00:00 starts the next
        ("2026-11-22T12:59:00+00:00", "Australia/Sydney", date(2026, 11, 22), date(2026, 11, 16)),
        ("2026-11-22T13:00:00+00:00", "Australia/Sydney", date(2026, 11, 23), date(2026, 11, 23)),
    ],
)
def test_local_date_and_week_use_the_given_zone(utc: str, tz: str, day: date, week: date) -> None:
    assert local_dates(datetime.fromisoformat(utc), tz) == (day, week)


def test_local_dates_refuse_naive_datetimes_and_bad_zones() -> None:
    with pytest.raises(ValueError):
        local_dates(datetime(2026, 1, 1), "Australia/Sydney")
    with pytest.raises(InvalidTimezone):
        local_dates(NOW, "Not/AZone")


def test_week_start_is_monday() -> None:
    assert week_start(date(2026, 11, 22)) == date(2026, 11, 16)  # Sunday
    assert week_start(date(2026, 11, 23)) == date(2026, 11, 23)  # Monday


@pytest.mark.parametrize(
    ("value", "tz", "expected_utc", "precision"),
    [
        ("2026-11-23T14:30:00+11:00", "Australia/Perth", "2026-11-23T03:30:00+00:00", "instant"),
        ("2026-11-23T03:30:00Z", "Australia/Sydney", "2026-11-23T03:30:00+00:00", "instant"),
        ("2026-11-23 14:30", "Australia/Sydney", "2026-11-23T03:30:00+00:00", "local"),
        ("2026-11-23 14:30", "Australia/Perth", "2026-11-23T06:30:00+00:00", "local"),
        ("2026-11-23", "Australia/Sydney", "2026-11-22T13:00:00+00:00", "date"),
    ],
)
def test_parse_instant(value: str, tz: str, expected_utc: str, precision: str) -> None:
    instant, kind = parse_instant(value, tz)
    assert instant == datetime.fromisoformat(expected_utc) and kind == precision


def test_ambiguous_dates_need_an_explicit_format() -> None:
    with pytest.raises(ValueError):
        parse_instant("03/04/2026", "Australia/Sydney")  # never guessed
    instant, kind = parse_instant("03/04/2026", "Australia/Sydney", "%d/%m/%Y")
    assert kind == "date" and local_dates(instant, "Australia/Sydney")[0] == date(2026, 4, 3)


# ------------------------------------------------------------------ text
def test_clean_text_removes_nul_and_unescapes() -> None:
    assert clean_text("It&#39;s fine\x00 ok  ") == "It's fine ok"
    assert clean_text("   ") is None and clean_text(None) is None


@pytest.mark.parametrize(
    "text",
    ["Nothing", "nothing.", "N/A", "-", "None", "No complaints", "Nothing, the staff were lovely."],
)
def test_filler_negatives_are_recognised(text: str) -> None:
    assert is_filler(text)


@pytest.mark.parametrize(
    "text",
    [
        "The shower was cold",
        "Nothing worked, the lift was broken",
        "Noisy at night but staff were lovely",
    ],
)
def test_real_complaints_are_not_filler(text: str) -> None:
    assert not is_filler(text)


def test_fit_truncates() -> None:
    assert fit("x" * 300, 64) == "x" * 64 and fit(None, 5) is None and fit("ab", 5) == "ab"


# ------------------------------------------------------------------ identity
def key(**kw: object) -> str:
    base: dict[str, object] = dict(
        namespace="booking",
        external_id=None,
        property_id=1,
        raw_published="2026-11-20",
        rating=8.0,
        positive="Good",
        negative=None,
        title=None,
    )
    base.update(kw)
    return dedupe_key(**base)  # type: ignore[arg-type]


def test_external_id_key_and_length_limit() -> None:
    assert key(external_id="abc123") == "ext:booking:abc123"
    long = key(external_id="x" * 90)
    assert len(long) <= 80 and long.startswith("ext:booking:h:")
    assert key(external_id="x" * 90) == long  # deterministic


def test_fingerprint_is_deterministic_and_whitespace_insensitive() -> None:
    a = key(positive="Great   location ", negative="Noisy")
    assert a == key(positive="great location", negative=" noisy ")
    assert a != key(positive="great location", negative="noisy", rating=7.0)
    assert a != key(positive="great location", negative="noisy", property_id=2)
    assert a.startswith("fp:")


# ------------------------------------------------------------------ columns
def test_columns_map_aliases_and_flag_personal_data() -> None:
    cm = build_column_map(
        [
            "Review ID",
            "Hotel",
            "Score",
            "Review Date",
            "Liked",
            "Reviewer Name",
            "E-mail",
            "Avatar URL",
            "Mystery",
        ]
    )
    assert cm.mapping == {
        "external_id": "Review ID",
        "property": "Hotel",
        "rating": "Score",
        "published": "Review Date",
        "positive": "Liked",
    }
    reasons = dict(cm.ignored)
    assert reasons["Reviewer Name"] == reasons["E-mail"] == reasons["Avatar URL"] == "personal data"
    assert reasons["Mystery"] == "unrecognised"


def test_an_import_cannot_choose_its_own_source() -> None:
    cm = build_column_map(["rating", "review_date", "source"])
    assert "source" not in cm.mapping and dict(cm.ignored)["source"] == "unrecognised"


# ------------------------------------------------------------------ adapters
def test_csv_delimiters_bom_and_blank_lines() -> None:
    for delim in (",", ";", "\t"):
        text = f"rating{delim}review_date\n8{delim}2026-11-20\n\n7{delim}2026-11-21\n"
        parsed = CsvAdapter().parse(b"\xef\xbb\xbf" + text.encode(), None)
        assert [r.data["rating"] for r in parsed.rows] == ["8", "7"] and [
            r.line for r in parsed.rows
        ] == [2, 4]


def test_csv_extra_unquoted_delimiter_flags_the_row_only() -> None:
    parsed = CsvAdapter().parse(
        b"rating,review_date,title\n8,2026-11-20,ok\n8,2026-11-20,a,b\n", None
    )
    assert len(parsed.rows) == 1 and [(i.line, i.code) for i in parsed.row_issues] == [(3, "DQ007")]


@pytest.mark.parametrize(
    ("content", "encoding", "code"),
    [
        ("café,x\n1,2\n".encode("cp1252"), None, "bad_encoding"),
        (b"a,b\n1,2\n", "no-such-codec", "bad_encoding"),
        (b"", None, "empty"),
        (b"rating,rating\n1,2\n", None, "bad_header"),
        (b"rating,\n1,2\n", None, "bad_header"),
    ],
)
def test_csv_file_level_rejections(content: bytes, encoding: str | None, code: str) -> None:
    with pytest.raises(FileRejected) as err:
        CsvAdapter().parse(content, encoding)
    assert err.value.code == code


def test_csv_accepts_a_stated_legacy_encoding() -> None:
    parsed = CsvAdapter().parse("title\ncafé\n".encode("cp1252"), "cp1252")
    assert parsed.rows[0].data["title"] == "café"


def test_json_shapes() -> None:
    assert len(JsonAdapter().parse(b'[{"rating": 8}, {"rating": 7}]', None).rows) == 2
    assert len(JsonAdapter().parse(b'{"reviews": [{"rating": 8}]}', None).rows) == 1
    jl = JsonAdapter().parse(b'{"rating": 8}\n{bad json}\n{"rating": 6}\n', None)
    assert [r.line for r in jl.rows] == [1, 3] and [(i.line, i.code) for i in jl.row_issues] == [
        (2, "DQ007")
    ]
    mixed = JsonAdapter().parse(b'[{"rating": 8}, 5, "x"]', None)
    assert len(mixed.rows) == 1 and len(mixed.row_issues) == 2


@pytest.mark.parametrize("content", [b"[1, 2", b"not json at all", b""])
def test_json_file_level_rejections(content: bytes) -> None:
    with pytest.raises(FileRejected):
        JsonAdapter().parse(content, None)


def test_adapter_selection() -> None:
    assert choose_adapter("x.json", b"", "auto").name == "json"
    assert choose_adapter("x.csv", b"", "auto").name == "csv"
    assert choose_adapter("export", b' [{"a":1}]', "auto").name == "json"
    assert choose_adapter("export", b"a,b", "auto").name == "csv"
    assert choose_adapter("x.csv", b"", "json").name == "json"
    with pytest.raises(FileRejected):
        choose_adapter("x", b"", "xml")


# ------------------------------------------------------------------ normalisation / validation
def ctx(default: PropertyInfo | None = None, **kw: object) -> NormaliseContext:
    props = {p.slug: p for p in (SYD, PER)} | {p.name.lower(): p for p in (SYD, PER)}
    return NormaliseContext(props, default, kw.get("date_format"), "booking", NOW)  # type: ignore[arg-type]


COLS = build_column_map(
    [
        "review_id",
        "property",
        "rating",
        "review_date",
        "title",
        "liked",
        "disliked",
        "country",
        "nights",
        "checkin_date",
        "checkout_date",
        "source_url",
        "language",
    ]
)


def norm(data: dict[str, object], **kw: object):  # type: ignore[no-untyped-def]
    base = {
        "review_id": "a1",
        "property": "chateau-de-venus",
        "rating": "8",
        "review_date": "2026-11-20",
    }
    base.update(data)
    return normalise_row(RawRow(7, base), COLS, ctx(**kw))  # type: ignore[arg-type]


def codes(issues: list) -> list[tuple[str, str | None]]:  # type: ignore[type-arg]
    return [(i.code, i.column) for i in issues]


def test_a_valid_row_is_normalised() -> None:
    rin, issues = norm(
        {"disliked": "Nothing", "liked": "Great &amp; quiet", "language": "XU", "country": "au"}
    )
    assert rin is not None and not [i for i in issues if i.rejects_row]
    assert rin.rating == 8.0 and rin.positive_text == "Great & quiet"
    assert (
        rin.negative_is_filler is True and rin.language_source == "xu" and rin.guest_country == "AU"
    )
    assert rin.dedupe_key == "ext:booking:a1" and rin.property == SYD
    assert rin.payload["line"] == 7 and "property" in rin.payload["fields"]


@pytest.mark.parametrize(
    ("data", "code", "column"),
    [
        ({"rating": ""}, "DQ002", "rating"),
        ({"rating": "abc"}, "DQ002", "rating"),
        ({"rating": "0"}, "DQ002", "rating"),
        ({"rating": "11"}, "DQ002", "rating"),
        ({"rating": "nan"}, "DQ002", "rating"),
        ({"review_date": ""}, "DQ007", "review_date"),
        ({"review_date": "20/11/2026"}, "DQ007", "review_date"),
        ({"review_date": "2027-01-01"}, "DQ003", "review_date"),
        ({"review_date": "2009-12-31"}, "DQ003", "review_date"),
        ({"property": "Nowhere Inn"}, "DQ012", "property"),
    ],
)
def test_invalid_rows_are_rejected_with_code_and_column(
    data: dict[str, object], code: str, column: str
) -> None:
    rin, issues = norm(data)
    assert rin is None
    assert (code, column) in codes([i for i in issues if i.rejects_row])
    assert all(i.line == 7 for i in issues)


def test_property_default_and_lookup_by_name_or_slug() -> None:
    assert norm({"property": "Darling Harbour"})[0].property == SYD  # type: ignore[union-attr]
    assert norm({"property": ""}, default=PER)[0].property == PER  # type: ignore[union-attr]
    assert norm({"property": ""})[0] is None


def test_rating_is_rounded_to_the_stored_precision_and_accepts_decimal_comma() -> None:
    assert norm({"rating": "7,5"})[0].rating == 7.5  # type: ignore[union-attr]
    assert norm({"rating": "7.26"})[0].rating == 7.3  # type: ignore[union-attr]


def test_bad_optional_values_are_dropped_not_fatal() -> None:
    rin, issues = norm(
        {
            "country": "Australia",
            "nights": "lots",
            "source_url": "javascript:x",
            "checkin_date": "tomorrow",
        }
    )
    assert rin is not None
    assert (rin.guest_country, rin.nights, rin.source_url, rin.checkin_date) == (
        None,
        None,
        None,
        None,
    )
    assert [c for c, _ in codes(issues)].count("DQ019") == 4


def test_informational_and_warning_findings() -> None:
    rin, issues = norm({"review_id": "", "liked": "", "checkout_date": "2026-11-25"})
    assert rin is not None and rin.dedupe_key.startswith("fp:")
    found = {c for c, _ in codes(issues)}
    assert {"DQ001", "DQ004", "DQ016", "DQ005"} <= found
    assert {i.code: i.severity for i in issues}["DQ005"] == "warning"


def test_precision_finding_only_for_imprecise_times() -> None:
    assert "DQ016" not in {i.code for i in norm({"review_date": "2026-11-20T09:00:00+11:00"})[1]}
    assert "DQ016" in {i.code for i in norm({"review_date": "2026-11-20"})[1]}


def test_local_dates_follow_the_rows_property() -> None:
    instant = "2026-11-22T13:30:00Z"
    syd = norm({"review_date": instant, "property": "chateau-de-venus"})[0]
    per = norm({"review_date": instant, "property": "perth-test"})[0]
    assert (syd.published_date_local, syd.published_week_local) == (
        date(2026, 11, 23),
        date(2026, 11, 23),
    )  # type: ignore[union-attr]
    assert (per.published_date_local, per.published_week_local) == (
        date(2026, 11, 22),
        date(2026, 11, 16),
    )  # type: ignore[union-attr]


def test_invalid_property_timezone_rejects_the_whole_file() -> None:
    bad = PropertyInfo(3, "bad", "Bad Zone", "Mars/Olympus")
    with pytest.raises(FileRejected) as err:
        normalise_row(
            RawRow(2, {"rating": "8", "review_date": "2026-11-20"}), COLS, ctx(default=bad)
        )
    assert err.value.code == "invalid_property_timezone"


def test_future_tolerance_is_one_day() -> None:
    assert norm({"review_date": (NOW + timedelta(hours=20)).isoformat()})[0] is not None
    assert norm({"review_date": (NOW + timedelta(days=2)).isoformat()})[0] is None
