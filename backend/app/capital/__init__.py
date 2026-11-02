"""Capital profile helpers for the 10K ₪ paper desk and the recovered 100K desk.

Day brakes and sizing on ``10k_ils`` use **percent of live account equity**
(morning mark / current equity). ILS appears only for display and for
equity-tier thresholds. ``ils_per_usd`` defaults to 3.14 (frankfurter /
er-api live read 2026-11-02); a daily Frankfurter fetch overrides it when
available, with 3.14 as fallback.

Tiers (by equity × ils_per_usd):
- ≤25K ₪: per-trade 15%, kill 8%, arm 6% / protect 4.5%, exposure 100%, DAY_TRAIL
- 25K–100K ₪: per-trade 10%, kill 6%, arm/protect scaled (provisional), DAY_TRAIL
- >100K ₪: 100K rules (7% / 35% / 3.5% kill / ladder, no trail)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.config.settings import TradingConfig

PROFILE_10K = "10k_ils"
PROFILE_100K = "100k"

# Fallback FX when Frankfurter is unreachable. Live-read 2026-11-02:
# frankfurter 3.1393, er-api 3.1405 → config default 3.14.
DEFAULT_ILS_PER_USD = Decimal("3.14")


@dataclass(frozen=True)
class CapitalTier:
    name: str
    per_trade_pct: Decimal
    hard_per_trade_pct: Decimal
    exposure_pct: Decimal
    kill_pct: Decimal
    arm_pct: Decimal
    protect_pct: Decimal
    day_trail: bool
    provisional: bool = False


def profile_is_10k(config: TradingConfig) -> bool:
    return str(getattr(config, "capital_profile", PROFILE_100K) or PROFILE_100K) == PROFILE_10K


def ils_per_usd(config: TradingConfig) -> Decimal:
    """Effective USD→ILS rate: live Frankfurter when fresh, else config / 3.14."""
    try:
        from app.capital.fx import live_ils_per_usd

        live = live_ils_per_usd()
        if live is not None and live > 0:
            return live
    except Exception:
        pass
    raw = getattr(config, "ils_per_usd", None)
    if raw in {None, "", 0, 0.0}:
        return DEFAULT_ILS_PER_USD
    rate = Decimal(str(raw))
    return rate if rate > 0 else DEFAULT_ILS_PER_USD


def working_capital_usd(config: TradingConfig) -> Decimal | None:
    """USD basis for the 10K profile (display / fallback), or None on 100K."""
    if not profile_is_10k(config):
        return None
    ils = Decimal(str(getattr(config, "working_capital_ils", 0) or 0))
    if ils <= 0:
        return None
    return (ils / ils_per_usd(config)).quantize(Decimal("0.01"))


def equity_ils(account_equity: Decimal | None, config: TradingConfig) -> Decimal | None:
    if account_equity is None:
        return None
    return (account_equity * ils_per_usd(config)).quantize(Decimal("0.01"))


def risk_equity(account_equity: Decimal | None, config: TradingConfig) -> Decimal | None:
    """Equity used for % brakes and sizing. Live equity — no WC cap on 10k_ils."""
    return account_equity


def resolve_tier(config: TradingConfig, account_equity: Decimal | None = None) -> CapitalTier:
    """Pick the active size / brake tier. 100K profile always returns classic rules."""
    if not profile_is_10k(config):
        return _tier_100k(config)

    basis = account_equity if account_equity is not None else working_capital_usd(config)
    ils = equity_ils(basis, config) if basis is not None else Decimal(str(getattr(config, "working_capital_ils", 10000) or 10000))
    t1 = Decimal(str(getattr(config, "capital_tier1_max_ils", 25000) or 25000))
    t2 = Decimal(str(getattr(config, "capital_tier2_max_ils", 100000) or 100000))

    if ils is not None and ils > t2:
        return _tier_100k(config)

    if ils is not None and ils > t1:
        # Provisional mid tier: kill/arm/protect scale from tier-1 by kill ratio 6/8.
        per = Decimal(str(getattr(config, "capital_tier2_per_trade_pct", 0.10) or 0.10))
        kill = Decimal(str(getattr(config, "capital_tier2_kill_pct", 0.06) or 0.06))
        arm = Decimal(str(getattr(config, "capital_tier2_arm_pct", 0.045) or 0.045))
        protect = Decimal(str(getattr(config, "capital_tier2_protect_pct", 0.03375) or 0.03375))
        exp = Decimal(str(getattr(config, "capital_tier2_exposure_pct", 1.0) or 1.0))
        return CapitalTier(
            name="mid_25k_100k",
            per_trade_pct=per,
            hard_per_trade_pct=max(per, per + Decimal("0.05")),
            exposure_pct=exp,
            kill_pct=kill,
            arm_pct=arm,
            protect_pct=protect,
            day_trail=True,
            provisional=True,
        )

    per = Decimal(str(getattr(config, "capital_tier1_per_trade_pct", 0.15) or 0.15))
    kill = Decimal(str(getattr(config, "capital_tier1_kill_pct", 0.08) or 0.08))
    arm = Decimal(str(getattr(config, "capital_tier1_arm_pct", 0.06) or 0.06))
    protect = Decimal(str(getattr(config, "capital_tier1_protect_pct", 0.045) or 0.045))
    exp = Decimal(str(getattr(config, "capital_tier1_exposure_pct", 1.0) or 1.0))
    return CapitalTier(
        name="small_le_25k",
        per_trade_pct=per,
        hard_per_trade_pct=max(per, Decimal(str(getattr(config, "hard_position_capital_pct", 0.20) or 0.20))),
        exposure_pct=exp,
        kill_pct=kill,
        arm_pct=arm,
        protect_pct=protect,
        day_trail=True,
        provisional=False,
    )


def _tier_100k(config: TradingConfig) -> CapitalTier:
    per = Decimal(str(getattr(config, "max_capital_per_trade_pct", 0.07) or 0.07))
    if not profile_is_10k(config):
        per = Decimal(str(getattr(config, "normal_position_capital_pct", 0.07) or 0.07))
    else:
        # When 10k profile grows past 100K ₪, use classic 100K fractions.
        per = Decimal("0.07")
    return CapitalTier(
        name="large_100k_rules",
        per_trade_pct=per if not profile_is_10k(config) else Decimal("0.07"),
        hard_per_trade_pct=Decimal("0.10"),
        exposure_pct=Decimal("0.35"),
        kill_pct=Decimal("0.035"),
        arm_pct=Decimal("0"),
        protect_pct=Decimal("0"),
        day_trail=False,
        provisional=False,
    )


def apply_tier(config: TradingConfig, account_equity: Decimal | None = None) -> TradingConfig:
    """Return a TradingConfig with pct fields overridden for the active tier."""
    tier = resolve_tier(config, account_equity)
    if not profile_is_10k(config) and tier.name == "large_100k_rules":
        return config
    return replace(
        config,
        normal_position_capital_pct=float(tier.per_trade_pct),
        hard_position_capital_pct=float(tier.hard_per_trade_pct),
        max_capital_per_trade_pct=float(tier.per_trade_pct),
        max_total_open_exposure_pct=float(tier.exposure_pct),
        max_aggregate_planned_risk_pct=float(tier.exposure_pct),
        day_trail_enabled=bool(tier.day_trail and getattr(config, "day_trail_enabled", True)),
        day_trail_arm_pct=float(tier.arm_pct) if tier.day_trail else float(getattr(config, "day_trail_arm_pct", 0) or 0),
        day_trail_protect_pct=float(tier.protect_pct) if tier.day_trail else float(getattr(config, "day_trail_protect_pct", 0) or 0),
        daily_kill_pct=float(tier.kill_pct),
    )


def effective_kill_usd(config: TradingConfig, account_equity: Decimal | None = None) -> Decimal:
    """Dollar kill line. 10K: kill_pct × live equity (fallback: × WC). 100K: fixed dollars or 3.5%."""
    if profile_is_10k(config):
        tier = resolve_tier(config, account_equity)
        if not tier.day_trail and tier.name == "large_100k_rules":
            # Grown into 100K rules while still on the 10k profile switch.
            basis = account_equity if account_equity is not None else working_capital_usd(config)
            if basis is not None and tier.kill_pct > 0:
                return (basis * tier.kill_pct).quantize(Decimal("0.01"))
            return Decimal(str(getattr(config, "daily_kill_loss_usd", 3500) or 3500))
        basis = account_equity if account_equity is not None else working_capital_usd(config)
        pct = tier.kill_pct
        if pct <= 0:
            # Legacy fixed ILS kill if pct unset.
            ils = Decimal(str(getattr(config, "daily_kill_ils", 0) or 0))
            if ils > 0:
                return (ils / ils_per_usd(config)).quantize(Decimal("0.01"))
            return Decimal("0")
        if basis is None:
            return Decimal("0")
        return (basis * pct).quantize(Decimal("0.01"))
    dollars = Decimal(str(getattr(config, "daily_kill_loss_usd", 0) or 0))
    if dollars > 0:
        return dollars
    # 0 disables the dollar kill; callers may fall back to daily_hard_stop_pct.
    return Decimal("0")


def day_trail_enabled(config: TradingConfig, account_equity: Decimal | None = None) -> bool:
    if not profile_is_10k(config):
        return False
    if not bool(getattr(config, "day_trail_enabled", False)):
        return False
    return resolve_tier(config, account_equity).day_trail


def day_trail_arm_usd(config: TradingConfig, account_equity: Decimal | None = None) -> Decimal | None:
    if not day_trail_enabled(config, account_equity):
        return None
    tier = resolve_tier(config, account_equity)
    basis = account_equity if account_equity is not None else working_capital_usd(config)
    if basis is None or tier.arm_pct <= 0:
        return None
    return (basis * tier.arm_pct).quantize(Decimal("0.01"))


def day_trail_protect_usd(config: TradingConfig, account_equity: Decimal | None = None) -> Decimal | None:
    """Absolute first protected floor in USD (never below this after arm)."""
    if not day_trail_enabled(config, account_equity):
        return None
    tier = resolve_tier(config, account_equity)
    basis = account_equity if account_equity is not None else working_capital_usd(config)
    if basis is None:
        return None
    protect = tier.protect_pct
    if protect <= 0:
        # Legacy: fraction of arm.
        arm = day_trail_arm_usd(config, account_equity)
        if arm is None:
            return None
        frac = Decimal(str(getattr(config, "day_trail_initial_floor_frac", 0.5) or 0.5))
        return (arm * frac).quantize(Decimal("0.01"))
    return (basis * protect).quantize(Decimal("0.01"))
