"""Relational model. See docs/ENGINEERING_AUDIT.md §7 and ADRs 0011/0012 for the rationale."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    ANALYSIS_STATUSES,
    ANALYZERS,
    RUN_KINDS,
    RUN_PROPERTY_STATUSES,
    RUN_STATUSES,
    SENTIMENTS,
    SEVERITIES,
    SOURCES,
    TOPIC_KEYS,
    Base,
    BigIntPK,
    UTCDateTime,
    in_list,
)


class Property(Base):
    __tablename__ = "property"
    __table_args__ = (UniqueConstraint("slug"), UniqueConstraint("booking_hotel_id"))

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    booking_url: Mapped[str | None] = mapped_column(String(500))
    booking_hotel_id: Mapped[int | None] = mapped_column(BigIntPK)
    timezone: Mapped[str] = mapped_column(String(64), default="Australia/Sydney")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class Run(Base):
    """One collection, import or analysis run (single history list for the UI)."""

    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(in_list("kind", RUN_KINDS), name="kind"),
        CheckConstraint(in_list("status", RUN_STATUSES), name="status"),
        # Single-flight (ADR 0012): at most one running run per scope key.
        Index(
            "ux_run_scope_running",
            "scope_key",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
        Index("ix_run_started_at", "started_at"),
        Index("ix_run_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    scope_key: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    trigger: Mapped[str] = mapped_column(String(16), default="cli")
    version: Mapped[str | None] = mapped_column(String(100))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    items_total: Mapped[int | None] = mapped_column(Integer)
    items_ok: Mapped[int | None] = mapped_column(Integer)
    items_failed: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(BigIntPK)
    output_tokens: Mapped[int | None] = mapped_column(BigIntPK)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4, asdecimal=False))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RunProperty(Base):
    __tablename__ = "run_property"
    __table_args__ = (
        CheckConstraint(in_list("status", RUN_PROPERTY_STATUSES), name="status"),
        Index("ix_run_property_property_id_started_at", "property_id", "started_at"),
    )

    run_id: Mapped[int] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    pages: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    received: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    new: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    unchanged: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rejected: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reported_list_total: Mapped[int | None] = mapped_column(Integer)
    reported_headline_total: Mapped[int | None] = mapped_column(Integer)
    next_offset: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_page_hash: Mapped[str | None] = mapped_column(String(16))
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Review(Base):
    __tablename__ = "review"
    __table_args__ = (
        UniqueConstraint("property_id", "dedupe_key"),
        CheckConstraint("rating BETWEEN 1 AND 10", name="rating_range"),
        CheckConstraint(in_list("source", SOURCES), name="source"),
        Index("ix_review_property_id_published_at", "property_id", "published_at"),
        Index("ix_review_published_week_local_property_id", "published_week_local", "property_id"),
        Index("ix_review_content_hash", "content_hash"),
        Index("ix_review_property_id_rating", "property_id", "rating"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"))
    source: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(80))
    external_id: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(500))
    rating: Mapped[float] = mapped_column(Numeric(3, 1, asdecimal=False))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime)
    published_date_local: Mapped[date] = mapped_column(Date)
    published_week_local: Mapped[date] = mapped_column(Date)
    title: Mapped[str | None] = mapped_column(Text)
    positive_text: Mapped[str | None] = mapped_column(Text)
    negative_text: Mapped[str | None] = mapped_column(Text)
    negative_is_filler: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    search_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    language_source: Mapped[str | None] = mapped_column(String(8))
    language: Mapped[str | None] = mapped_column(String(8))
    language_method: Mapped[str | None] = mapped_column(String(32))
    guest_country: Mapped[str | None] = mapped_column(String(2))
    traveller_type: Mapped[str | None] = mapped_column(String(64))
    room_type: Mapped[str | None] = mapped_column(String(200))
    checkin_date: Mapped[date | None] = mapped_column(Date)
    checkout_date: Mapped[date | None] = mapped_column(Date)
    nights: Mapped[int | None] = mapped_column(Integer)
    stay_status: Mapped[str | None] = mapped_column(String(64))
    management_reply: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(16))
    source_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # PII-stripped
    first_run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))
    collected_at: Mapped[datetime] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ReviewRevision(Base):
    __tablename__ = "review_revision"
    __table_args__ = (
        Index("ix_review_revision_review_id_detected_at", "review_id", "detected_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("review.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))
    previous_hash: Mapped[str] = mapped_column(String(16))
    new_hash: Mapped[str] = mapped_column(String(16))
    changed_fields: Mapped[dict[str, Any] | list[Any]] = mapped_column(JSON)
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ScoreSnapshot(Base):
    """Booking.com's own aggregate scores, kept separate from our review-level averages."""

    __tablename__ = "score_snapshot"

    property_id: Mapped[int] = mapped_column(ForeignKey("property.id"), primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))
    value: Mapped[float] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issue"
    __table_args__ = (
        CheckConstraint(in_list("severity", SEVERITIES), name="severity"),
        Index(
            "ux_data_quality_issue_open_fingerprint",
            "fingerprint",
            unique=True,
            sqlite_where=text("resolved_at IS NULL"),
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_data_quality_issue_code_resolved_at", "code", "resolved_at"),
        Index("ix_data_quality_issue_property_id", "property_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    code: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))
    property_id: Mapped[int | None] = mapped_column(ForeignKey("property.id"))
    review_id: Mapped[int | None] = mapped_column(ForeignKey("review.id", ondelete="CASCADE"))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class ReviewAnalysis(Base):
    """One analysis of one review by one analyzer configuration (ADR 0011).

    Every column in the cache key is NOT NULL: NULLs are distinct in unique
    indexes on both PostgreSQL and SQLite, so a nullable key column would let
    duplicates through silently.
    """

    __tablename__ = "review_analysis"
    __table_args__ = (
        UniqueConstraint(
            "review_id",
            "analyzer",
            "provider",
            "model_id",
            "prompt_version",
            "taxonomy_version",
            "input_hash",
            name="uq_review_analysis_cache_key",
        ),
        # Target of review_aspect's composite foreign key.
        UniqueConstraint("id", "review_id", name="uq_review_analysis_id_review_id"),
        CheckConstraint(in_list("analyzer", ANALYZERS), name="analyzer"),
        CheckConstraint(in_list("status", ANALYSIS_STATUSES), name="status"),
        CheckConstraint(
            "overall_sentiment IS NULL OR " + in_list("overall_sentiment", SENTIMENTS),
            name="overall_sentiment",
        ),
        CheckConstraint("analyzer <> 'lexicon' OR provider = 'builtin'", name="lexicon_provider"),
        CheckConstraint(
            "status = 'failed' OR overall_sentiment IS NOT NULL", name="sentiment_when_ok"
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"
        ),
        # A failed analysis can never be the one used for reporting, so a failed retry
        # cannot displace a good analysis. "needs_review" is complete but flagged.
        CheckConstraint(
            "NOT is_current OR status IN ('ok', 'needs_review')", name="current_requires_usable"
        ),
        # At most one analysis per review is used for reporting.
        Index(
            "ux_review_analysis_current",
            "review_id",
            unique=True,
            sqlite_where=text("is_current"),
            postgresql_where=text("is_current"),
        ),
        Index("ix_review_analysis_status", "status"),
        Index("ix_review_analysis_run_id", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("review.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id", ondelete="SET NULL"))
    analyzer: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32))
    taxonomy_version: Mapped[str] = mapped_column(String(32))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    flags: Mapped[list[Any] | None] = mapped_column(JSON)
    overall_sentiment: Mapped[str | None] = mapped_column(String(16))
    summary_en: Mapped[str | None] = mapped_column(Text)
    main_complaint: Mapped[str | None] = mapped_column(Text)
    main_complaint_topic: Mapped[str | None] = mapped_column(String(32))
    suggested_action: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(8))
    confidence: Mapped[float | None] = mapped_column(Float)
    off_taxonomy: Mapped[list[Any] | None] = mapped_column(JSON)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6, asdecimal=False))
    attempts: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    error: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ReviewAspect(Base):
    __tablename__ = "review_aspect"
    __table_args__ = (
        # (analysis_id, review_id) must exist together in review_analysis, so the
        # denormalised review_id can never disagree with the parent analysis.
        ForeignKeyConstraint(
            ["analysis_id", "review_id"],
            ["review_analysis.id", "review_analysis.review_id"],
            name="fk_review_aspect_analysis_review",
            ondelete="CASCADE",
        ),
        UniqueConstraint("analysis_id", "topic"),
        CheckConstraint(in_list("topic", TOPIC_KEYS), name="topic"),
        CheckConstraint(in_list("sentiment", SENTIMENTS), name="sentiment"),
        Index("ix_review_aspect_topic_sentiment_review_id", "topic", "sentiment", "review_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(BigIntPK)
    review_id: Mapped[int] = mapped_column(BigIntPK)
    topic: Mapped[str] = mapped_column(String(32))
    sentiment: Mapped[str] = mapped_column(String(16))
    certainty: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[list[Any]] = mapped_column(JSON)
