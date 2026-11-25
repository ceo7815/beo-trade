from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.analytics.trading_rules import build_trading_rules
from app.config.settings import Settings, load_calendar, load_trading_config
from app.core.sessions import CLOSED, INTRADAY_SCAN, PRE_CLOSE, phase_at, session_bounds
from app.market.regime_policy import public_stressed_regime, regime_trade_policy
from app.ops.safety import policy_errors
from app.recommendations import pipeline
from app.recommendations.pipeline import run_scan
from app.schemas.domain import DecisionKind, OptionRight
from app.workers import loops
from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market


NY = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[1]
O2_TOML = ROOT / "app" / "config" / "trading_v2610_40k.toml"
K20_TOML = ROOT / "app" / "config" / "trading.toml"


def test_calendar_nov_25_is_normal_close_and_nov_27_and_dec_24_are_early_closes():
    cal = load_calendar()
    # 2026-11-25 (Wednesday before Thanksgiving) is a normal 16:00 NY close.
    nov25_mid = datetime(2026, 11, 25, 13, 30, tzinfo=NY)
    bounds25 = session_bounds(nov25_mid, cal)
    assert bounds25 is not None
    assert bounds25[1] == datetime(2026, 11, 25, 16, 0, tzinfo=NY)
    assert phase_at(nov25_mid, cal) == INTRADAY_SCAN

    # 2026-11-26 (Thanksgiving) stays closed.
    assert phase_at(datetime(2026, 11, 26, 11, 0, tzinfo=NY), cal) == CLOSED

    # 2026-11-27 (Friday after Thanksgiving) and 2026-12-24 close early at 13:00 NY.
    for month, day in ((11, 27), (12, 24)):
        early_mid = datetime(2026, month, day, 12, 50, tzinfo=NY)
        bounds = session_bounds(early_mid, cal)
        assert bounds is not None
        assert bounds[1] == datetime(2026, month, day, 13, 0, tzinfo=NY)
        assert phase_at(early_mid, cal) == PRE_CLOSE


def test_stressed_allow_puts_profile_settings_and_safety():
    o2 = load_trading_config(O2_TOML)
    k20 = load_trading_config(K20_TOML)
    assert o2.stressed_allow_puts is True
    assert k20.stressed_allow_puts is False
    assert policy_errors(o2) == []
    assert policy_errors(k20) == []
    assert policy_errors(replace(o2, stressed_allow_puts=False)) == ["stressed_allow_puts"]
    assert policy_errors(replace(k20, stressed_allow_puts=True)) == ["stressed_allow_puts"]


def test_regime_trade_policy_allows_puts_under_stressed_only_when_enabled():
    down_stressed = {"status": "CALCULATED", "trend_state": "DOWN", "volatility_state": "STRESSED"}
    mixed_stressed = {"status": "CALCULATED", "trend_state": "MIXED", "volatility_state": "STRESSED"}
    up_stressed = {"status": "CALCULATED", "trend_state": "UP", "volatility_state": "STRESSED"}

    # Options 2 (allow_stressed_puts=True): PUT allowed on DOWN/MIXED, CALL blocked, UP blocks PUT directionally.
    assert regime_trade_policy(down_stressed, "PUT", allow_stressed_puts=True) == ("ALLOW", "REGIME_STRESSED_PUT_ALLOWED")
    assert regime_trade_policy(mixed_stressed, "PUT", allow_stressed_puts=True) == ("ALLOW", "REGIME_STRESSED_PUT_ALLOWED")
    assert regime_trade_policy(down_stressed, "CALL", allow_stressed_puts=True) == ("SUPPRESS", "REGIME_STRESSED")
    assert regime_trade_policy(mixed_stressed, "CALL", allow_stressed_puts=True) == ("SUPPRESS", "REGIME_STRESSED")
    assert regime_trade_policy(up_stressed, "PUT", allow_stressed_puts=True) == ("SUPPRESS", "REGIME_DIRECTION")
    assert regime_trade_policy(up_stressed, "CALL", allow_stressed_puts=True) == ("SUPPRESS", "REGIME_STRESSED")

    # 20K / default (allow_stressed_puts=False): STRESSED still blocks both PUT and CALL.
    assert regime_trade_policy(down_stressed, "PUT", allow_stressed_puts=False) == ("SUPPRESS", "REGIME_STRESSED")
    assert regime_trade_policy(down_stressed, "CALL", allow_stressed_puts=False) == ("SUPPRESS", "REGIME_STRESSED")
    assert regime_trade_policy(mixed_stressed, "PUT") == ("SUPPRESS", "REGIME_STRESSED")


