"""Migration acceptance tests (run on SQLite and PostgreSQL)."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, select, text

from reviewhub.persistence import migrate, models
from reviewhub.persistence.base import Base
from reviewhub.persistence.migrate import MIGRATIONS_DIR
from reviewhub.persistence.session import create_session_factory

EXPECTED_TABLES = {
    "property",
    "run",
    "run_property",
    "review",
    "review_revision",
    "score_snapshot",
    "data_quality_issue",
    "review_analysis",
    "review_aspect",
}


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_single_migration_head() -> None:
    heads = ScriptDirectory(str(MIGRATIONS_DIR)).get_heads()
    assert len(heads) == 1, f"migration history has branched: {heads}"


def test_upgrade_creates_every_model_table(engine: Engine) -> None:
    assert _tables(engine) == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_upgrade_is_idempotent(db_url: str, engine: Engine) -> None:
    before = _tables(engine)
    migrate.upgrade(db_url)  # already at head: must be a no-op
    assert _tables(engine) == before
    assert migrate.current_revision(engine) == "0002"


def test_downgrade_to_base_then_upgrade_again(db_url: str, engine: Engine) -> None:
    migrate.downgrade(db_url, "base")
    assert _tables(engine) == set()
    assert migrate.current_revision(engine) is None
    migrate.upgrade(db_url)
    assert _tables(engine) == EXPECTED_TABLES
    assert migrate.current_revision(engine) == "0002"


def test_downgrade_one_step_removes_only_seed_rows(db_url: str, engine: Engine) -> None:
    migrate.downgrade(db_url, "0001")
    with create_session_factory(engine)() as s:
        assert s.scalars(select(models.Property.id)).all() == []
    assert _tables(engine) == EXPECTED_TABLES


def test_migrated_schema_matches_models(engine: Engine) -> None:
    """No drift: what the migrations build is exactly what the models describe."""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_four_properties_seeded(session) -> None:  # type: ignore[no-untyped-def]
    rows = session.execute(
        select(models.Property.slug, models.Property.name, models.Property.timezone).order_by(
            models.Property.slug
        )
    ).all()
    assert [(r.slug, r.name) for r in rows] == [
        ("chateau-de-venus", "Darling Harbour"),
        ("olympic-paddington", "Olympic Hotel Paddington"),
        ("venus-potts-point-sydney", "Potts Point"),
        ("venus-surry-hills", "Central Sydney"),
    ]
    assert {r.timezone for r in rows} == {"Australia/Sydney"}


def test_no_review_data_is_seeded(session) -> None:  # type: ignore[no-untyped-def]
    # Migrations carry reference data only; sample data must go through the import pipeline.
    assert session.scalar(select(text("count(*)")).select_from(models.Review)) == 0
