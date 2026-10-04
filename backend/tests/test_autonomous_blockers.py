from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.ai.lock import claim_fingerprint
from app.ai.telemetry import store_ai_request, telemetry_from_response
from app.config.settings import TradingConfig
from app.models.db import configure_database, init_db, session_scope
from app.models.tables import AIRequestLog
from app.options.risk_limits import daily_loss_reason, sector_exposure_reason
from app.paper_trading.engine import exit_signal
from app.recommendations.pipeline import run_scan
from app.schemas.domain import DecisionKind, OptionRight, OptionSnapshot
from tests.test_broker import NOW, SYMBOL, _adapter, _fresh_quotes, _recommendation, _settings
from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market
from tests.test_finalize import NOW as MARKET_NOW
from tests.test_finalize import _option, _underlying


def test_daily_loss_limit_blocks_buy():
    config = loose_config()
    equity = Decimal("1000")
    assert daily_loss_reason(equity, Decimal("-39"), config) is None
    assert daily_loss_reason(equity, Decimal("-40"), config) == "DAILY_LOSS_LIMIT_REACHED"
    assert daily_loss_reason(equity, Decimal("-50"), config) == "DAILY_HARD_STOP"
    underlying, option, news = sample_market()
    rows = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        equity,
        set(),
        daily_pnl=Decimal("-40"),
    )
    assert rows
    assert rows[0].decision is DecisionKind.SUPPRESS
    assert rows[0].suppress_reason == "DAILY_LOSS_LIMIT_REACHED"


def test_sector_exposure_blocks_buy():
    from dataclasses import replace

    config = replace(loose_config(), max_sector_exposure_pct=0.15)
    equity = Decimal("1000")
    added = Decimal("160")
    assert sector_exposure_reason(equity, "TECH", {"TECH": Decimal("0")}, added, config) == "SECTOR_EXPOSURE_LIMIT"
    underlying, option, news = sample_market()
    rows = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        equity,
        set(),
        sector_book={"UNCLASSIFIED": Decimal("140")},
        sector_of={"TEST": "UNCLASSIFIED"},
    )
    assert rows[0].decision is DecisionKind.SUPPRESS
    assert rows[0].suppress_reason == "SECTOR_EXPOSURE_LIMIT"


def test_session_close_exit_uses_real_clock(tmp_path):
    from app.config.settings import Settings
    from app.workers.loops import monitor_once

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "monitor.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    option = _option("1.00", "1.02")
    symbol = option.option_symbol.replace(" ", "")

    class Adapter:
        def sync_positions(self):
            return [{"symbol": symbol, "qty": "1", "avg_entry_price": "1.00", "trade_id": "t-session"}]

        def get_market_clock(self):
            return {"is_open": False}

        def close(self):
            return None

    result = monitor_once(
        settings,
        {symbol: {"option": option, "underlying": _underlying()}},
        Adapter(),
        MARKET_NOW,
        submit=False,
    )
    assert result["exits"][0]["reason"] == "SESSION_CLOSE"


def test_fresh_revalidation_rejects_a_stale_quote():
    from app.broker.execution import fresh_quote_reasons

    settings = _settings_open()
    stale = _fresh_quotes()[0]
    stale = OptionSnapshot(
        stale.underlying,
        stale.option_symbol,
        stale.right,
        stale.strike,
        stale.expiration,
        stale.bid,
        stale.ask,
        stale.last,
        stale.volume,
        stale.open_interest,
        NOW - timedelta(seconds=120),
        Decimal("0.4"),
        Decimal("0.4"),
        Decimal("0.01"),
        Decimal("-0.02"),
        Decimal("0.1"),
    )
    reasons = fresh_quote_reasons(
        settings,
        SYMBOL,
        "buy_to_open",
        NOW,
        [stale],
        {"news_fresh": True, "regime_allowed": True, "session_open": True, "exposure_ok": True, "daily_loss_ok": True, "sector_ok": True, "reference_price": "1.11"},
    )
    assert "הציטוט אינו טרי" in reasons


def test_two_workers_cannot_claim_the_same_fingerprint(tmp_path):
    settings = _settings(tmp_path)
    moment = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
    with session_scope() as session:
        assert claim_fingerprint(session, "abc", moment, 900) is True
    with session_scope() as session:
        assert claim_fingerprint(session, "abc", moment, 900) is False


