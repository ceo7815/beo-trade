"""Dollar kill line for read payloads and the desk gauge.

Order and exit decisions still use ``daily_hard_stop_pct`` inside
``daily_loss_state``. This helper never sells, blocks, or writes the floor.
"""

from __future__ import annotations

from decimal import Decimal

from app.config.settings import TradingConfig


def display_kill_usd(trading: TradingConfig, equity: Decimal | None) -> str | None:
    dollars = Decimal(str(trading.daily_kill_loss_usd or 0))
    if dollars > 0:
        return str(dollars.quantize(Decimal("0.01")))
    if equity is None:
        return None
    return str((equity * Decimal(str(trading.daily_hard_stop_pct))).quantize(Decimal("0.01")))
