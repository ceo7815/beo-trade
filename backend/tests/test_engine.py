from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.ai.budget import BudgetLedger, UsageEntry, make_ledger
from app.ai.client import extract_output_text, parse_decision
from app.ai.prompts import decision_json_schema
from app.config.settings import Settings, TradingConfig
from app.core.sessions import CLOSED, INTRADAY_SCAN, phase_at
from app.paper_trading.engine import close_paper, exit_signal, open_paper, pnl
from app.quant.black_scholes import greeks, price
from app.quant.volatility import historical_volatility, iv_rank
from app.recommendations.pipeline import LeakageError, run_scan
from app.schemas.domain import (
    DecisionKind,
    ModelOutput,
    NewsItem,
    OptionRight,
    OptionSnapshot,
    UnderlyingSnapshot,
)
from app.backtesting.engine import run_backtest
from app.backtesting.outcomes import evaluate_horizons
from app.config.settings import load_calendar


UTC = timezone.utc
AS_OF = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


class StubAI:
    calls = 0

    def analyze(self, packet, as_of):
        StubAI.calls += 1
        contract = packet["shortlist"][0]
        return ModelOutput(
            decision="BUY",
            option_symbol=contract["option_symbol"],
            call_put=contract["call_put"],
            thesis="תנועה חזקה אחרי אירוע שדווח בשני מקורות, עם נזילות שמאפשרת כניסה.",
            catalyst="שני מקורות דיווחו על אירוע מהותי בנכס.",
            risk="התנועה יכולה להתהפך, התנודתיות יכולה לרדת, והזמן שוחק את מחיר האופציה.",
            invalidation="אם הנכס חוזר אל מתחת למחיר הפתיחה והנפח נחלש.",
            reason_codes=["MOMENTUM", "NEWS_CATALYST", "LIQUIDITY"],
            raw={},
            model="stub",
            prompt_version="test",
            input_hash="",
        )


class RaisingAI:
    def analyze(self, packet, as_of):
        raise AssertionError("AI must not be called")


def loose_config() -> TradingConfig:
    return TradingConfig(
        min_option_volume=1,
        min_open_interest=1,
        max_bid_ask_spread=0.25,
        min_underlying_volume=1,
        max_expiration_days=5,
        min_delta=0.05,
        max_delta=0.95,
        min_gamma=0,
        max_theta_threshold=5,
        min_data_freshness_seconds=120,
        min_relative_volume=1,
        min_underlying_move_percent=0.5,
        min_option_price=0.01,
        max_option_price=100,
        entry_buffer_percent=0.08,
        max_risk_percent=1,
        max_capital_per_trade=5000,
        max_capital_per_trade_pct=1,
        max_total_open_exposure_pct=1,
        risk_per_trade_pct=1,
        normal_position_capital_pct=1,
        hard_position_capital_pct=1,
        max_aggregate_planned_risk_pct=1,
        max_sector_exposure_pct=1,
        min_events=2,
        news_max_age_seconds=86400,
    )


def ledger() -> BudgetLedger:
    return BudgetLedger(
        hard_limit=Decimal("200"),
        soft_budget=Decimal("150"),
        warning_budget=Decimal("150"),
        critical_budget=Decimal("180"),
        max_per_day=Decimal("20"),
        price_input=Decimal("2"),
        price_cached=Decimal("1"),
        price_output=Decimal("8"),
        max_input_tokens=1000,
        max_output_tokens=200,
    )