def test_autonomous_buy_does_not_need_user_approval(tmp_path):
    from app.broker.execution import place_order
    from app.integrations.alpaca import PaperOnlyError

    settings = _settings(tmp_path)
    state = {"posts": 0}
    quote = _quoted()
    with session_scope() as session:
        session.add(_recommendation())
        session.commit()
        result = place_order(
            settings,
            session,
            _adapter(state),
            {
                "symbol": SYMBOL,
                "qty": 1,
                "limit_price": "1.20",
                "position_intent": "buy_to_open",
                "approved": False,
                "system_validated": True,
                "recommendation_id": "rec-1",
                "trade_id": "auto-1",
                "revalidation": _context("1.11"),
            },
            NOW,
            [quote],
        )
        session.commit()
    assert state["posts"] == 1
    assert result["duplicate"] is False
    with pytest.raises(PaperOnlyError):
        with session_scope() as session:
            place_order(
                settings,
                session,
                _adapter({"posts": 0}),
                {
                    "symbol": SYMBOL,
                    "qty": 50,
                    "limit_price": "1.20",
                    "position_intent": "buy_to_open",
                    "approved": False,
                    "system_validated": True,
                    "recommendation_id": "rec-1",
                    "trade_id": "auto-big",
                    "revalidation": _context("1.11"),
                },
                NOW,
                [quote],
            )


def test_expiration_protection_for_zero_dte():
    close = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
    now = close - timedelta(minutes=18)
    option = OptionSnapshot(
        "NVDA",
        "NVDA  261001C00100000",
        OptionRight.CALL,
        Decimal("100"),
        close.date(),
        Decimal("1"),
        Decimal("1.02"),
        Decimal("1"),
        100,
        100,
        now,
        Decimal("0.5"),
        Decimal("0.4"),
        Decimal("0.01"),
        Decimal("-0.02"),
        Decimal("0.1"),
    )
    signal = exit_signal(_fill_at(now), option, _underlying(now), now, TradingConfig(), True, close)
    assert signal is not None
    assert signal.reason == "EXPIRATION"


def test_ai_usage_ledger_keeps_missing_fields_null(tmp_path):
    settings = _settings(tmp_path)
    payload = {"id": "resp_123", "usage": {"input_tokens": 20, "output_tokens": 5}}
    measured = telemetry_from_response(payload, 42)
    assert measured["request_id"] == "resp_123"
    assert measured["reasoning_tokens"] is None
    assert measured["cached_tokens"] is None
    assert measured["cache_hit"] is None
    assert measured["input_tokens"] == 20
    moment = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
    with session_scope() as session:
        store_ai_request(
            session,
            {
                **measured,
                "scan_id": "scan-1",
                "candidate_id": "NVDA",
                "candidate_fingerprint": "abc",
                "model": "gpt-6.1-sol",
                "decision": "SUPPRESS",
                "estimated_cost": None,
            },
            moment,
        )
        session.commit()
        row = session.query(AIRequestLog).one()
    assert row.reasoning_tokens is None
    assert row.cached_tokens is None
    assert row.cache_hit is None
    assert row.latency_ms == 42
    assert row.input_tokens == 20
    assert row.estimated_cost is None
    assert settings.trading_mode == "PAPER"


def _settings_open():
    from app.config.settings import Settings

    return Settings(app_env="local", trading_mode="PAPER", auth_required=False)


def _quoted() -> OptionSnapshot:
    base = _fresh_quotes()[0]
    return OptionSnapshot(
        base.underlying,
        base.option_symbol,
        base.right,
        base.strike,
        base.expiration,
        base.bid,
        base.ask,
        base.last,
        base.volume,
        base.open_interest,
        base.observed_at,
        Decimal("0.45"),
        Decimal("0.4"),
        Decimal("0.02"),
        Decimal("-0.04"),
        Decimal("0.08"),
    )


def _context(price: str) -> dict:
    return {
        "news_fresh": True,
        "regime_allowed": True,
        "session_open": True,
        "exposure_ok": True,
        "daily_loss_ok": True,
        "sector_ok": True,
        "reference_price": price,
    }


def _fill_at(moment: datetime):
    from app.schemas.domain import PaperFill

    return PaperFill("t1", "r1", "NVDA  261001C00100000", 1, Decimal("1.00"), moment, delta_at_entry=Decimal("0.4"), underlying_at_entry=Decimal("100"))
