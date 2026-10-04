from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from app.config.settings import TradingConfig
from app.schemas.domain import (
    BANNED_LANGUAGE,
    QUALITY_GATES,
    DecisionKind,
    ModelOutput,
    NewsItem,
    OptionSnapshot,
    Recommendation,
    ScenarioResult,
    UnderlyingSnapshot,
)


def language_ok(text: str) -> bool:
    lowered = text.casefold()
    if any(phrase in lowered for phrase in BANNED_LANGUAGE):
        return False
    if re.search(r"\d+\s*%\s*(הצלחה|סיכוי)", text):
        return False
    return True


def supported_reason_codes(
    events: set[str],
    news_ok: bool,
    option: OptionSnapshot,
    config: TradingConfig,
) -> set[str]:
    codes: set[str] = set()
    if news_ok:
        codes.update({"NEWS_CATALYST", "EVENT_DRIVEN"})
    if "UNUSUAL_VOLUME" in events:
        codes.add("UNUSUAL_VOLUME")
    if events & {"MOMENTUM", "BREAKOUT", "BREAKDOWN", "REVERSAL", "GAP"}:
        codes.add("MOMENTUM")
        codes.add("TECHNICAL_CONFIRMATION")
    codes.add("LIQUIDITY")
    if option.implied_volatility and option.implied_volatility > 0:
        codes.add("IV_SETUP")
    if option.gamma is not None and option.gamma >= Decimal(str(config.min_gamma)):
        codes.add("GAMMA_SETUP")
    if option.delta is not None:
        codes.add("DELTA_SETUP")
    codes.add("SHORT_TERM_STRUCTURE")
    return codes


def direction_matches(underlying: UnderlyingSnapshot, option: OptionSnapshot, events: set[str]) -> bool:
    if "REVERSAL" in events:
        return True
    if underlying.change_percent > 0 and option.right.value == "PUT":
        return False
    if underlying.change_percent < 0 and option.right.value == "CALL":
        return False
    return True


def scenario_sane(option: OptionSnapshot, scenarios: list[ScenarioResult]) -> bool:
    if not scenarios:
        return False
    by_move = {item.move_percent: item for item in scenarios}
    flat = by_move.get(0.0) or by_move.get(0)
    up = by_move.get(1.0)
    down = by_move.get(-1.0)
    if flat is None or up is None or down is None:
        return False
    if option.right.value == "CALL":
        return up.option_price > flat.option_price and down.option_price < flat.option_price
    return down.option_price > flat.option_price and up.option_price < flat.option_price


def build_recommendation(
    *,
    recommendation_id: str,
    as_of: datetime,
    underlying: UnderlyingSnapshot,
    option: OptionSnapshot,
    config: TradingConfig,
    events: set[str],
    news_items: list[NewsItem],
    news_ok: bool,
    scenarios: list[ScenarioResult],
    quantity: int,
    max_entry: Decimal,
    model: ModelOutput | None,
    shortlist_symbols: set[str],
    budget_blocked: bool,
) -> Recommendation:
    gates = {name: False for name in QUALITY_GATES}
    gates["DATA_OK"] = option.observed_at <= as_of and underlying.observed_at <= as_of
    gates["NEWS_OK"] = news_ok or config.allow_buy_without_news
    gates["LIQUIDITY_OK"] = option.bid > 0 and option.ask >= option.bid
    gates["OPTIONS_OK"] = option.option_symbol in shortlist_symbols and direction_matches(underlying, option, events)
    gates["GREEKS_OK"] = None not in (option.delta, option.gamma, option.theta, option.vega)
    gates["IV_OK"] = option.implied_volatility is not None and option.implied_volatility > 0
    gates["RISK_OK"] = quantity >= 1 and option.ask <= max_entry
    gates["SCENARIO_OK"] = scenario_sane(option, scenarios)

    codes: list[str] = []
    thesis = catalyst = risk = invalidation = ""
    decision = DecisionKind.SUPPRESS
    reason = "quant"
    prompt_version = ""
    model_name = ""
    audit_hash = ""
    if budget_blocked:
        reason = "AI_BUDGET_EXCEEDED"
    elif model is None:
        reason = "ai_missing"
    else:
        prompt_version = model.prompt_version
        model_name = model.model
        audit_hash = model.input_hash
        allowed = supported_reason_codes(events, news_ok, option, config)
        codes = [code for code in model.reason_codes if code in allowed]
        text = " ".join([model.thesis, model.catalyst, model.risk, model.invalidation])
        in_list = model.option_symbol == option.option_symbol
        data_quality = str((model.raw or {}).get("data_quality") or "PASS")
        gates["AI_OK"] = (
            model.decision == "BUY"
            and in_list
            and data_quality != "FAIL"
            and len(codes) >= config.min_reason_codes
            and language_ok(text)
            and bool(model.thesis.strip())
            and bool(model.risk.strip())
            and bool(model.invalidation.strip())
        )
        thesis = model.thesis.strip()
        catalyst = model.catalyst.strip()
        risk = model.risk.strip()
        move_pct = config.invalidation_underlying_percent * 100
        stop_pct = config.initial_stop_decline_pct * 100
        invalidation = (
            f"{model.invalidation.strip()} "
            f"ביטול כמותי: תנועה של {move_pct:.1f}% נגד הכיוון, "
            f"או ירידת מחיר האופציה ב-{stop_pct:.0f}% ממחיר הכניסה."
        ).strip()
        if model.decision != "BUY":
            reason = "model_suppress"
        elif not in_list:
            reason = "symbol_not_in_shortlist"
        elif not gates["AI_OK"]:
            reason = "ai_validation"

    if all(gates.values()):
        decision = DecisionKind.BUY
        reason = ""

    return Recommendation(
        recommendation_id=recommendation_id,
        decision=decision,
        timestamp=as_of,
        underlying=underlying.symbol,
        underlying_price=underlying.price,
        option_symbol=option.option_symbol,
        call_put=option.right,
        strike=option.strike,
        expiration=option.expiration,
        option_price=option.mid.quantize(Decimal("0.01")),
        bid=option.bid,
        ask=option.ask,
        delta=option.delta or Decimal("0"),
        gamma=option.gamma or Decimal("0"),
        theta=option.theta or Decimal("0"),
        vega=option.vega or Decimal("0"),
        iv=option.implied_volatility or Decimal("0"),
        volume=option.volume,
        open_interest=option.open_interest,
        max_entry_price=max_entry,
        quantity=quantity,
        holding_window_min=config.holding_window_min_minutes,
        holding_window_max=config.holding_window_max_minutes,
        thesis=thesis,
        catalyst=catalyst,
        risk=risk,
        invalidation=invalidation,
        reason_codes=codes,
        gate_results=gates,
        suppress_reason=reason,
        scenarios=scenarios,
        audit_hash=audit_hash,
        prompt_version=prompt_version,
        model=model_name,
        model_decision="" if model is None else model.decision,
        input_tokens=0 if model is None else model.input_tokens,
        cached_tokens=0 if model is None else model.cached_tokens,
        output_tokens=0 if model is None else model.output_tokens,
        reasoning_tokens=0 if model is None else model.reasoning_tokens,
        estimated_cost=Decimal("0") if model is None else model.cost,
    )
