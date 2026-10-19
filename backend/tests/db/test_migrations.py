"""Alembic migrations against a real, empty PostgreSQL database."""

import re

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, create_engine, inspect, text

from app.db.models import Base
from tests.db.conftest import TemporaryDatabase, alembic_config


def test_upgrade_matches_models_and_downgrade_is_clean(
    temporary_database: TemporaryDatabase,
) -> None:
    config = alembic_config(temporary_database.url)
    engine = create_engine(temporary_database.url)
    try:
        command.upgrade(config, "head")
        with engine.connect() as connection:
            diff = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
            )
            tables = set(inspect(connection).get_table_names())
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert diff == []
        assert tables == set(Base.metadata.tables) | {"alembic_version"}
        assert version == "0002"

        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == {"alembic_version"}

        command.upgrade(config, "head")  # re-applying after a downgrade works
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) >= set(Base.metadata.tables)
    finally:
        engine.dispose()


def test_check_constraints_and_generated_column_exist(
    temporary_database: TemporaryDatabase,
) -> None:
    command.upgrade(alembic_config(temporary_database.url), "head")
    engine = create_engine(temporary_database.url)
    try:
        with engine.connect() as connection:
            checks = {
                row.conname
                for row in connection.execute(
                    text("SELECT conname FROM pg_constraint WHERE contype = 'c'")
                )
            }
            generated = connection.execute(
                text(
                    "SELECT is_generated FROM information_schema.columns "
                    "WHERE table_name = 'trace_events' AND column_name = 'duration_ms'"
                )
            ).scalar()
    finally:
        engine.dispose()

    assert {
        "ck_runs_answer_iff_completed",
        "ck_trace_events_root_iff_run",
        "ck_review_annotations_single_matching_target",
        "ck_runs_status_values",
    } <= checks
    assert generated == "ALWAYS"


def _quoted_values(sql: str) -> set[str]:
    return set(re.findall(r"'([^']*)'", sql))


def test_enum_check_constraints_match_models(temporary_database: TemporaryDatabase) -> None:
    """Autogenerate does not compare CHECK constraints, so enum drift (a value added to a
    Python enum without a migration) would go unnoticed; compare the allowed values here."""
    command.upgrade(alembic_config(temporary_database.url), "head")
    expected: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint) and str(constraint.name).endswith("_values"):
                name = str(constraint.name)  # already the full convention name
                expected[name] = _quoted_values(str(constraint.sqltext))
    engine = create_engine(temporary_database.url)
    try:
        with engine.connect() as connection:
            actual = {
                row.conname: _quoted_values(row.definition)
                for row in connection.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) AS definition "
                        "FROM pg_constraint WHERE contype = 'c' AND conname LIKE '%\\_values'"
                    )
                )
            }
    finally:
        engine.dispose()

    assert "ck_criterion_results_criterion_type_values" in expected
    assert actual == expected