def sample_market():
    spot = 100.0
    strike = 100.0
    sigma = 0.8
    years = 2 / 365
    rate = 0.04
    right = OptionRight.CALL
    theoretical = price(right, spot, strike, years, rate, 0.0, sigma)
    greek = greeks(right, spot, strike, years, rate, 0.0, sigma)
    underlying = UnderlyingSnapshot(
        symbol="TEST",
        price=Decimal("100"),
        open=Decimal("98"),
        high=Decimal("100.2"),
        low=Decimal("97.5"),
        close=Decimal("100"),
        volume=2_000_000,
        relative_volume=Decimal("2.4"),
        change_percent=Decimal("1.8"),
        change_dollars=Decimal("1.8"),
        prior_close=Decimal("98.2"),
        observed_at=AS_OF,
        session_ok=True,
    )
    option = OptionSnapshot(
        underlying="TEST",
        option_symbol="TEST261002C00100000",
        right=right,
        strike=Decimal("100"),
        expiration=AS_OF.date(),
        bid=Decimal(str(round(theoretical - 0.05, 2))),
        ask=Decimal(str(round(theoretical + 0.05, 2))),
        last=Decimal(str(round(theoretical, 2))),
        volume=800,
        open_interest=1500,
        observed_at=AS_OF,
        implied_volatility=Decimal(str(sigma)),
        delta=Decimal(str(round(greek["delta"], 4))),
        gamma=Decimal(str(round(greek["gamma"], 4))),
        theta=Decimal(str(round(greek["theta"], 4))),
        vega=Decimal(str(round(greek["vega"], 4))),
    )
    news = [
        NewsItem(
            source="sec",
            url="https://example.test/a",
            headline="החברה עדכנה תחזית",
            published_at=AS_OF - timedelta(minutes=20),
            symbols=("TEST",),
            category="filing",
            event_type="guidance",
            importance=3,
            content="עדכון",
            source_credibility=Decimal("0.95"),
            observed_at=AS_OF - timedelta(minutes=20),
        ),
        NewsItem(
            source="wire",
            url="https://example.test/b",
            headline="השוק מגיב לעדכון",
            published_at=AS_OF - timedelta(minutes=12),
            symbols=("TEST",),
            category="news",
            event_type="reaction",
            importance=2,
            content="תגובה",
            source_credibility=Decimal("0.7"),
            observed_at=AS_OF - timedelta(minutes=12),
        ),
    ]
    return underlying, option, news


def test_call_price_and_parity():
    call = price(OptionRight.CALL, 100, 100, 1, 0.05, 0, 0.2)
    put = price(OptionRight.PUT, 100, 100, 1, 0.05, 0, 0.2)
    assert call == pytest.approx(10.4506, abs=0.001)
    parity = call - put
    expected = 100 - 100 * pow(2.718281828, -0.05)
    assert parity == pytest.approx(expected, abs=0.01)
    values = greeks(OptionRight.CALL, 100, 100, 0.25, 0.04, 0, 0.3)
    assert 0 < values["delta"] < 1
    assert values["gamma"] > 0
    assert values["theta"] < 0
    assert values["vega"] > 0


def test_iv_rank_and_history():
    history = historical_volatility([100, 101, 99, 102, 100, 103])
    assert history is not None and history > 0
    assert iv_rank(30, [10, 20, 40]) == pytest.approx(66.666, abs=0.01)


def test_buy_uses_code_prices_and_hides_nothing_required():
    StubAI.calls = 0
    underlying, option, news = sample_market()
    rows = run_scan(
        [underlying],
        [option],
        news,
        loose_config(),
        StubAI(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("1000"),
        set(),
    )
    buys = [row for row in rows if row.decision is DecisionKind.BUY]
    assert len(buys) == 1
    assert buys[0].risk
    assert buys[0].invalidation
    assert "בטוח" not in buys[0].thesis
    assert buys[0].quantity >= 1
    assert buys[0].max_entry_price >= buys[0].option_price
    assert StubAI.calls == 1


def test_wide_spread_never_calls_ai():
    underlying, option, news = sample_market()
    wide = OptionSnapshot(
        underlying=option.underlying,
        option_symbol=option.option_symbol,
        right=option.right,
        strike=option.strike,
        expiration=option.expiration,
        bid=Decimal("1.00"),
        ask=Decimal("2.00"),
        last=Decimal("1.40"),
        volume=option.volume,
        open_interest=option.open_interest,
        observed_at=option.observed_at,
        implied_volatility=option.implied_volatility,
        delta=option.delta,
        gamma=option.gamma,
        theta=option.theta,
        vega=option.vega,
    )
    StubAI.calls = 0
    rows = run_scan([underlying], [wide], news, loose_config(), RaisingAI(), ledger(), AS_OF, AS_OF.date(), Decimal("1000"), set())
    assert rows == []
    assert StubAI.calls == 0


def test_future_news_is_rejected():
    underlying, option, news = sample_market()
    future = NewsItem(
        source="sec",
        url="https://example.test/future",
        headline="עתיד",
        published_at=AS_OF + timedelta(minutes=5),
        symbols=("TEST",),
        category="filing",
        event_type="guidance",
        importance=3,
        content="",
        source_credibility=Decimal("0.95"),
        observed_at=AS_OF + timedelta(minutes=5),
    )
    with pytest.raises(LeakageError):
        run_scan([underlying], [option], [future], loose_config(), StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("1000"), set())


def test_backtest_does_not_see_later_news():
    underlying, option, news = sample_market()
    later = NewsItem(
        source="late",
        url="https://example.test/late",
        headline="מאוחר",
        published_at=AS_OF + timedelta(hours=2),
        symbols=("TEST",),
        category="news",
        event_type="late",
        importance=1,
        content="",
        source_credibility=Decimal("0.4"),
        observed_at=AS_OF + timedelta(hours=2),
    )
    seen = {}

    class Capture(StubAI):
        def analyze(self, packet, as_of):
            seen["headlines"] = [item["headline"] for item in packet["news"]]
            return super().analyze(packet, as_of)

    run_backtest([underlying], [option], news + [later], [AS_OF], loose_config(), Capture(), ledger(), Decimal("1000"))
    assert "מאוחר" not in seen["headlines"]


def test_budget_hard_stop_blocks_the_model():
    book = ledger()
    book.record(
        UsageEntry("decision", AS_OF, Decimal("199.50"), 10, 0, 10, "gpt-6-sol")
    )
    underlying, option, news = sample_market()
    rows = run_scan([underlying], [option], news, loose_config(), RaisingAI(), book, AS_OF, AS_OF.date(), Decimal("1000"), set())
    assert all(row.decision is DecisionKind.SUPPRESS for row in rows)


def test_unpriced_budget_blocks():
    settings = Settings(
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        app_env="local",
        auto_create_tables=True,
        auth_required=False,
    )
    book = make_ledger(settings)
    assert book.allow("decision", AS_OF) is False


def test_schema_is_strict():
    schema = decision_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    payload = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"decision":"SUPPRESS","underlying":"TEST","option_symbol":"X","call_put":"CALL","strike":1,"expiration":"2026-10-02","option_price":1,"max_entry_price":1,"holding_window_minutes":30,"thesis":"","catalyst":"","risk":"","invalidation":"","reason_codes":[],"confidence":0,"risk_factors":[],"holding_window":"","required_conditions":[],"data_quality":"FAIL"}',
                    }
                ],
            }
        ],
    }
    parsed = parse_decision(payload)
    assert parsed.decision == "SUPPRESS"
    assert extract_output_text(payload).startswith("{")


