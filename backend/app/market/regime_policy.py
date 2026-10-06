"""Deterministic regime policy for a new long option.

This does not create a trade. It only allows or suppresses a side.

- Status other than CALCULATED: suppress. The regime was not measured.
- Volatility STRESSED: suppress calls and puts.
- Trend UP: calls allowed, puts suppressed.
- Trend DOWN: puts allowed, calls suppressed.
- Trend MIXED with volatility CALM or ELEVATED: both sides allowed.
  The regime is context and is not a hard directional block.
- UNKNOWN volatility does not suppress when the trend is known. A missing VIX quote is not a reason to block the session.
- UNKNOWN trend: suppress.
"""

from __future__ import annotations


def regime_trade_policy(regime: dict | None, right: str) -> tuple[str, str]:
    if not isinstance(regime, dict) or regime.get("status") != "CALCULATED":
        return "SUPPRESS", "REGIME_WAITING"
    trend = regime.get("trend_state")
    volatility = regime.get("volatility_state")
    if trend in {None, "UNKNOWN"}:
        return "SUPPRESS", "REGIME_UNKNOWN"
    if volatility in {None, "UNKNOWN"}:
        volatility = "ELEVATED"
    if volatility == "STRESSED":
        return "SUPPRESS", "REGIME_STRESSED"
    side = right.upper()
    if trend == "UP" and side == "PUT":
        return "SUPPRESS", "REGIME_DIRECTION"
    if trend == "DOWN" and side == "CALL":
        return "SUPPRESS", "REGIME_DIRECTION"
    if trend == "MIXED" and volatility in {"CALM", "ELEVATED"}:
        return "ALLOW", ""
    if trend == "UP" and side == "CALL":
        return "ALLOW", ""
    if trend == "DOWN" and side == "PUT":
        return "ALLOW", ""
    return "SUPPRESS", "REGIME_UNKNOWN"
