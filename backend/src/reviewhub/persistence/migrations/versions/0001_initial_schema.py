"""Initial schema (hand-reviewed; see docs/ENGINEERING_AUDIT.md section 7, ADR 0011, ADR 0012).

This migration is frozen: it must not import application code.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('property',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('booking_url', sa.String(length=500), nullable=True),
    sa.Column('booking_hotel_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_property')),
    sa.UniqueConstraint('booking_hotel_id', name=op.f('uq_property_booking_hotel_id')),
    sa.UniqueConstraint('slug', name=op.f('uq_property_slug'))
    )
    op.create_table('run',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('scope_key', sa.String(length=200), nullable=False),
    sa.Column('mode', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('trigger', sa.String(length=16), nullable=False),
    sa.Column('version', sa.String(length=100), nullable=True),
    sa.Column('params', sa.JSON(), nullable=True),
    sa.Column('file_sha256', sa.String(length=64), nullable=True),
    sa.Column('items_total', sa.Integer(), nullable=True),
    sa.Column('items_ok', sa.Integer(), nullable=True),
    sa.Column('items_failed', sa.Integer(), nullable=True),
    sa.Column('input_tokens', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('output_tokens', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=10, scale=4, asdecimal=False), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('collect', 'import', 'analyse')", name=op.f('ck_run_kind')),
    sa.CheckConstraint("status IN ('running', 'success', 'partial', 'failed', 'blocked', 'abandoned')", name=op.f('ck_run_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_run'))
    )
    op.create_index('ix_run_started_at', 'run', ['started_at'], unique=False)
    op.create_index('ix_run_status', 'run', ['status'], unique=False)
    op.create_index('ux_run_scope_running', 'run', ['scope_key'], unique=True, sqlite_where=sa.text("status = 'running'"), postgresql_where=sa.text("status = 'running'"))
    op.create_table('review',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('property_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('dedupe_key', sa.String(length=80), nullable=False),
    sa.Column('external_id', sa.String(length=100), nullable=True),
    sa.Column('source_url', sa.String(length=500), nullable=True),
    sa.Column('rating', sa.Numeric(precision=3, scale=1, asdecimal=False), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('published_date_local', sa.Date(), nullable=False),
    sa.Column('published_week_local', sa.Date(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('positive_text', sa.Text(), nullable=True),
    sa.Column('negative_text', sa.Text(), nullable=True),
    sa.Column('negative_is_filler', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('search_text', sa.Text(), server_default='', nullable=False),
    sa.Column('language_source', sa.String(length=8), nullable=True),
    sa.Column('language', sa.String(length=8), nullable=True),
    sa.Column('language_method', sa.String(length=32), nullable=True),
    sa.Column('guest_country', sa.String(length=2), nullable=True),
    sa.Column('traveller_type', sa.String(length=64), nullable=True),
    sa.Column('room_type', sa.String(length=200), nullable=True),
    sa.Column('checkin_date', sa.Date(), nullable=True),
    sa.Column('checkout_date', sa.Date(), nullable=True),
    sa.Column('nights', sa.Integer(), nullable=True),
    sa.Column('stay_status', sa.String(length=64), nullable=True),
    sa.Column('management_reply', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.String(length=16), nullable=False),
    sa.Column('source_payload', sa.JSON(), nullable=True),
    sa.Column('first_run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('last_run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("source IN ('booking_public', 'booking_experimental', 'csv_import', 'json_import', 'demo_synthetic')", name=op.f('ck_review_source')),
    sa.CheckConstraint('rating BETWEEN 1 AND 10', name=op.f('ck_review_rating_range')),
    sa.ForeignKeyConstraint(['first_run_id'], ['run.id'], name=op.f('fk_review_first_run_id_run'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['last_run_id'], ['run.id'], name=op.f('fk_review_last_run_id_run'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['property_id'], ['property.id'], name=op.f('fk_review_property_id_property')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review')),
    sa.UniqueConstraint('property_id', 'dedupe_key', name=op.f('uq_review_property_id_dedupe_key'))
    )
    op.create_index('ix_review_content_hash', 'review', ['content_hash'], unique=False)
    op.create_index('ix_review_property_id_published_at', 'review', ['property_id', 'published_at'], unique=False)
    op.create_index('ix_review_property_id_rating', 'review', ['property_id', 'rating'], unique=False)
    op.create_index('ix_review_published_week_local_property_id', 'review', ['published_week_local', 'property_id'], unique=False)
    op.create_table('run_property',
    sa.Column('run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('property_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('pages', sa.Integer(), server_default='0', nullable=False),
    sa.Column('received', sa.Integer(), server_default='0', nullable=False),
    sa.Column('new', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated', sa.Integer(), server_default='0', nullable=False),
    sa.Column('unchanged', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rejected', sa.Integer(), server_default='0', nullable=False),
    sa.Column('reported_list_total', sa.Integer(), nullable=True),
    sa.Column('reported_headline_total', sa.Integer(), nullable=True),
    sa.Column('next_offset', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_page_hash', sa.String(length=16), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('running', 'success', 'partial', 'failed', 'blocked')", name=op.f('ck_run_property_status')),
    sa.ForeignKeyConstraint(['property_id'], ['property.id'], name=op.f('fk_run_property_property_id_property')),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_run_property_run_id_run'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id', 'property_id', name=op.f('pk_run_property'))
    )
    op.create_index('ix_run_property_property_id_started_at', 'run_property', ['property_id', 'started_at'], unique=False)
    op.create_table('score_snapshot',
    sa.Column('property_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('metric', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('review_count', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['property_id'], ['property.id'], name=op.f('fk_score_snapshot_property_id_property')),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_score_snapshot_run_id_run'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('property_id', 'captured_at', 'metric', name=op.f('pk_score_snapshot'))
    )
    op.create_table('data_quality_issue',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('property_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('review_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.Column('fingerprint', sa.String(length=64), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.CheckConstraint("severity IN ('info', 'warning', 'error')", name=op.f('ck_data_quality_issue_severity')),
    sa.ForeignKeyConstraint(['property_id'], ['property.id'], name=op.f('fk_data_quality_issue_property_id_property')),
    sa.ForeignKeyConstraint(['review_id'], ['review.id'], name=op.f('fk_data_quality_issue_review_id_review'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_data_quality_issue_run_id_run'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_data_quality_issue'))
    )
    op.create_index('ix_data_quality_issue_code_resolved_at', 'data_quality_issue', ['code', 'resolved_at'], unique=False)
    op.create_index('ix_data_quality_issue_property_id', 'data_quality_issue', ['property_id'], unique=False)
    op.create_index('ux_data_quality_issue_open_fingerprint', 'data_quality_issue', ['fingerprint'], unique=True, sqlite_where=sa.text('resolved_at IS NULL'), postgresql_where=sa.text('resolved_at IS NULL'))
    op.create_table('review_analysis',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('review_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('analyzer', sa.String(length=16), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model_id', sa.String(length=100), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('taxonomy_version', sa.String(length=32), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('flags', sa.JSON(), nullable=True),
    sa.Column('overall_sentiment', sa.String(length=16), nullable=True),
    sa.Column('summary_en', sa.Text(), nullable=True),
    sa.Column('main_complaint', sa.Text(), nullable=True),
    sa.Column('main_complaint_topic', sa.String(length=32), nullable=True),
    sa.Column('suggested_action', sa.Text(), nullable=True),
    sa.Column('language', sa.String(length=8), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('off_taxonomy', sa.JSON(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=10, scale=6, asdecimal=False), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='1', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('is_current', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("analyzer <> 'lexicon' OR provider = 'builtin'", name=op.f('ck_review_analysis_lexicon_provider')),
    sa.CheckConstraint("analyzer IN ('llm', 'lexicon')", name=op.f('ck_review_analysis_analyzer')),
    sa.CheckConstraint("overall_sentiment IS NULL OR overall_sentiment IN ('positive', 'negative', 'neutral', 'mixed')", name=op.f('ck_review_analysis_overall_sentiment')),
    sa.CheckConstraint("status = 'failed' OR overall_sentiment IS NOT NULL", name=op.f('ck_review_analysis_sentiment_when_ok')),
    sa.CheckConstraint("status IN ('ok', 'needs_review', 'failed')", name=op.f('ck_review_analysis_status')),
    sa.CheckConstraint('confidence IS NULL OR confidence BETWEEN 0 AND 1', name=op.f('ck_review_analysis_confidence_range')),
    sa.CheckConstraint("NOT is_current OR status IN ('ok', 'needs_review')", name=op.f('ck_review_analysis_current_requires_usable')),
    sa.ForeignKeyConstraint(['review_id'], ['review.id'], name=op.f('fk_review_analysis_review_id_review'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_review_analysis_run_id_run'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_analysis')),
    sa.UniqueConstraint('id', 'review_id', name='uq_review_analysis_id_review_id'),
    sa.UniqueConstraint('review_id', 'analyzer', 'provider', 'model_id', 'prompt_version', 'taxonomy_version', 'input_hash', name='uq_review_analysis_cache_key')
    )
    op.create_index('ix_review_analysis_run_id', 'review_analysis', ['run_id'], unique=False)
    op.create_index('ix_review_analysis_status', 'review_analysis', ['status'], unique=False)
    op.create_index('ux_review_analysis_current', 'review_analysis', ['review_id'], unique=True, sqlite_where=sa.text('is_current'), postgresql_where=sa.text('is_current'))
    op.create_table('review_revision',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('review_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('run_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('previous_hash', sa.String(length=16), nullable=False),
    sa.Column('new_hash', sa.String(length=16), nullable=False),
    sa.Column('changed_fields', sa.JSON(), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['review_id'], ['review.id'], name=op.f('fk_review_revision_review_id_review'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['run.id'], name=op.f('fk_review_revision_run_id_run'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_revision'))
    )
    op.create_index('ix_review_revision_review_id_detected_at', 'review_revision', ['review_id', 'detected_at'], unique=False)
    op.create_table('review_aspect',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('analysis_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('review_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('topic', sa.String(length=32), nullable=False),
    sa.Column('sentiment', sa.String(length=16), nullable=False),
    sa.Column('certainty', sa.String(length=16), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('evidence', sa.JSON(), nullable=False),
    sa.CheckConstraint("sentiment IN ('positive', 'negative', 'neutral', 'mixed')", name=op.f('ck_review_aspect_sentiment')),
    sa.CheckConstraint("topic IN ('cleanliness', 'check_in', 'staff', 'noise', 'facilities', 'location', 'room_condition', 'value')", name=op.f('ck_review_aspect_topic')),
    sa.ForeignKeyConstraint(['analysis_id', 'review_id'], ['review_analysis.id', 'review_analysis.review_id'], name='fk_review_aspect_analysis_review', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_aspect')),
    sa.UniqueConstraint('analysis_id', 'topic', name=op.f('uq_review_aspect_analysis_id_topic'))
    )
    op.create_index('ix_review_aspect_topic_sentiment_review_id', 'review_aspect', ['topic', 'sentiment', 'review_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_review_aspect_topic_sentiment_review_id', table_name='review_aspect')
    op.drop_table('review_aspect')
    op.drop_index('ix_review_revision_review_id_detected_at', table_name='review_revision')
    op.drop_table('review_revision')
    op.drop_index('ux_review_analysis_current', table_name='review_analysis')
    op.drop_index('ix_review_analysis_status', table_name='review_analysis')
    op.drop_index('ix_review_analysis_run_id', table_name='review_analysis')
    op.drop_table('review_analysis')
    op.drop_index('ux_data_quality_issue_open_fingerprint', table_name='data_quality_issue')
    op.drop_index('ix_data_quality_issue_property_id', table_name='data_quality_issue')
    op.drop_index('ix_data_quality_issue_code_resolved_at', table_name='data_quality_issue')
    op.drop_table('data_quality_issue')
    op.drop_table('score_snapshot')
    op.drop_index('ix_run_property_property_id_started_at', table_name='run_property')
    op.drop_table('run_property')
    op.drop_index('ix_review_published_week_local_property_id', table_name='review')
    op.drop_index('ix_review_property_id_rating', table_name='review')
    op.drop_index('ix_review_property_id_published_at', table_name='review')
    op.drop_index('ix_review_content_hash', table_name='review')
    op.drop_table('review')
    op.drop_index('ux_run_scope_running', table_name='run')
    op.drop_index('ix_run_status', table_name='run')
    op.drop_index('ix_run_started_at', table_name='run')
    op.drop_table('run')
    op.drop_table('property')