def test_paper_pnl_and_exit():
    underlying, option, news = sample_market()
    rows = run_scan([underlying], [option], news, loose_config(), StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("5000"), set())
    buy = next(row for row in rows if row.decision is DecisionKind.BUY)
    fill = open_paper(buy, loose_config(), AS_OF)
    assert fill is not None
    dollars, percent = pnl(fill.entry_price, fill.entry_price + Decimal("0.40"), fill.quantity, 100)
    assert dollars == Decimal("0.40") * fill.quantity * 100
    assert percent > 0
    later_option = OptionSnapshot(
        underlying=option.underlying,
        option_symbol=option.option_symbol,
        right=option.right,
        strike=option.strike,
        expiration=option.expiration,
        bid=fill.entry_price * Decimal("0.5"),
        ask=fill.entry_price * Decimal("0.55"),
        last=fill.entry_price * Decimal("0.5"),
        volume=10,
        open_interest=10,
        observed_at=AS_OF + timedelta(minutes=10),
        implied_volatility=option.implied_volatility,
        delta=option.delta,
        gamma=option.gamma,
        theta=option.theta,
        vega=option.vega,
    )
    signal = exit_signal(fill, later_option, underlying, AS_OF + timedelta(minutes=10), loose_config(), True)
    assert signal is not None and signal.reason == "STOP"
    close_paper(fill, signal)
    assert fill.exit_reason == "STOP"


def test_outcomes_ignore_ticks_after_the_horizon():
    entry = AS_OF
    ticks = [
        (entry + timedelta(minutes=5), Decimal("1.10")),
        (entry + timedelta(minutes=130), Decimal("9")),
    ]
    marks = evaluate_horizons(entry, Decimal("1"), ticks, 0.15)
    five = next(item for item in marks if item["horizon_minutes"] == 5)
    assert five["max_gain_percent"] == Decimal("0.1000")
    assert "9" not in str(five["max_gain_percent"])


def test_session_phase_uses_exchange_clock():
    calendar = load_calendar()
    moment = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    assert phase_at(moment, calendar) == INTRADAY_SCAN
    sunday = datetime(2026, 10, 4, 15, 0, tzinfo=UTC)
    assert phase_at(sunday, calendar) == CLOSED


def test_production_settings_refuse_open_auth():
    with pytest.raises(ValueError):
        Settings(app_env="production", auth_required=False, database_url="postgresql://db", auto_create_tables=False, supabase_jwt_secret="x")
