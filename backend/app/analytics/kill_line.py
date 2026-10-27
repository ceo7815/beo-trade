"""Dollar kill line for read payloads and the desk gauge.

The key and default match trading.toml: ``daily_kill_loss_usd = 3500``.
``0`` falls back to equity × ``daily_hard_stop_pct``. This helper does not
place an order or write the floor.
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
