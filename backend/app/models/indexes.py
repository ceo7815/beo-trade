"""Idempotent indexes for the trade-report lookups.

Existing SQLite files are created with create_all, which does not add indexes
that were introduced later. CREATE INDEX IF NOT EXISTS is safe to run on every boot.
"""

from __future__ import annotations

READ_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "ix_broker_orders_broker_order_id",
        "CREATE INDEX IF NOT EXISTS ix_broker_orders_broker_order_id ON broker_orders (broker_order_id)",
    ),
    (
        "ix_recommendations_underlying_decision",
        "CREATE INDEX IF NOT EXISTS ix_recommendations_underlying_decision ON recommendations (underlying, decision, created_at)",
    ),
)


def ensure_read_indexes(engine=None) -> list[str]:
    """Create the read indexes when the tables already exist. Missing tables are skipped."""
    from app.models.db import _engine

    target = engine if engine is not None else _engine
    if target is None:
        return []
    created: list[str] = []
    for name, statement in READ_INDEXES:
        try:
            with target.begin() as connection:
                connection.exec_driver_sql(statement)
        except Exception:
            continue
        created.append(name)
    return created
