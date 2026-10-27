"""Lightweight additive schema migrations for existing databases.

The project bootstraps new databases with ``Base.metadata.create_all`` and
seeds demo data on startup (see ``app.main``). ``create_all`` never adds
columns to tables that already exist, so columns introduced after the first
deployment are applied here with idempotent ``ALTER TABLE ... ADD COLUMN``.

Only nullable columns are supported — sufficient for the platform's additive
evolution. SQLite (dev/default) and PostgreSQL are both supported.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.services.logger import logger

# table -> {column: column DDL type}
_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "eval_cases": {
        "turns": "JSON",
    },
    "traces": {
        "session_id": "VARCHAR(64)",
    },
    "trace_steps": {
        "step_key": "VARCHAR(64)",
        "parent_key": "VARCHAR(64)",
    },
}


def ensure_additive_columns(engine: Engine) -> list[str]:
    """Add any missing nullable columns. Returns descriptions applied."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    is_sqlite = engine.dialect.name == "sqlite"

    with engine.begin() as conn:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all will create it with the full schema
            present = {col["name"] for col in inspector.get_columns(table)}
            for column, ddl_type in columns.items():
                if column in present:
                    continue
                # SQLite has no native JSON type; store as TEXT (SQLAlchemy's
                # JSON bind/result processors handle serialization regardless
                # of the declared column affinity).
                effective_type = "TEXT" if is_sqlite and ddl_type == "JSON" else ddl_type
                conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {column} {effective_type}")
                )
                applied.append(f"{table}.{column}")
                logger.info(f"Lightweight migration: added column {table}.{column}")

    return applied
