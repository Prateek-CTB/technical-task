"""CSV and JSON file adapters. They only parse: no business rules, no database."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from reviewhub.ingestion.columns import normalise_header
from reviewhub.ingestion.contracts import FileAdapter, FileRejected, ParsedFile, RawRow
from reviewhub.ingestion.quality import RowIssue, issue

MAX_ROWS = 200_000


def decode(content: bytes, encoding: str | None) -> str:
    """Decode to text. UTF-8 (with or without BOM) unless told otherwise; never guess."""
    try:
        text = content.decode(encoding or "utf-8-sig")
    except LookupError as exc:
        raise FileRejected("bad_encoding", f"Unknown encoding {encoding!r}.") from exc
    except UnicodeDecodeError as exc:
        raise FileRejected(
            "bad_encoding",
            "The file is not valid UTF-8. Re-save it as UTF-8 (Excel: 'CSV UTF-8'), "
            "or state the encoding, e.g. --encoding cp1252.",
        ) from exc
    return text.replace("\x00", "")  # PostgreSQL text cannot hold NUL


class CsvAdapter:
    name = "csv"
    source = "csv_import"

    def parse(self, content: bytes, encoding: str | None) -> ParsedFile:
        text = decode(content, encoding)
        first = next((ln for ln in text.splitlines() if ln.strip()), "")
        if not first:
            raise FileRejected("empty", "The file is empty.")
        delimiter = (
            max((",", ";", "\t"), key=first.count) if any(d in first for d in ",;\t") else ","
        )
        try:
            records = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
        except csv.Error as exc:
            raise FileRejected("bad_csv", f"The file is not valid CSV: {exc}") from exc

        header_idx = next(i for i, rec in enumerate(records) if any(c.strip() for c in rec))
        header = [c.strip() for c in records[header_idx]]
        if any(not h for h in header):
            raise FileRejected("bad_header", "The header row has an empty column name.")
        normalised = [normalise_header(h) for h in header]
        if len(set(normalised)) != len(normalised):
            raise FileRejected("bad_header", "The header row has duplicate column names.")
        if len(records) - header_idx - 1 > MAX_ROWS:
            raise FileRejected("too_many_rows", f"More than {MAX_ROWS} rows; split the file.")

        rows: list[RawRow] = []
        problems: list[RowIssue] = []
        for offset, rec in enumerate(records[header_idx + 1 :], start=header_idx + 2):
            if not any(c.strip() for c in rec):
                continue  # blank line
            extra = [c for c in rec[len(header) :] if c.strip()]
            if extra:
                problems.append(
                    issue(
                        offset,
                        "DQ007",
                        f"row has {len(rec)} values but the header has {len(header)} "
                        "(an unquoted delimiter inside a text field?)",
                    )
                )
                continue
            rows.append(RawRow(offset, dict(zip(header, rec, strict=False))))
        return ParsedFile(header=header, rows=rows, row_issues=problems)


class JsonAdapter:
    """A JSON array, ``{"reviews": [...]}``, or JSON Lines (one object per line)."""

    name = "json"
    source = "json_import"

    def parse(self, content: bytes, encoding: str | None) -> ParsedFile:
        text = decode(content, encoding).strip()
        if not text:
            raise FileRejected("empty", "The file is empty.")
        problems: list[RowIssue] = []
        items: list[tuple[int, Any]]
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as whole_error:
            lines = [(n, ln) for n, ln in enumerate(text.splitlines(), start=1) if ln.strip()]
            if len(lines) < 2:
                raise FileRejected(
                    "bad_json", f"The file is not valid JSON: {whole_error}"
                ) from whole_error
            items = []
            for n, ln in lines:
                try:
                    items.append((n, json.loads(ln)))
                except json.JSONDecodeError as exc:
                    problems.append(issue(n, "DQ007", f"line is not valid JSON: {exc.msg}"))
        else:
            if isinstance(doc, dict):
                inner = doc.get("data")
                if isinstance(inner, dict) and isinstance(inner.get("reviews"), list):
                    doc = inner["reviews"]  # Booking.com Guest Reviews API envelope
                else:
                    doc = next(
                        (
                            doc[k]
                            for k in ("reviews", "data", "items")
                            if isinstance(doc.get(k), list)
                        ),
                        [doc],
                    )
            if not isinstance(doc, list):
                raise FileRejected("bad_json", "Expected a JSON array of review objects.")
            items = list(enumerate(doc, start=1))
        if len(items) > MAX_ROWS:
            raise FileRejected("too_many_rows", f"More than {MAX_ROWS} records; split the file.")

        header: list[str] = []
        rows: list[RawRow] = []
        for line, obj in items:
            if not isinstance(obj, dict):
                problems.append(issue(line, "DQ007", "record is not a JSON object"))
                continue
            obj = flatten_booking_review(obj)
            for key in obj:
                if key not in header:
                    header.append(str(key))
            rows.append(RawRow(line, obj))
        return ParsedFile(header=header, rows=rows, row_issues=problems)


def flatten_booking_review(obj: dict[str, Any]) -> dict[str, Any]:
    """Map one review of Booking.com's official Guest Reviews API to the flat import columns.

    Reviewer names are never read. ``created_timestamp`` has no stated timezone, so it is read as
    property-local time (finding DQ016).
    """
    if not ("content" in obj or "scoring" in obj or "created_timestamp" in obj):
        return obj
    content = obj.get("content") or {}
    scoring = obj.get("scoring") or {}
    reply = obj.get("reply") or obj.get("response") or {}
    reviewer = obj.get("reviewer") or {}
    out: dict[str, Any] = {
        "review_id": obj.get("review_id"),
        "rating": scoring.get("review_score"),
        "review_date": obj.get("created_timestamp"),
        "language": content.get("language_code"),
        "title": content.get("headline"),
        "liked": content.get("positive"),
        "disliked": content.get("negative"),
        "country": reviewer.get("country_code"),
        "management_reply": reply.get("text") if isinstance(reply, dict) else None,
    }
    if "property" in obj:
        out["property"] = obj["property"]
    return out


def choose_adapter(filename: str, content: bytes, fmt: str = "auto") -> FileAdapter:
    if fmt == "csv":
        return CsvAdapter()
    if fmt == "json":
        return JsonAdapter()
    if fmt != "auto":
        raise FileRejected("bad_format", f"Unknown format {fmt!r}; use csv, json or auto.")
    lower = filename.lower()
    if lower.endswith((".json", ".jsonl", ".ndjson")):
        return JsonAdapter()
    if lower.endswith((".csv", ".tsv", ".txt")):
        return CsvAdapter()
    head = content.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    return JsonAdapter() if head in (b"[", b"{") else CsvAdapter()
