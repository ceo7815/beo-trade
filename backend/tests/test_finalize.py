from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.broker.approval import approve_recommendation
from app.config.settings import TradingConfig
from app.integrations.probes import probe_redis
from app.market.regime import regime_from_context
from app.market.regime_policy import regime_trade_policy
from app.models.db import configure_database, init_db, session_scope
from app.paper_trading.engine import exit_signal
from app.quant.bars import atr, expected_move, vwap
from app.quant.volatility import historical_volatility, iv_percentile, iv_rank
from app.schemas.domain import OptionRight, OptionSnapshot, PaperFill, UnderlyingSnapshot
from app.config.settings import Settings

NOW = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)


def _config(**overrides) -> TradingConfig:
    return dc_replace(TradingConfig(), **overrides)


def _fill() -> PaperFill:
    return PaperFill("t1", "r1", "NVDA  261003C00100000", 1, Decimal("1.00"), NOW, iv_at_entry=Decimal("0.50"), delta_at_entry=Decimal("0.4"), underlying_at_entry=Decimal("100"))


def _option(bid: str, ask: str, when: datetime | None = None) -> OptionSnapshot:
    return OptionSnapshot("NVDA", "NVDA  261003C00100000", OptionRight.CALL, Decimal("100"), datetime(2026, 10, 3).date(), Decimal(bid), Decimal(ask), Decimal(bid), 800, 500, when or NOW, Decimal("0.5"), Decimal("0.4"), Decimal("0.01"), Decimal("-0.02"), Decimal("0.1"))


def _underlying(when: datetime | None = None) -> UnderlyingSnapshot:
    return UnderlyingSnapshot("NVDA", Decimal("100"), Decimal("99"), Decimal("101"), Decimal("98"), Decimal("100"), 1_000_000, Decimal("2"), Decimal("1"), Decimal("1"), Decimal("99"), when or NOW, True)


def test_redis_probe_never_asks_for_a_key():
    status, _latency, detail = probe_redis(Settings(app_env="local", trading_mode="PAPER", auth_required=False))
    assert status in {"connected", "disconnected", "error"}
    assert "API" not in detail
    assert "מפתח" not in detail


def test_regime_waits_without_quotes_and_calculates_with_them():
    waiting = regime_from_context({}, {"source": "fred", "series": [{"id": "FEDFUNDS", "value": 3.75, "date": "2026-09-01"}]})
    assert waiting["status"] == "WAITING_FOR_MARKET_DATA"
    assert waiting["market_regime"] is None
    assert waiting["macro"]["series"][0]["id"] == "FEDFUNDS"
    ready = regime_from_context(
        {
            "SPY": {"symbol": "SPY", "price": 500, "change_percent": 0.4},
            "QQQ": {"symbol": "QQQ", "price": 400, "change_percent": 0.5},
            "IWM": {"symbol": "IWM", "price": 200, "change_percent": 0.3},
            "VIX": {"symbol": "VIX", "price": 18, "change_percent": None},
        }
    )
    assert ready["status"] == "CALCULATED"
    assert ready["trend_state"] == "UP"
    assert ready["volatility_state"] == "CALM"
    assert ready["risk_state"] == "OPEN"
    assert ready["market_regime"] == "UP/CALM/OPEN"


def test_quant_helpers_do_not_invent_short_history():
    assert atr([1, 2], [1, 1], [1, 1]) is None
    assert vwap([10, 11], [0, 0]) is None
    assert expected_move(100, 0, 5) is None
    assert historical_volatility([10, 11]) is None
    assert iv_rank(1, [1]) is None
    assert iv_percentile(1, []) is None
    value = atr([10, 11, 12] + [12] * 12, [9, 10, 11] + [11] * 12, [9.5, 10.5, 11.5] + [11.5] * 12)
    assert value is not None and value > 0
    assert round(vwap([10, 20], [1, 1]), 2) == 15
    assert expected_move(100, 0.2, 365) == 20


def test_exit_rules(tmp_path):
    underlying = _underlying()
    config = _config()
    assert exit_signal(_fill(), _option("1.20", "1.25"), underlying, NOW, config, True) is None
    stopped = exit_signal(_fill(), _option("0.50", "0.55"), underlying, NOW + timedelta(minutes=10), config, True)
    assert stopped.reason == "STOP"
    assert exit_signal(_fill(), _option("1.00", "1.02"), underlying, NOW, config, False).reason == "SESSION_CLOSE"
    same_day = dc_replace(_option("1.00", "1.02"), expiration=NOW.date())
    assert exit_signal(_fill(), same_day, underlying, NOW + timedelta(minutes=91), config, True).reason == "TIME_STOP"
    runner = _fill()
    runner.max_favorable = Decimal("0.70")
    trailing = exit_signal(runner, _option("1.20", "1.24"), underlying, NOW + timedelta(minutes=10), config, True)
    assert trailing.reason == "TRAILING"
    wide = _option("1.00", "1.40")
    assert exit_signal(_fill(), wide, underlying, NOW, config, True, liquidity_hits=0) is None
    assert exit_signal(_fill(), wide, underlying, NOW, config, True, liquidity_hits=1).reason == "LIQUIDITY"
    killed = exit_signal(_fill(), _option("1.00", "1.02"), underlying, NOW, _config(kill_switch=True), True)
    assert killed.reason == "KILL_SWITCH"
    close = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
    near = close - timedelta(minutes=18)
    expiring = OptionSnapshot("NVDA", "NVDA  261001C00100000", OptionRight.CALL, Decimal("100"), close.date(), Decimal("1"), Decimal("1.02"), Decimal("1"), 800, 500, near, Decimal("0.5"), Decimal("0.4"), Decimal("0.01"), Decimal("-0.02"), Decimal("0.1"))
    assert exit_signal(_fill(), expiring, _underlying(near), near, config, True, close).reason == "EXPIRATION"


def test_approval_is_idempotent(tmp_path):
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "approve.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    from app.models.tables import RecommendationRow

    with session_scope() as session:
        session.add(
            RecommendationRow(
                id="rec-approve",
                decision="BUY",
                underlying="NVDA",
                underlying_price=100,
                option_symbol="NVDA251003C00100000",
                call_put="CALL",
                strike=100,
                expiration="2025-10-03",
                option_price=1,
                bid=1,
                ask=1.1,
                delta=0.4,
                gamma=0.01,
                theta=-0.02,
                vega=0.1,
                iv=0.4,
                volume=10,
                open_interest=10,
                max_entry_price=1.2,
                quantity=1,
                holding_window_min=5,
                holding_window_max=30,
                reason_codes=[],
                gate_results={"RISK": True},
                scenarios=[],
                quote_observed_at=NOW,
            )
        )
        session.commit()
        first = approve_recommendation(session, "rec-approve", NOW)
        second = approve_recommendation(session, "rec-approve", NOW)
        session.commit()
        assert first.id == second.id
        assert first.status == "APPROVED"
        assert first.trade_id == second.trade_id


def test_missing_vix_does_not_block_a_known_trend():
    regime = {"status": "CALCULATED", "trend_state": "MIXED", "volatility_state": "UNKNOWN"}
    assert regime_trade_policy(regime, "CALL") == ("ALLOW", "")
    stressed = {"status": "CALCULATED", "trend_state": "UP", "volatility_state": "STRESSED"}
    assert regime_trade_policy(stressed, "CALL") == ("SUPPRESS", "REGIME_STRESSED")
