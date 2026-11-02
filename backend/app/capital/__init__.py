"""Working-capital helpers for the 10K ₪ paper profile.

When ``capital_profile`` is ``10k_ils``, sizing and risk use
``min(account equity, working_capital_usd)``. When the profile is ``100k``
(or working capital is unset), every helper returns the account equity
unchanged so the 100K desk behaves exactly as before.
"""

from __future__ import annotations

from decimal import Decimal

from app.config.settings import TradingConfig

PROFILE_10K = "10k_ils"
PROFILE_100K = "100k"


def profile_is_10k(config: TradingConfig) -> bool:
    return str(getattr(config, "capital_profile", PROFILE_100K) or PROFILE_100K) == PROFILE_10K


def working_capital_usd(config: TradingConfig) -> Decimal | None:
    """USD basis for the 10K profile, or None when the 100K profile is active."""
    if not profile_is_10k(config):
        return None
    ils = Decimal(str(getattr(config, "working_capital_ils", 0) or 0))
    rate = Decimal(str(getattr(config, "ils_per_usd", 0) or 0))
    if ils <= 0 or rate <= 0:
        return None
    return (ils / rate).quantize(Decimal("0.01"))


def risk_equity(account_equity: Decimal | None, config: TradingConfig) -> Decimal | None:
    """Equity used for sizing and percentage risk limits."""
    if account_equity is None:
        return None
    cap = working_capital_usd(config)
    if cap is None:
        return account_equity
    if account_equity <= 0:
        return account_equity
    return min(account_equity, cap)


def effective_kill_usd(config: TradingConfig) -> Decimal:
    """Dollar kill line. 10K profile uses ``daily_kill_ils`` / rate; 100K keeps ``daily_kill_loss_usd``."""
    if profile_is_10k(config):
        ils = Decimal(str(getattr(config, "daily_kill_ils", 0) or 0))
        rate = Decimal(str(getattr(config, "ils_per_usd", 0) or 0))
        if ils > 0 and rate > 0:
            return (ils / rate).quantize(Decimal("0.01"))
    return Decimal(str(getattr(config, "daily_kill_loss_usd", 0) or 0))


def day_trail_enabled(config: TradingConfig) -> bool:
    if not profile_is_10k(config):
        return False
    return bool(getattr(config, "day_trail_enabled", False))


def day_trail_arm_usd(config: TradingConfig) -> Decimal | None:
    """Arm threshold in USD (default 4% of working capital)."""
    cap = working_capital_usd(config)
    if cap is None:
        return None
    pct = Decimal(str(getattr(config, "day_trail_arm_pct", 0.04) or 0))
    if pct <= 0:
        return None
    return (cap * pct).quantize(Decimal("0.01"))
