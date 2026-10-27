"""Tests for additive startup migrations (ALTER TABLE ADD COLUMN)."""

from sqlalchemy import create_engine, inspect, text

from app.db_migrations import ensure_additive_columns


def test_adds_missing_columns_to_legacy_table():
    engine = create_engine("sqlite:///:memory:")
    # Simulate a legacy database whose ``traces`` table predates the
    # session_id / multi-turn features.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE traces ("
            "id INTEGER PRIMARY KEY, trace_id VARCHAR(64), user_input TEXT)"
        ))

    applied = ensure_additive_columns(engine)
    assert "traces.session_id" in applied

    columns = {c["name"] for c in inspect(engine).get_columns("traces")}
    assert "session_id" in columns


def test_idempotent_on_already_current_schema():
    from app.database import Base
    from app.models import trace  # noqa: F401  (register models)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    first = ensure_additive_columns(engine)
    second = ensure_additive_columns(engine)
    assert first == []
    assert second == []


def test_missing_table_is_skipped_for_fresh_create_all():
    from app.database import Base
    from app.models import dataset  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    # No tables at all: create_all owns fresh-schema creation; the migration
    # must neither error nor create partial tables.
    Base.metadata.create_all(bind=engine)
    applied = ensure_additive_columns(engine)
    assert applied == []

    columns = {c["name"] for c in inspect(engine).get_columns("eval_cases")}
    assert "turns" in columns
