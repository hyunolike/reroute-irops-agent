"""Idempotent, additive schema migrations run after `create_all()`.

`create_all()` creates missing tables but never alters existing ones, so columns added after a database
was first created (e.g. a persisted Docker volume) are added here. Keep entries additive and nullable.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("reroute.db")

# (table, column, column DDL)
ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("agent_tasks", "pending", "VARCHAR(16)"),
]


def migrate(engine: Engine) -> list[str]:
    applied: list[str] = []
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in ADDITIVE_COLUMNS:
            if table not in tables:
                continue
            if column in {c["name"] for c in insp.get_columns(table)}:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            applied.append(f"{table}.{column}")
            log.info("migration: added %s.%s", table, column)
    return applied
