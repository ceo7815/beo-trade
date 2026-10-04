from __future__ import annotations

import hashlib
import json
from decimal import Decimal


def _num(value, places: str = "0.0001") -> str:
    if value is None:
        return ""
    return str(Decimal(str(value)).quantize(Decimal(places)))


def decision_fingerprint(packet: dict) -> str:
    underlying = packet.get("underlying") or {}
    regime = packet.get("market_regime") or {}
    macro = packet.get("macro") or {}
    research = packet.get("research") or {}
    body = {
        "underlying": underlying.get("symbol"),
        "price": _num(underlying.get("price"), "0.01"),
        "change_percent": _num(underlying.get("change_percent"), "0.01"),
        "relative_volume": _num(underlying.get("relative_volume"), "0.01"),
        "volume": underlying.get("volume"),
        "contracts": [
            {
                "option_symbol": item.get("option_symbol"),
                "bid": _num(item.get("bid"), "0.01"),
                "ask": _num(item.get("ask"), "0.01"),
                "iv": _num(item.get("iv"), "0.0001"),
                "delta": _num(item.get("delta"), "0.0001"),
                "gamma": _num(item.get("gamma"), "0.0001"),
                "theta": _num(item.get("theta"), "0.0001"),
                "vega": _num(item.get("vega"), "0.0001"),
                "volume": item.get("volume"),
                "open_interest": item.get("open_interest"),
            }
            for item in packet.get("shortlist") or []
        ],
        "news": [
            {"id": item.get("id"), "published_at": item.get("published_at")}
            for item in packet.get("news") or []
        ],
        "regime": {
            "status": regime.get("status"),
            "trend": regime.get("trend_state"),
            "volatility": regime.get("volatility_state"),
            "risk": regime.get("risk_state"),
        },
        "macro": {
            "status": macro.get("status"),
            "series": sorted((macro.get("series") or {}).keys()) if isinstance(macro.get("series"), dict) else [],
        },
        "research": (research.get("company") or {}).get("ticker") if isinstance(research.get("company"), dict) else "",
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
