"""Structural proof that the migrations build exactly what the models describe.

Alembic's ``compare_metadata`` (used in test_migrations) does not look at CHECK
constraints, partial-index predicates, server defaults or foreign-key actions: a
migration that silently dropped the predicate of a partial unique index passes
it. These tests reflect the real database instead, on both engines:

1. the **migrated** database and a database built straight from the **models**
   must have identical reflected structure;
2. specific, security-relevant constraints are asserted by name (so the check
   does not just compare two things that could be wrong in the same way);
3. a negative control proves the comparison can actually fail.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, Engine, ForeignKeyConstraint, inspect, text

from reviewhub.persistence import migrate, models  # noqa: F401  (registers tables)
from reviewhub.persistence.base import Base
from reviewhub.persistence.session import create_db_engine

#: partial unique indexes whose predicate is what makes them correct
PARTIAL_INDEXES = {
    "ux_run_scope_running": "running",
    "ux_data_quality_issue_open_fingerprint": "resolved_at",
    "ux_review_analysis_current": "is_current",
}


def _norm(value: Any) -> str | None:
    """Normalise SQL text so cosmetic differences (quotes, casts, parentheses) don't matter."""
    if value is None:
        return None
    out = re.sub(r"::[a-z_ ]+(\[\])?", "", str(value).lower())
    return re.sub(r"[\s()\"']", "", out)


def signature(engine: Engine) -> dict[str, dict[str, Any]]:
    insp = inspect(engine)
    where_key = f"{engine.dialect.name}_where"
    sig: dict[str, dict[str, Any]] = {}
    for table in sorted(insp.get_table_names()):
        if table == "alembic_version":
            continue
        pk = insp.get_pk_constraint(table)
        sig[table] = {
            "columns": {
                c["name"]: (str(c["type"]).lower(), c["nullable"], _norm(c.get("default")))
                for c in insp.get_columns(table)
            },
            "pk": (pk.get("name"), tuple(pk["constrained_columns"])),
            "fks": sorted(
                (
                    fk["name"],
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    (fk.get("options") or {}).get("ondelete"),
                )
                for fk in insp.get_foreign_keys(table)
            ),
            "uniques": sorted(
                (u["name"], tuple(u["column_names"])) for u in insp.get_unique_constraints(table)
            ),
            "checks": sorted(
                (c["name"], _norm(c["sqltext"])) for c in insp.get_check_constraints(table)
            ),
            "indexes": sorted(
                (
                    i["name"],
                    tuple(i["column_names"]),
                    bool(i["unique"]),
                    _norm((i.get("dialect_options") or {}).get(where_key)),
                )
                for i in insp.get_indexes(table)
            ),
        }
    return sig


def _build_from_models(url: str) -> Engine:
    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    return engine


def test_migrated_schema_is_structurally_identical_to_models(
    db_url: str, second_db_url: str
) -> None:
    migrate.upgrade(db_url)
    migrated = create_db_engine(db_url)
    from_models = _build_from_models(second_db_url)
    try:
        a, b = signature(migrated), signature(from_models)
    finally:
        migrated.dispose()
        from_models.dispose()
    assert set(a) == set(b)
    for table in a:
        for part in a[table]:
            assert a[table][part] == b[table][part], (
                f"{table}.{part} differs between migration and model"
            )


def test_every_model_check_and_foreign_key_exists_in_the_migrated_database(engine: Engine) -> None:
    insp = inspect(engine)
    for name, table in Base.metadata.tables.items():
        model_checks = [c for c in table.constraints if isinstance(c, CheckConstraint)]
        assert len(insp.get_check_constraints(name)) == len(model_checks), f"{name}: CHECK count"
        model_fks = [c for c in table.constraints if isinstance(c, ForeignKeyConstraint)]
        reflected = insp.get_foreign_keys(name)
        assert len(reflected) == len(model_fks), f"{name}: FK count"
        for fk in model_fks:
            expected = (fk.ondelete or "").upper() or None
            actual = {
                tuple(r["constrained_columns"]): (r.get("options") or {}).get("ondelete")
                for r in reflected
            }[tuple(fk.column_keys)]
            assert actual == expected, f"{name}: ON DELETE for {list(fk.column_keys)}"


@pytest.mark.parametrize(("index", "token"), sorted(PARTIAL_INDEXES.items()))
def test_partial_unique_indexes_keep_their_predicate(
    engine: Engine, index: str, token: str
) -> None:
    table = {
        "ux_run_scope_running": "run",
        "ux_data_quality_issue_open_fingerprint": "data_quality_issue",
        "ux_review_analysis_current": "review_analysis",
    }[index]
    found = [i for i in inspect(engine).get_indexes(table) if i["name"] == index]
    assert len(found) == 1 and found[0]["unique"]
    predicate = (found[0].get("dialect_options") or {}).get(f"{engine.dialect.name}_where")
    assert predicate is not None, f"{index} lost its WHERE clause"
    assert token in str(predicate).lower(), f"{index} has an unexpected WHERE clause"


def test_negative_control_comparison_detects_a_dropped_predicate(
    db_url: str, second_db_url: str
) -> None:
    migrate.upgrade(db_url)
    broken = create_db_engine(db_url)
    reference = _build_from_models(second_db_url)
    try:
        with broken.begin() as conn:  # the exact regression compare_metadata cannot see
            conn.execute(text("DROP INDEX ux_run_scope_running"))
            conn.execute(text("CREATE UNIQUE INDEX ux_run_scope_running ON run (scope_key)"))
        assert signature(broken)["run"]["indexes"] != signature(reference)["run"]["indexes"]
    finally:
        broken.dispose()
        reference.dispose()
