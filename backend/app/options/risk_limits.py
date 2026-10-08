from __future__ import annotations

from decimal import Decimal

from app.config.settings import TradingConfig


def exposure_reason(equity: Decimal, open_premium: Decimal, proposed_premium: Decimal, config: TradingConfig) -> str | None:
    """Block a buy when current open premium plus the proposed premium exceeds the cap."""
    if equity <= 0 or open_premium < 0 or proposed_premium < 0:
        return "EXPOSURE_LIMIT"
    cap = equity * Decimal(str(config.max_total_open_exposure_pct))
    if open_premium + proposed_premium > cap:
        return "EXPOSURE_LIMIT"
    return None


def daily_loss_state(equity: Decimal, daily_pnl: Decimal, config: TradingConfig) -> str:
    """ok, warning, entry_stop, or hard_stop. A non-positive equity is a hard stop."""
    if equity <= 0:
        return "hard_stop"
    loss = -daily_pnl if daily_pnl < 0 else Decimal("0")
    if loss >= equity * Decimal(str(config.daily_hard_stop_pct)):
        return "hard_stop"
    if loss >= equity * Decimal(str(config.daily_entry_stop_pct)):
        return "entry_stop"
    if loss >= equity * Decimal(str(config.daily_warning_pct)):
        return "warning"
    return "ok"


def daily_loss_reason(equity: Decimal, daily_pnl: Decimal, config: TradingConfig) -> str | None:
    """Block a new buy at the entry stop. The hard stop also blocks buys and still allows sells."""
    state = daily_loss_state(equity, daily_pnl, config)
    if state == "hard_stop":
        return "DAILY_HARD_STOP"
    if state == "entry_stop":
        return "DAILY_LOSS_LIMIT_REACHED"
    return None


def sector_key(underlying: str, sector_of: dict[str, str] | None = None) -> str:
    """Without a known sector a name is its own bucket. One shared unknown bucket would cap all exposure at the sector limit."""
    symbol = str(underlying or "").strip().upper()
    known = (sector_of or {}).get(symbol)
    return known if known else f"UNCLASSIFIED:{symbol}"


def sector_exposure_reason(
    equity: Decimal,
    sector: str,
    open_premium_by_sector: dict[str, Decimal],
    added_premium: Decimal,
    config: TradingConfig,
) -> str | None:
    """One sector bucket, keyed by sector_key."""
    if equity <= 0 or added_premium < 0:
        return "SECTOR_EXPOSURE_LIMIT"
    cap = equity * Decimal(str(config.max_sector_exposure_pct))
    current = open_premium_by_sector.get(sector or "UNCLASSIFIED", Decimal("0"))
    if current + added_premium > cap:
        return "SECTOR_EXPOSURE_LIMIT"
    return None
