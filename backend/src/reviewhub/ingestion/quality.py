"""Data-quality checks raised during ingestion (proposal §6.4).

``error`` rows are rejected and never loaded. ``warning`` and ``error`` issues are
stored in ``data_quality_issue``; ``info`` findings are only counted in the run
report (storing one row per rating-only review would bury the real problems).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class Check:
    code: str
    severity: Severity
    title: str


CHECKS: dict[str, Check] = {
    c.code: c
    for c in (
        Check("DQ001", "info", "No external review id: a content fingerprint is used instead"),
        Check("DQ002", "error", "Rating missing, not numeric, or outside 1-10"),
        Check("DQ003", "error", "Publication date in the future or before 2010"),
        Check("DQ004", "info", "No review text at all (rating only)"),
        Check(
            "DQ005",
            "warning",
            "Dates are inconsistent (published before check-out, or check-in after check-out)",
        ),
        Check(
            "DQ006", "warning", "Possible duplicate: identical text and rating under a different id"
        ),
        Check("DQ007", "error", "Row is not valid (missing or unreadable required field)"),
        Check("DQ012", "error", "Unknown or missing property"),
        Check(
            "DQ016",
            "info",
            "Publication time is imprecise (date only, or no timezone): property-local time assumed",
        ),
        Check("DQ017", "warning", "Repeated review id in the same file: first occurrence kept"),
        Check("DQ019", "info", "Optional value not understood and ignored"),
    )
}


@dataclass(frozen=True)
class RowIssue:
    """A finding about one input row (line numbers count the header as line 1 for CSV)."""

    line: int
    code: str
    message: str
    column: str | None = None
    property_slug: str | None = None
    key: str | None = None  # identity override for issues not tied to a file line (e.g. DQ006)

    @property
    def severity(self) -> Severity:
        return CHECKS[self.code].severity

    @property
    def rejects_row(self) -> bool:
        return self.severity == "error"


def issue(
    line: int, code: str, message: str, column: str | None = None, property_slug: str | None = None
) -> RowIssue:
    if code not in CHECKS:
        raise KeyError(code)
    return RowIssue(
        line=line, code=code, message=message, column=column, property_slug=property_slug
    )
