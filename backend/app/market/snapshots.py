from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MarketSnapshot, Underlying


def store_underlying_snapshots(session: Session, underlyings: list) -> int:
    """Persist only rows that already exist. An empty scan writes nothing."""
    stored = 0
    for item in underlyings:
        symbol = str(getattr(item, "symbol", "") or "").strip().upper()
        price = getattr(item, "price", None)
        observed_at = getattr(item, "observed_at", None)
        if not symbol or price is None or observed_at is None:
            continue
        row = session.scalar(select(Underlying).where(Underlying.symbol == symbol))
        if row is None:
            row = Underlying(symbol=symbol)
            session.add(row)
            session.flush()
        session.add(
            MarketSnapshot(
                underlying_id=row.id,
                price=price,
                open=getattr(item, "open", price),
                high=getattr(item, "high", price),
                low=getattr(item, "low", price),
                close=getattr(item, "close", price),
                volume=int(getattr(item, "volume", 0) or 0),
                relative_volume=getattr(item, "relative_volume", 0) or 0,
                observed_at=observed_at,
            )
        )
        stored += 1
    return stored
