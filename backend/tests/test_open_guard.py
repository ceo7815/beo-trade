"""Opening-session gap guards. Paper entries only. The clock is America/New_York."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
import structlog
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
import app.positions.open_guard as open_guard
from app.positions.open_guard import (
    GAP_DATA_MISSING,
    GAP_SYMBOL,
    MIN_GAP,
    OPEN_GAP_RETRACE,
    OPEN_NO_FADE,
    RETRACE_PCT,
    WINDOW_END,
    GapReading,
    MinuteBar,
    commit,
    entry_block,
    fold,
    guard_for,
    in_fade_window,
    in_retrace_window,
    public_open_guard,
    reading_from_raw,
    refresh_open_guard,
    reset_open_guard,
    retracement,
)
from app.providers.thetadata import ThetaFeed

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.positions.open_guard.store_path", lambda: tmp_path / "open_guard.json")
    reset_open_guard()
    yield
    reset_open_guard()


def _ny(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=NY)


def _down() -> GapReading:
    """Gap down 2%: open 98, prior close 100."""
    return GapReading(Decimal("98"), Decimal("100"))


def _up() -> GapReading:
    """Gap up 2%: open 102, prior close 100."""
    return GapReading(Decimal("102"), Decimal("100"))


def _bar(hour: int, minute: int, close: str, opened: str = "98") -> MinuteBar:
    return MinuteBar(time(hour, minute), Decimal(opened), Decimal(close))


def test_constants_are_the_measured_rule_and_not_an_env_switch():
    assert RETRACE_PCT == Decimal("0.50")
    assert MIN_GAP == Decimal("0")
    assert WINDOW_END == time(10, 10)
    assert GAP_SYMBOL == "QQQ"
    text = open(open_guard.__file__, encoding="utf-8").read()
    assert "OPEN_NO_FADE_ENABLED" not in text
    assert "OPEN_GAP_RETRACE_ENABLED" not in text


def test_gap_down_blocks_the_fade_and_allows_the_put():
    moment = _ny(2026, 10, 15, 9, 40)
    state = fold(None, moment, _down())
    call = entry_block("CALL", "NVDA", moment, state)
    assert call is not None and call.startswith(OPEN_NO_FADE)
    assert "gap=-2.00%" in call
    assert "NVDA" in call and call.endswith("CALL")
    assert len(call) <= 80
    assert entry_block("PUT", "NVDA", moment, state) is None
    assert entry_block("call", "NVDA", moment, state).startswith(OPEN_NO_FADE)


def test_gap_up_blocks_the_fade_and_allows_the_call():
    moment = _ny(2026, 10, 15, 9, 31)
    state = fold(None, moment, _up())
    put = entry_block("PUT", "MSTR", moment, state)
    assert put is not None and put.startswith(OPEN_NO_FADE)
    assert "gap=2.00%" in put and "MSTR" in put and put.endswith("PUT")
    assert entry_block("CALL", "MSTR", moment, state) is None


def test_entries_at_and_after_the_window_end_are_allowed():
    state = fold(None, _ny(2026, 10, 15, 9, 40), _up())
    assert entry_block("PUT", "AMD", _ny(2026, 10, 15, 10, 9, 59), state).startswith(OPEN_NO_FADE)
    assert entry_block("PUT", "AMD", _ny(2026, 10, 15, 10, 10, 0), state) is None
    assert entry_block("CALL", "AMD", _ny(2026, 10, 15, 9, 29, 59), state) is None
    # Winter clock, same rule.
    winter = fold(None, _ny(2026, 11, 12, 9, 40), _down())
    assert entry_block("CALL", "SMCI", _ny(2026, 11, 12, 10, 9, 59), winter).startswith(OPEN_NO_FADE)
    assert entry_block("CALL", "SMCI", _ny(2026, 11, 12, 10, 10, 0), winter) is None


def test_retrace_at_0945_blocks_every_new_buy_including_with_the_gap():
    moment = _ny(2026, 10, 15, 9, 45)
    # Halfway back to 100 is exactly 50%.
    state = fold(None, moment, GapReading(Decimal("98"), Decimal("100"), (_bar(9, 45, "99"),)))
    assert state["latched"] is True
    assert state["retracement_pct"] == "50.00"
    assert retracement(Decimal("99"), Decimal("98"), Decimal("100")) == Decimal("0.5")
    assert entry_block("PUT", "COIN", moment, state).startswith(OPEN_GAP_RETRACE)
    assert entry_block("CALL", "COIN", moment, state).startswith(OPEN_GAP_RETRACE)
    assert "retrace=50.00%" in entry_block("PUT", "COIN", moment, state)


def test_a_retrace_below_half_does_not_block_the_with_gap_side():
    moment = _ny(2026, 10, 15, 9, 50)
    # (98.9 - 98) / (100 - 98) = 0.45
    state = fold(None, moment, GapReading(Decimal("98"), Decimal("100"), (_bar(9, 45, "98.9"),)))
    assert state["latched"] is False
    assert entry_block("PUT", "DELL", moment, state) is None
    assert entry_block("CALL", "DELL", moment, state).startswith(OPEN_NO_FADE)


def test_the_retrace_is_not_applied_before_0945():
    early = _ny(2026, 10, 15, 9, 44, 59)
    state = fold(None, early, GapReading(Decimal("98"), Decimal("100"), (_bar(9, 44, "99"),)))
    assert state["latched"] is False
    assert state["price_0945"] is None
    assert not in_retrace_window(early)
    assert entry_block("PUT", "NVDA", early, state) is None
    assert entry_block("CALL", "NVDA", early, state).startswith(OPEN_NO_FADE)


def test_bar_at_or_before_0945_is_the_fifteen_minute_price():
    moment = _ny(2026, 11, 12, 9, 50)
    # No 09:45 bar: the 09:44 close is the reading, matching analyze_first40.
    only_earlier = fold(None, moment, GapReading(Decimal("98"), Decimal("100"), (_bar(9, 30, "98.2"), _bar(9, 44, "99"))))
    assert only_earlier["price_0945"] == "99"
    assert only_earlier["price_source"] == "bar"
    assert only_earlier["latched"] is True
    # The 09:45 bar wins over 09:44 when both exist. 98.4 is a 20% retrace.
    both = fold(
        None,
        moment,
        GapReading(Decimal("98"), Decimal("100"), (_bar(9, 44, "99"), _bar(9, 45, "98.4"), _bar(9, 46, "99.5"))),
    )
    assert both["price_0945"] == "98.4"
    assert both["latched"] is False


def test_a_live_trade_after_0946_is_not_the_0945_price():
    moment = _ny(2026, 10, 15, 10, 5)
    state = fold(None, moment, GapReading(Decimal("98"), Decimal("100"), (), Decimal("99"), time(10, 5)))
    assert state["price_0945"] is None
    assert state["latched"] is False
    assert entry_block("PUT", "NVDA", moment, state) is None
    inside = fold(None, moment, GapReading(Decimal("98"), Decimal("100"), (), Decimal("99"), time(9, 45, 30)))
    assert inside["price_source"] == "trade"
    assert inside["latched"] is True


def test_once_triggered_the_retrace_stays_for_the_window():
    first = _ny(2026, 10, 15, 9, 45, 20)
    latched = fold(None, first, GapReading(Decimal("102"), Decimal("100"), (), Decimal("101"), time(9, 45, 20)))
    assert latched["latched"] is True
    # The later 09:45 bar would have been only a 20% retrace. The latch stays.
    later = fold(latched, _ny(2026, 10, 15, 9, 50), GapReading(Decimal("102"), Decimal("100"), (_bar(9, 45, "101.6", "102"),)))
    assert later["latched"] is True
    assert later["price_0945"] == "101"
    moment = _ny(2026, 10, 15, 10, 0)
    assert entry_block("CALL", "AMD", moment, later).startswith(OPEN_GAP_RETRACE)
    assert entry_block("PUT", "AMD", moment, later).startswith(OPEN_GAP_RETRACE)
    assert entry_block("CALL", "AMD", _ny(2026, 10, 15, 10, 10), later) is None


def test_missing_open_or_prior_close_fails_open_and_logs():
    moment = _ny(2026, 11, 12, 9, 35)
    with structlog.testing.capture_logs() as logs:
        missing_open = commit(moment, GapReading(None, Decimal("100")))
        reset_open_guard()
        open_guard.store_path().unlink(missing_ok=True)
        missing_prior = commit(moment, GapReading(Decimal("98"), None))
    assert entry_block("CALL", "NVDA", moment, missing_open) is None
    assert entry_block("PUT", "NVDA", moment, missing_prior) is None
    assert missing_open["data_missing"] is True
    assert missing_prior["data_missing"] is True
    missed = [row for row in logs if row.get("event") == "entry.gap_data_missing"]
    assert missed and all(row.get("code") == GAP_DATA_MISSING for row in missed)
    assert public_open_guard(moment)["status"] == GAP_DATA_MISSING


def test_a_flat_gap_does_not_block_either_side():
    moment = _ny(2026, 10, 15, 9, 50)
    state = fold(None, moment, GapReading(Decimal("100"), Decimal("100"), (_bar(9, 45, "90", "100"),)))
    assert state["latched"] is False
    assert entry_block("CALL", "QQQ", moment, state) is None
    assert entry_block("PUT", "QQQ", moment, state) is None


def test_dst_uses_new_york_not_a_fixed_utc_offset():
    assert _ny(2026, 10, 31, 9, 30).utcoffset() == timedelta(hours=-4)
    assert _ny(2026, 11, 1, 9, 30).utcoffset() == timedelta(hours=-5)
    # 13:30 UTC is 09:30 EDT in October and 08:30 EST in November.
    assert in_fade_window(datetime(2026, 10, 31, 13, 30, tzinfo=UTC))
    assert not in_fade_window(datetime(2026, 11, 12, 13, 30, tzinfo=UTC))
    assert in_fade_window(datetime(2026, 11, 12, 14, 30, tzinfo=UTC))
    assert in_retrace_window(datetime(2026, 11, 1, 14, 45, tzinfo=UTC))
    assert not in_retrace_window(datetime(2026, 11, 1, 13, 45, tzinfo=UTC))
    # 10:09:59 EST is 15:09:59 UTC. 10:10:00 EST is 15:10:00 UTC.
    winter_state = fold(None, datetime(2026, 11, 12, 14, 40, tzinfo=UTC), _up())
    assert entry_block("PUT", "IWM", datetime(2026, 11, 12, 15, 9, 59, tzinfo=UTC), winter_state).startswith(OPEN_NO_FADE)
    assert entry_block("PUT", "IWM", datetime(2026, 11, 12, 15, 10, tzinfo=UTC), winter_state) is None
    summer_state = fold(None, datetime(2026, 10, 15, 13, 40, tzinfo=UTC), _down())
    assert entry_block("CALL", "IWM", datetime(2026, 10, 15, 14, 9, 59, tzinfo=UTC), summer_state).startswith(OPEN_NO_FADE)
    assert entry_block("CALL", "IWM", datetime(2026, 10, 15, 14, 10, tzinfo=UTC), summer_state) is None


def test_the_latch_survives_a_restart_and_a_later_miss():
    moment = _ny(2026, 11, 12, 9, 46)
    commit(moment, GapReading(Decimal("98"), Decimal("100"), (_bar(9, 45, "99"),)))
    reset_open_guard()
    restored = guard_for(moment)
    assert restored["latched"] is True
    assert restored["open"] == "98"
    # A later empty read must not forget the open or the latch.
    kept = commit(moment, GapReading())
    assert kept["latched"] is True
    assert kept["data_missing"] is False
    assert entry_block("PUT", "SMCI", moment, kept).startswith(OPEN_GAP_RETRACE)
    shown = public_open_guard(moment)
    assert shown["symbol"] == "QQQ"
    assert shown["gap_pct"] == "-2.0000"
    assert shown["retrace_blocking"] is True
    assert shown["blocked_sides"] == ["CALL", "PUT"]
    assert shown["status"] == OPEN_GAP_RETRACE


def test_refresh_asks_the_feed_only_inside_the_opening_window():
    calls = {"n": 0}

    class Feed:
        def session_gap(self, symbol, as_of):
            calls["n"] += 1
            assert symbol == "QQQ"
            return {"open": "98", "prior_close": "100", "bars": [], "live_price": None, "live_time": None}

    class Providers:
        market = type("Market", (), {"feed": Feed()})()

    afternoon = _ny(2026, 10, 15, 11, 0)
    assert refresh_open_guard(afternoon, Providers())["data_missing"] is True
    assert calls["n"] == 0
    morning = _ny(2026, 10, 15, 9, 35)
    state = refresh_open_guard(morning, Providers())
    assert calls["n"] == 1
    assert state["data_missing"] is False
    assert entry_block("CALL", "NVDA", morning, state).startswith(OPEN_NO_FADE)
    # Open and prior are already stored, and it is still before 09:45, so the feed stays quiet.
    refresh_open_guard(morning, Providers())
    assert calls["n"] == 1


def test_theta_minutes_feed_the_guard_and_a_dead_feed_fails_open():
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v3/stock/snapshot/ohlc":
            return httpx.Response(200, json=[{"symbol": "QQQ", "timestamp": "2026-11-12T09:30:00.000", "open": "680.26", "close": "686"}])
        if path == "/v3/stock/snapshot/trade":
            return httpx.Response(200, json=[{"symbol": "QQQ", "timestamp": "2026-11-12T09:45:30.000", "price": "686.10"}])
        if path == "/v3/stock/history/eod":
            return httpx.Response(200, json=[{"symbol": "QQQ", "last_trade": "2026-11-11T16:00:00.000", "close": "690.40", "volume": 1}])
        if path == "/v3/stock/history/ohlc":
            return httpx.Response(
                200,
                json=[
                    {"symbol": "QQQ", "timestamp": "2026-11-12T09:30:00.000", "open": "680.26", "close": "681"},
                    {"symbol": "QQQ", "timestamp": "2026-11-12T09:45:00.000", "open": "685", "close": "686.10"},
                ],
            )
        return httpx.Response(500, json={"error": "down"})

    moment = datetime(2026, 11, 12, 14, 50, tzinfo=UTC)  # 09:50 EST
    feed = ThetaFeed(Settings(app_env="local", trading_mode="PAPER", thetadata_api_key="test-terminal"), transport=httpx.MockTransport(handle))
    raw = feed.session_gap("QQQ", moment)
    state = commit(moment, reading_from_raw(raw))
    assert state["open"] == "680.26"
    assert state["prior_close"] == "690.40"
    assert state["price_source"] == "bar"
    assert state["price_0945"] == "686.10"
    assert state["latched"] is True
    blocked = entry_block("PUT", "MSTR", moment, state)
    assert blocked.startswith(OPEN_GAP_RETRACE)
    assert "MSTR" in blocked and "PUT" in blocked

    def down(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="no")

    reset_open_guard()
    open_guard.store_path().unlink(missing_ok=True)
    dead = ThetaFeed(Settings(app_env="local", trading_mode="PAPER", thetadata_api_key="test-terminal"), transport=httpx.MockTransport(down))
    with structlog.testing.capture_logs() as logs:
        empty = commit(_ny(2026, 11, 12, 9, 36), reading_from_raw(dead.session_gap("QQQ", _ny(2026, 11, 12, 9, 36))))
    assert empty["data_missing"] is True
    assert entry_block("CALL", "NVDA", _ny(2026, 11, 12, 9, 36), empty) is None
    assert any(row.get("code") == GAP_DATA_MISSING for row in logs)


def test_rules_book_and_the_risk_payload_show_the_guard(tmp_path):
    from app.analytics.trading_rules import build_trading_rules

    entry = build_trading_rules(Settings(app_env="local", trading_mode="PAPER", auth_required=False))["active"]["sections"]["entry"]
    row = next(item for item in entry if item["id"] == "open_guard")
    assert OPEN_NO_FADE in row["explain"]
    assert OPEN_GAP_RETRACE in row["explain"]
    assert GAP_DATA_MISSING in row["explain"]

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "beo.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    with TestClient(create_app(settings, allow_open_api=True)) as client:
        risk = client.get("/api/v1/risk")
        assert risk.status_code == 200
        guard = risk.json()["open_guard"]
        assert guard["symbol"] == "QQQ"
        assert guard["window_end"] == "10:10"
        assert guard["retrace_from"] == "09:45"
        assert guard["retrace_pct"] == "50.00"
        system = client.get("/api/v1/system")
        assert system.status_code == 200
        assert system.json()["open_guard"]["symbol"] == "QQQ"


def test_scan_records_the_block_on_the_recommendation():
    from app.recommendations.pipeline import run_scan
    from app.schemas.domain import DecisionKind, OptionRight
    from tests.test_engine import StubAI, ledger, loose_config, sample_market

    underlying, option, news = sample_market()
    # 09:40 EDT on the sample day. News in the sample is later, so it is moved back.
    moment = datetime(2026, 10, 1, 13, 40, tzinfo=UTC)
    after = datetime(2026, 10, 1, 14, 5, tzinfo=UTC)  # 10:05 EDT
    past = datetime(2026, 10, 1, 14, 20, tzinfo=UTC)  # 10:20 EDT
    news = [replace(item, published_at=moment - timedelta(minutes=15), observed_at=moment - timedelta(minutes=15), symbols=("TEST",)) for item in news]
    down_stock = replace(
        underlying,
        change_percent=Decimal("-1.5"),
        price=Decimal("98"),
        open=Decimal("99"),
        high=Decimal("99.4"),
        observed_at=moment,
    )
    put = replace(option, right=OptionRight.PUT, delta=-abs(option.delta), observed_at=moment)
    gap_up = fold(None, moment, _up())
    blocked = run_scan([down_stock], [put], news, loose_config(), StubAI(), ledger(), moment, moment.date(), Decimal("100000"), set(), open_guard=gap_up)
    assert blocked[0].decision is DecisionKind.SUPPRESS
    assert blocked[0].suppress_reason.startswith(OPEN_NO_FADE)
    assert blocked[0].policy_trace["open_guard"] == blocked[0].suppress_reason
    assert "TEST" in blocked[0].suppress_reason and blocked[0].suppress_reason.endswith("PUT")

    up_stock = replace(down_stock, change_percent=Decimal("1.2"), price=Decimal("101"), open=Decimal("100"), high=Decimal("103"), observed_at=after)
    call = replace(option, right=OptionRight.CALL, observed_at=after)
    gap_down = fold(None, after, _down())
    fade = run_scan([up_stock], [call], news, loose_config(), StubAI(), ledger(), after, after.date(), Decimal("100000"), set(), open_guard=gap_down)
    assert fade[0].suppress_reason.startswith(OPEN_NO_FADE)
    assert fade[0].suppress_reason.endswith("CALL")

    allowed = run_scan([down_stock], [put], news, loose_config(), StubAI(), ledger(), moment, moment.date(), Decimal("100000"), set(), open_guard=fold(None, moment, _down()))
    assert allowed[0].decision is DecisionKind.BUY
    assert allowed[0].suppress_reason == ""

    later_stock = replace(down_stock, observed_at=past)
    later_put = replace(put, observed_at=past)
    opened = run_scan([later_stock], [later_put], news, loose_config(), StubAI(), ledger(), past, past.date(), Decimal("100000"), set(), open_guard=gap_up)
    assert opened[0].decision is DecisionKind.BUY
