"""Contracts shared by adapters, the pipeline and the API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from .quality import RowIssue


class FileRejected(Exception):
    """The whole file is unusable. Nothing is loaded; the run is recorded as failed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ForbiddenSource(ValueError):
    """An adapter tried to record data under a source an import may not use."""


@dataclass(frozen=True)
class RawRow:
    line: int  # 1-based; for CSV the header is line 1
    data: dict[str, Any]


@dataclass
class ParsedFile:
    header: list[str]
    rows: list[RawRow]
    row_issues: list[RowIssue] = field(default_factory=list)  # malformed rows found while parsing


class FileAdapter(Protocol):
    """Turns file bytes into raw rows. Knows nothing about the database or business rules."""

    name: str
    source: str  # value recorded in review.source

    def parse(self, content: bytes, encoding: str | None) -> ParsedFile: ...


@dataclass(frozen=True)
class PropertyInfo:
    id: int
    slug: str
    name: str
    timezone: str


@dataclass(frozen=True)
class ReviewIn:
    """A validated, normalised review, identical whatever file format it came from."""

    line: int
    property: PropertyInfo
    external_id: str | None
    rating: float
    published_at: datetime  # UTC
    published_date_local: date
    published_week_local: date
    title: str | None
    positive_text: str | None
    negative_text: str | None
    negative_is_filler: bool
    language_source: str | None
    guest_country: str | None
    traveller_type: str | None
    room_type: str | None
    checkin_date: date | None
    checkout_date: date | None
    nights: int | None
    stay_status: str | None
    management_reply: str | None
    source_url: str | None
    dedupe_key: str
    content_hash: str
    search_text: str
    payload: dict[str, Any]  # PII-free record of what was mapped, for re-parsing


# ----------------------------------------------------------------------------- report
class IssueOut(BaseModel):
    line: int
    code: str
    severity: str
    message: str
    column: str | None = None
    property: str | None = None


class IgnoredColumn(BaseModel):
    name: str
    reason: str  # personal data | unrecognised | duplicate of <field>


class FileError(BaseModel):
    code: str
    message: str


class ImportReport(BaseModel):
    status: Literal["success", "partial", "failed"]
    dry_run: bool
    run_id: int | None
    file_name: str
    file_sha256: str
    file_format: str | None = None
    source: str | None = None
    rows_total: int = 0
    rows_valid: int = 0
    rows_rejected: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    duplicates_in_file: int = 0
    raced: int = Field(default=0, description="rows another import inserted first; reconciled")
    issue_counts: dict[str, int] = Field(default_factory=dict)
    issues: list[IssueOut] = Field(default_factory=list)
    issues_truncated: bool = False
    ignored_columns: list[IgnoredColumn] = Field(default_factory=list)
    per_property: dict[str, dict[str, int]] = Field(default_factory=dict)
    file_error: FileError | None = None
    dataset: dict[str, Any] = Field(default_factory=dict)
