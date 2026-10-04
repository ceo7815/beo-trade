from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.config.settings import TradingConfig
from app.schemas.domain import OptionSnapshot, UnderlyingSnapshot


def rank_contracts(
    options: list[OptionSnapshot],
    underlying: UnderlyingSnapshot,
    config: TradingConfig,
    as_of: datetime,
) -> list[OptionSnapshot]:
    def score(option: OptionSnapshot) -> float:
        mid = float(option.mid)
        spread = float((option.ask - option.bid) / option.mid)
        delta_gap = abs(abs(float(option.delta or 0)) - config.target_delta)
        moneyness = abs(float(option.strike) - float(underlying.price)) / float(underlying.price)
        dte = (option.expiration - as_of.date()).days
        volume_score = min(option.volume, 5000) / 5000
        oi_score = min(option.open_interest, 5000) / 5000
        gamma_score = min(abs(float(option.gamma or 0)), 0.2) / 0.2
        return volume_score + oi_score + gamma_score * 0.5 - spread * 3 - delta_gap - moneyness - dte * 0.02

    ordered = sorted(options, key=score, reverse=True)
    return ordered[: config.shortlist_size]


def risk_plan(
    option: OptionSnapshot,
    equity: Decimal,
    config: TradingConfig,
    open_premium: Decimal = Decimal("0"),
    open_positions: int = 0,
    same_underlying_positions: int = 0,
    sector_premium: Decimal = Decimal("0"),
    open_planned_risk: Decimal = Decimal("0"),
    buying_power: Decimal | None = None,
):
    """Size from the AGGRESSIVE policy. Capital and planned risk are separate."""
    from app.policy.sizing import size_position

    return size_position(
        option,
        equity,
        config,
        open_premium,
        open_positions,
        same_underlying_positions,
        sector_premium,
        open_planned_risk,
        buying_power,
    )