def test_scan_under_stressed_regime_allows_o2_put_blocks_o2_call_and_blocks_20k_put():
    underlying, call_opt, news = sample_market()
    down_underlying = replace(underlying, price=Decimal("98.00"), change_percent=-2.5)
    put_opt = replace(
        call_opt,
        option_symbol="NVDA  261002P00100000",
        right=OptionRight.PUT,
        delta=Decimal("-0.45"),
    )
    regime = {"status": "CALCULATED", "trend_state": "DOWN", "volatility_state": "STRESSED"}

    o2_cfg = replace(loose_config(), stressed_allow_puts=True, min_events=1, allow_quant_entry=True)
    k20_cfg = replace(loose_config(), stressed_allow_puts=False, min_events=1, allow_quant_entry=True)

    # Options 2 PUT under STRESSED -> BUY with policy_trace["regime"] = REGIME_STRESSED_PUT_ALLOWED
    o2_put_rows = run_scan(
        [down_underlying], [put_opt], news, o2_cfg, StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("100000"), set(), regime=regime
    )
    o2_put = next(row for row in o2_put_rows if row.underlying == down_underlying.symbol)
    assert o2_put.decision is DecisionKind.BUY
    assert o2_put.policy_trace.get("regime") == "REGIME_STRESSED_PUT_ALLOWED"

    # Options 2 CALL under STRESSED -> SUPPRESS REGIME_STRESSED
    o2_call_rows = run_scan(
        [underlying], [call_opt], news, o2_cfg, StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("100000"), set(), regime=regime
    )
    o2_call = next(row for row in o2_call_rows if row.underlying == underlying.symbol)
    assert o2_call.decision is DecisionKind.SUPPRESS
    assert o2_call.suppress_reason == "REGIME_STRESSED"

    # 20K PUT under STRESSED -> SUPPRESS REGIME_STRESSED
    k20_put_rows = run_scan(
        [down_underlying], [put_opt], news, k20_cfg, StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("100000"), set(), regime=regime
    )
    k20_put = next(row for row in k20_put_rows if row.underlying == down_underlying.symbol)
    assert k20_put.decision is DecisionKind.SUPPRESS
    assert k20_put.suppress_reason == "REGIME_STRESSED"


def test_views_rules_release_notes_and_submit_path():
    o2_settings = Settings(app_env="local", trading_mode="PAPER", auth_required=False, trading_config_path=str(O2_TOML))
    k20_settings = Settings(app_env="local", trading_mode="PAPER", auth_required=False)

    assert public_stressed_regime(o2_settings.trading()) == {
        "rule": "STRESSED_ALLOW_PUTS",
        "enabled": True,
        "allowed_sides": ["PUT"],
        "blocked_sides": ["CALL"],
    }
    assert public_stressed_regime(k20_settings.trading()) == {
        "rule": "STRESSED_ALLOW_PUTS",
        "enabled": False,
        "allowed_sides": [],
        "blocked_sides": ["CALL", "PUT"],
    }

    o2_regime_rule = next(item for item in build_trading_rules(o2_settings)["active"]["sections"]["entry"] if item["id"] == "regime")
    k20_regime_rule = next(item for item in build_trading_rules(k20_settings)["active"]["sections"]["entry"] if item["id"] == "regime")
    assert "PUT מותר" in o2_regime_rule["value"]
    assert "חוסם CALL ו־PUT" in k20_regime_rule["value"]

    releases = json.loads((ROOT / "app" / "config" / "releases.json").read_text(encoding="utf-8"))["releases"]
    assert releases[0]["version"] == "2026.11.25.1"
    assert any("REGIME_STRESSED_PUT_ALLOWED" in line for line in releases[0]["changes"])

    assert "stressed_allow_puts" in inspect.getsource(loops._submit_autonomous_buys)
    assert "stressed_allow_puts" in inspect.getsource(pipeline.run_scan)
