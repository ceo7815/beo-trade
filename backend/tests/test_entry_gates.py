"""ADD_BRAKE and CHASE_ATR. New buys only. The kill, the floor, and exits stay put."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import structlog
from fastapi.testclient import TestClient

from app.capital import effective_kill_usd
from app.config.settings import Settings, load_trading_config
from app.positions.entry_gates import (
    ADD_BRAKE,
    ADD_BRAKE_FRACTION,
    CHASE_ATR,
    CHASE_ATR_LIMIT,
    add_brake_reason,
    atr14,
    brake_threshold,
    chase_reading,
    directional_move,
    public_add_brake,
    public_chase,
)
from app.quant.bars import atr
from app.schemas.domain import Bar
from app.workers.loops import _submit_autonomous_buys, close_all_positions, monitor_once

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NY_OPEN = datetime(2026, 10, 1, 13, 30, tzinfo=UTC)
SESSION = NY_OPEN.date()


def _bars(count: int, *, high: str = "101", low: str = "99", close: str = "100", start: datetime | None = None) -> list[Bar]:
    stamp = start or datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    rows = []
    for index in range(count):
        moment = stamp + timedelta(days=index)
        rows.append(Bar(moment, Decimal(close), Decimal(high), Decimal(low), Decimal(close), 1_000))
    return rows


def test_options2_brake_is_a_quarter_of_the_1400_kill_and_the_edges_hold():
    cfg = load_trading_config(ROOT / "app" / "config" / "trading_v2610_40k.toml")
    kill = effective_kill_usd(cfg, Decimal("40000"), open_equity=Decimal("39000"))
    assert kill == Decimal("1400")
    assert brake_threshold(kill) == Decimal("-350")
    assert ADD_BRAKE_FRACTION == Decimal("0.25")
    on_the_line = add_brake_reason(Decimal("-350"), kill)
    assert on_the_line is not None and on_the_line.startswith(ADD_BRAKE)
    assert "day_pnl=-350" in on_the_line and "threshold=-350" in on_the_line
    assert add_brake_reason(Decimal("-350.01"), kill).startswith(ADD_BRAKE)
    assert add_brake_reason(Decimal("-349.99"), kill) is None
    assert add_brake_reason(Decimal("0"), kill) is None
    assert add_brake_reason(Decimal("10"), kill) is None


def test_20k_brake_uses_the_locked_kill_not_the_shrunk_equity():
    cfg = load_trading_config()
    assert cfg.capital_profile == "20k_ils"
    # 5,782.11 × 3500/30000 = 674.58. A quarter of that kill is 168.645.
    opened = Decimal("5782.11")
    shrunk = Decimal("5000")
    kill = effective_kill_usd(cfg, shrunk, open_equity=opened)
    assert kill == Decimal("674.58")
    assert brake_threshold(kill) == Decimal("-168.645")
    blocked = add_brake_reason(Decimal("-168.645"), kill)
    assert blocked is not None and "day_pnl=-168.645" in blocked and "threshold=-168.645" in blocked
    assert add_brake_reason(Decimal("-168.644"), kill) is None
    # 25% of a kill locked to the shrunk $5,000 book is about −$145.83.
    # The same −$150 must not brake against the locked $674.58 kill.
    live_kill = effective_kill_usd(cfg, shrunk)
    assert add_brake_reason(Decimal("-150"), live_kill) is not None
    assert add_brake_reason(Decimal("-150"), kill) is None


def test_the_brake_lifts_when_pnl_recovers_and_missing_data_does_not_block():
    kill = Decimal("1400")
    assert add_brake_reason(Decimal("-400"), kill).startswith(ADD_BRAKE)
    assert add_brake_reason(Decimal("-350"), kill).startswith(ADD_BRAKE)
    assert add_brake_reason(Decimal("-349.99"), kill) is None
    assert add_brake_reason(Decimal("25"), kill) is None
    assert add_brake_reason(None, kill) is None
    assert add_brake_reason(Decimal("-400"), None) is None
    assert add_brake_reason(Decimal("-400"), 0) is None
    assert add_brake_reason("", kill) is None
    view = public_add_brake(Decimal("-400"), kill)
    assert view["blocking"] is True
    assert view["lifts_when_recovered"] is True
    assert public_add_brake(Decimal("-100"), kill)["blocking"] is False
    assert public_add_brake(None, kill)["data_missing"] is True
    assert public_add_brake(None, kill)["blocking"] is False


def test_chase_is_symmetric_and_the_limit_is_exclusive():
    width = Decimal("10")
    opened = Decimal("100")
    # Exactly 0.65 stays eligible. One cent of price past it does not.
    assert directional_move("CALL", Decimal("106.5"), opened, width) == Decimal("0.65")
    assert directional_move("PUT", Decimal("93.5"), opened, width) == Decimal("0.65")
    assert chase_reading("CALL", Decimal("106.5"), opened, width, symbol="NVDA")["chase_blocked"] is False
    assert chase_reading("PUT", Decimal("93.5"), opened, width, symbol="NVDA")["reason"] is None
    call = chase_reading("CALL", Decimal("106.51"), opened, width, symbol="NVDA")
    put = chase_reading("PUT", Decimal("93.49"), opened, width, symbol="MSTR")
    assert call["reason"].startswith(CHASE_ATR)
    assert put["reason"].startswith(CHASE_ATR)
    assert call["move_atr"] == put["move_atr"]
    assert "move_atr=" in call["reason"]
    # The other direction is not a chase.
    assert chase_reading("CALL", Decimal("90"), opened, width, symbol="NVDA")["chase_blocked"] is False
    assert chase_reading("PUT", Decimal("110"), opened, width, symbol="NVDA")["chase_blocked"] is False
    assert chase_reading("call", Decimal("110"), opened, width, symbol="nvda")["chase_blocked"] is True
    assert CHASE_ATR_LIMIT == Decimal("0.65")


def test_missing_atr_open_or_price_does_not_block_and_is_logged():
    bars = _bars(10)
    assert atr14(bars, SESSION) is None
    with structlog.testing.capture_logs() as logs:
        no_atr = chase_reading("CALL", "110", "100", None, symbol="AMD")
        no_open = chase_reading("PUT", "90", None, "2", symbol="AMD")
        no_price = chase_reading("CALL", 0, "100", "2", symbol="AMD")
        short = chase_reading("PUT", "90", "100", atr14(bars, SESSION), symbol="AMD")
    assert no_atr["reason"] is None and no_atr["missing"] is True
    assert no_open["reason"] is None and no_price["reason"] is None and short["reason"] is None
    assert [row.get("event") for row in logs].count("entry.chase_data_missing") == 4
    assert all(row.get("symbol") == "AMD" for row in logs)


def test_atr14_uses_completed_days_and_ignores_todays_bar():
    prior = _bars(15)
    today = Bar(datetime(2026, 10, 1, 15, 0, tzinfo=UTC), Decimal("100"), Decimal("130"), Decimal("70"), Decimal("120"), 1)
    assert atr14(prior, SESSION) == Decimal("2")
    assert atr14([*prior, today], SESSION) == Decimal("2")
    assert atr14(prior, None) == Decimal("2")
    highs = [101.0] * 15
    lows = [99.0] * 15
    closes = [100.0] * 15
    assert atr(highs, lows, closes) == 2
    huge_today = _bars(14) + [Bar(datetime(2026, 10, 1, 18, 0, tzinfo=UTC), Decimal("100"), Decimal("180"), Decimal("20"), Decimal("100"), 1)]
    assert atr14(huge_today, SESSION) is None


def test_both_profiles_feed_the_same_rule_and_eli_and_avi_do_not():
    main = load_trading_config()
    options2 = load_trading_config(ROOT / "app" / "config" / "trading_v2610_40k.toml")
    assert public_add_brake(Decimal("-168.645"), effective_kill_usd(main, open_equity=Decimal("5782.11")))["blocking"] is True
    assert public_add_brake(Decimal("-350"), effective_kill_usd(options2, open_equity=Decimal("40000")))["blocking"] is True
    assert public_chase()["limit"] == "0.65"
    assert public_chase()["blocks_on_missing_data"] is False
    for name in ("spy/engine.py", "spy/strategy.py", "avi/engine.py", "avi/strategy.py"):
        text = (ROOT / "app" / name).read_text(encoding="utf-8")
        assert "entry_gates" not in text
        assert "ADD_BRAKE" not in text
        assert "CHASE_ATR" not in text


def test_exits_do_not_read_the_new_gates():
    exits = inspect.getsource(monitor_once) + inspect.getsource(close_all_positions)
    assert "add_brake_reason" not in exits
    assert "chase_reading" not in exits
    assert "ADD_BRAKE" not in exits
    assert "CHASE_ATR" not in exits
    assert "add_brake_reason" in inspect.getsource(_submit_autonomous_buys)
    assert "chase_reading" in inspect.getsource(_submit_autonomous_buys)


def test_scan_records_move_atr_and_both_gates_can_suppress_a_buy():
    from app.recommendations.pipeline import run_scan
    from app.schemas.domain import DecisionKind
    from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market

    underlying, option, news = sample_market()
    config = loose_config()
    moment = AS_OF
    quiet = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        counts={},
    )
    assert quiet[0].decision is DecisionKind.BUY
    assert quiet[0].policy_trace["move_atr"] is None
    counted = {}
    quiet = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        counts=counted,
    )
    assert counted["move_atr"][0]["missing"] is True
    assert counted["move_atr"][0]["chase_blocked"] is False

    chased = replace(underlying, bars=tuple(_bars(15)))
    counts_chase: dict = {}
    blocked = run_scan(
        [chased],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        counts=counts_chase,
    )
    assert blocked[0].decision is DecisionKind.SUPPRESS
    assert blocked[0].suppress_reason.startswith(CHASE_ATR)
    assert blocked[0].policy_trace["move_atr"] == "1"
    assert counts_chase["move_atr"][0]["chase_blocked"] is True
    assert counts_chase["move_atr"][0]["symbol"] == "TEST"

    wide = _bars(15, high="110", low="90", close="100")
    allowed = run_scan(
        [replace(underlying, bars=tuple(wide))],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
    )
    assert allowed[0].decision is DecisionKind.BUY
    assert allowed[0].policy_trace["atr14"] == "20"

    stopped = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        account_day_pnl=Decimal("-350"),
        kill_usd=Decimal("1400"),
    )
    assert stopped[0].suppress_reason.startswith(ADD_BRAKE)
    resumed = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        account_day_pnl=Decimal("-349.99"),
        kill_usd=Decimal("1400"),
    )
    assert resumed[0].decision is DecisionKind.BUY


def test_rules_book_and_risk_and_system_show_both_gates(tmp_path):
    from app.analytics.trading_rules import build_trading_rules
    from app.main import create_app

    entry = build_trading_rules(Settings(app_env="local", trading_mode="PAPER", auth_required=False))["active"]["sections"]["entry"]
    brake = next(item for item in entry if item["id"] == "add_brake")
    chase = next(item for item in entry if item["id"] == "chase_atr")
    assert ADD_BRAKE in brake["explain"] and "25%" in brake["value"]
    assert CHASE_ATR in chase["explain"] and "0.65" in chase["value"]
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
        body = risk.json()
        assert body["add_brake"]["fraction"] == "0.25"
        assert body["add_brake"]["lifts_when_recovered"] is True
        assert body["chase_atr"]["limit"] == "0.65"
        assert body["chase_atr"]["period"] == 14
        assert body["chase_atr"]["blocks_on_missing_data"] is False
        system = client.get("/api/v1/system")
        assert system.status_code == 200
        assert system.json()["add_brake"]["fraction"] == "0.25"
        assert system.json()["chase_atr"]["limit"] == "0.65"


def test_submit_blocks_on_the_brake_and_resumes_when_the_day_recovers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.schemas.domain import DecisionKind, OptionRight, Recommendation
    from app.workers import loops
    from tests.test_entry_pace import _database

    settings = _database(tmp_path)
    placed: list[str] = []
    monkeypatch.setattr(loops, "_cash", lambda _settings: Decimal("100000"))
    monkeypatch.setattr(loops, "_entry_pnl", lambda _settings: Decimal("0"))
    monkeypatch.setattr(loops, "_measured_sectors", lambda _settings: {})
    monkeypatch.setattr("app.options.account_read.entry_block_reason", lambda: None)
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *args, **kwargs: {"closed": False, "reason": ""})
    monkeypatch.setattr("app.capital.session_lock.open_equity_from_account", lambda *args, **kwargs: Decimal("30000"))

    class Adapter:
        def __init__(self, equity: str):
            self.equity = equity

        def get_market_clock(self):
            return {"is_open": True, "next_close": "2026-11-13T21:00:00+00:00"}

        def get_positions(self):
            return []

        def get_orders(self, _status=None):
            return []

        def get_account(self):
            return {
                "equity": self.equity,
                "last_equity": "30000",
                "cash": self.equity,
                "buying_power": self.equity,
                "options_buying_power": self.equity,
            }

        def get_activities(self):
            return []

        def close(self):
            return None

    state = {"equity": "29150.00"}
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda _settings: Adapter(state["equity"]))

    def quotes(_settings, _live, _buys):
        found = loops._SpotQuotes()
        found.spots["NVDA"] = SimpleNamespace(open=Decimal("100"), high=Decimal("100.2"), price=Decimal("100"))
        found.append(SimpleNamespace())
        return found

    monkeypatch.setattr(loops, "_revalidation_quotes", quotes)

    def place_order(*args, **kwargs):
        placed.append("placed")
        return {"duplicate": False, "order": {}}

    monkeypatch.setattr("app.broker.execution.place_order", place_order)
    moment = datetime(2026, 11, 13, 15, 30, tzinfo=UTC)
    buy = Recommendation(
        recommendation_id="rec-brake-1",
        decision=DecisionKind.BUY,
        timestamp=moment,
        underlying="NVDA",
        underlying_price=Decimal("100"),
        option_symbol="NVDA261113C00100000",
        call_put=OptionRight.CALL,
        strike=Decimal("100"),
        expiration=moment.date() + timedelta(days=2),
        option_price=Decimal("1.02"),
        bid=Decimal("1.00"),
        ask=Decimal("1.05"),
        delta=Decimal("0.40"),
        gamma=Decimal("0.02"),
        theta=Decimal("-0.05"),
        vega=Decimal("0.10"),
        iv=Decimal("0.35"),
        volume=500,
        open_interest=500,
        max_entry_price=Decimal("1.05"),
        quantity=1,
        holding_window_min=5,
        holding_window_max=90,
        thesis="",
        catalyst="",
        risk="",
        invalidation="",
        reason_codes=["TEST"],
        gate_results={"DATA_OK": True},
        policy_trace={"atr14": "10", "chase_open": "100", "chase_price": "100"},
    )
    # Locked open is $30,000, so the 20K kill is $3,500 and the brake is −$875.
    # Equity 29,100 is a −$900 day, past the line. 29,200 is −$800 and the brake lifts.
    state["equity"] = "29100.00"
    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [buy], moment, outcomes)
    assert outcomes[0]["result"] == "blocked"
    assert outcomes[0]["reason"].startswith(ADD_BRAKE)
    assert "day_pnl=-900" in outcomes[0]["reason"]
    assert placed == []
    from app.options.day_floor import day_close_reason

    assert day_close_reason(settings, moment) is None

    state["equity"] = "29200.00"
    outcomes = []
    recovered = replace(buy, recommendation_id="rec-brake-2")
    _submit_autonomous_buys(settings, [recovered], moment, outcomes)
    assert outcomes[0]["result"] == "submitted"
    assert placed == ["placed"]

    state["equity"] = "30000.00"
    chased = replace(
        buy,
        recommendation_id="rec-chase-1",
        policy_trace={"atr14": "10", "chase_open": "100", "chase_price": "100"},
    )
    monkeypatch.setattr(
        loops,
        "_revalidation_quotes",
        lambda _settings, _live, _buys: _spots(Decimal("106.51")),
    )
    outcomes = []
    _submit_autonomous_buys(settings, [chased], moment, outcomes)
    assert outcomes[0]["result"] == "blocked"
    assert outcomes[0]["reason"].startswith(CHASE_ATR)
    assert outcomes[0]["move_atr"] is not None
    assert placed == ["placed"]

    monkeypatch.setattr(
        loops,
        "_revalidation_quotes",
        lambda _settings, _live, _buys: _spots(Decimal("100")),
    )
    missing = replace(buy, recommendation_id="rec-chase-2", policy_trace={})
    outcomes = []
    _submit_autonomous_buys(settings, [missing], moment, outcomes)
    assert outcomes[0]["result"] == "submitted"


def _spots(price: Decimal):
    from types import SimpleNamespace

    from app.workers import loops

    found = loops._SpotQuotes()
    found.spots["NVDA"] = SimpleNamespace(open=Decimal("100"), high=Decimal("200"), price=price)
    found.append(SimpleNamespace())
    return found
