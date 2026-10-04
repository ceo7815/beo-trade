from __future__ import annotations

from decimal import Decimal


def _level(quote: dict | None) -> dict | None:
    if not quote:
        return None
    price = quote.get("price")
    change = quote.get("change_percent")
    if price is None:
        return None
    return {
        "symbol": quote.get("symbol"),
        "price": float(price),
        "change_percent": None if change is None else float(change),
    }


def build_regime(quotes: dict[str, dict | None], flat_percent: float = 0.15, elevated_vix: float = 20, stressed_vix: float = 28) -> dict:
    """Context only. Missing quotes stay null. Nothing here is a trade."""
    spy = _level(quotes.get("SPY"))
    qqq = _level(quotes.get("QQQ"))
    iwm = _level(quotes.get("IWM"))
    vix = _level(quotes.get("VIX"))
    changes = [item["change_percent"] for item in (spy, qqq, iwm) if item and item["change_percent"] is not None]
    if not changes:
        trend = "UNKNOWN"
    elif all(value > flat_percent for value in changes):
        trend = "UP"
    elif all(value < -flat_percent for value in changes):
        trend = "DOWN"
    else:
        trend = "MIXED"
    vix_price = None if vix is None else vix["price"]
    if vix_price is None:
        volatility_state = "UNKNOWN"
    elif vix_price >= stressed_vix:
        volatility_state = "STRESSED"
    elif vix_price >= elevated_vix:
        volatility_state = "ELEVATED"
    else:
        volatility_state = "CALM"
    if trend == "UNKNOWN" or volatility_state == "UNKNOWN":
        risk_state = "UNKNOWN"
    elif volatility_state == "STRESSED" or trend == "DOWN":
        risk_state = "CAUTION"
    else:
        risk_state = "OPEN"
    return {
        "spy": spy,
        "qqq": qqq,
        "iwm": iwm,
        "vix": vix,
        "trend": trend,
        "trend_state": trend,
        "volatility_state": volatility_state,
        "risk_state": risk_state,
        "market_regime": f"{trend}/{volatility_state}/{risk_state}",
        "status": "CALCULATED",
    }


_LAST: dict = {"status": "WAITING_FOR_MARKET_DATA"}


def last_regime() -> dict:
    return dict(_LAST)


def _macro_view(macro: dict | None) -> dict | None:
    if not isinstance(macro, dict):
        return None
    series = macro.get("series")
    if not isinstance(series, list):
        return {"source": "fred"} if macro.get("source") == "fred" else None
    rows = []
    for item in series:
        if isinstance(item, dict) and item.get("value") is not None:
            rows.append({"id": item.get("id") or item.get("series_id"), "value": item.get("value"), "date": item.get("date") or item.get("observation_date")})
    if not rows:
        return None
    return {"source": "fred", "series": rows}


def regime_from_context(quotes: dict | None, macro: dict | None = None, flat_percent: float = 0.15, elevated_vix: float = 20, stressed_vix: float = 28) -> dict:
    """Context only. Missing market quotes do not become a regime or a trade."""
    global _LAST
    usable = False
    if isinstance(quotes, dict):
        for symbol in ("SPY", "QQQ", "IWM", "VIX"):
            if _level(quotes.get(symbol)):
                usable = True
                break
    if not usable:
        waiting = {
            "status": "WAITING_FOR_MARKET_DATA",
            "trend_state": None,
            "volatility_state": None,
            "risk_state": None,
            "market_regime": None,
            "macro": _macro_view(macro),
        }
        _LAST = waiting
        return waiting
    built = build_regime(quotes or {}, flat_percent, elevated_vix, stressed_vix)
    built["macro"] = _macro_view(macro)
    _LAST = built
    return built


def decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
