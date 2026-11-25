"""Deterministic regime policy for a new long option.

This does not create a trade. It only allows or suppresses a side.

- Status other than CALCULATED: suppress. The regime was not measured.
- Volatility STRESSED: suppress calls and puts, unless ``allow_stressed_puts``
  is enabled (Options 2 / v2610_40k), in which case PUTs remain eligible when
  the trend is not UP (tagged ``REGIME_STRESSED_PUT_ALLOWED``) while CALLs stay
  suppressed as ``REGIME_STRESSED``.
- Trend UP: calls allowed, puts suppressed.
- Trend DOWN: puts allowed, calls suppressed.
- Trend MIXED with volatility CALM or ELEVATED: both sides allowed.
  The regime is context and is not a hard directional block.
- UNKNOWN volatility does not suppress when the trend is known. A missing VIX quote is not a reason to block the session.
- UNKNOWN trend: suppress.
"""

from __future__ import annotations


def regime_trade_policy(
    regime: dict | None,
    right: str,
    *,
    allow_stressed_puts: bool = False,
) -> tuple[str, str]:
    if not isinstance(regime, dict) or regime.get("status") != "CALCULATED":
        return "SUPPRESS", "REGIME_WAITING"
    trend = regime.get("trend_state")
    volatility = regime.get("volatility_state")
    if trend in {None, "UNKNOWN"}:
        return "SUPPRESS", "REGIME_UNKNOWN"
    if volatility in {None, "UNKNOWN"}:
        volatility = "ELEVATED"
    side = right.upper()
    if volatility == "STRESSED":
        if allow_stressed_puts and side == "PUT":
            if trend == "UP":
                return "SUPPRESS", "REGIME_DIRECTION"
            return "ALLOW", "REGIME_STRESSED_PUT_ALLOWED"
        return "SUPPRESS", "REGIME_STRESSED"
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


def public_stressed_regime(trading: object) -> dict:
    """STRESSED PUT allowance as /risk and /system show it. No feed call."""
    enabled = bool(getattr(trading, "stressed_allow_puts", False)) if trading is not None else False
    return {
        "rule": "STRESSED_ALLOW_PUTS",
        "enabled": enabled,
        "allowed_sides": ["PUT"] if enabled else [],
        "blocked_sides": ["CALL"] if enabled else ["CALL", "PUT"],
    }
