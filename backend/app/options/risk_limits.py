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


def entry_pnl(mark: Decimal | None, realized: Decimal | None) -> Decimal | None:
    """Use whichever figure is worse. A green open position must not hide a loss that is already closed."""
    values = [item for item in (mark, realized) if item is not None]
    return None if not values else min(values)


def daily_loss_reason(equity: Decimal, daily_pnl: Decimal, config: TradingConfig) -> str | None:
    """Block a new buy at the entry stop. The hard stop also blocks buys."""
    state = daily_loss_state(equity, daily_pnl, config)
    if state == "hard_stop":
        return "DAILY_HARD_STOP"
    if state == "entry_stop":
        return "DAILY_LOSS_LIMIT_REACHED"
    return None


def day_flatten_reason(equity: Decimal | None, day_pnl: Decimal | None, config: TradingConfig) -> str | None:
    """Close the session when total day P&L hits the dollar kill or the percent hard stop, whichever is first.

    day_pnl is the account mark versus last_equity: realized plus the unrealized of open positions, after same-day funding.
    A missing mark does not flatten. The dollar line is daily_kill_loss_usd; 0 turns that line off and leaves the percent stop.
    """
    if day_pnl is None:
        return None
    limit = Decimal(str(config.daily_kill_loss_usd))
    if limit > 0 and day_pnl <= -limit:
        return "DAILY_HARD_STOP"
    if equity is None:
        return None
    if daily_loss_state(equity, day_pnl, config) == "hard_stop":
        return "DAILY_HARD_STOP"
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
