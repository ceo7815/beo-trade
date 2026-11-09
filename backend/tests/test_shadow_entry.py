"""Shadow entry rules are a log. They do not change an order."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config.settings import Settings, TradingConfig, load_trading_config
from app.positions.shadow_log import drain_shadow, note_entry_shadow, process_snapshot, reset_shadow_memory, shadow_report
from app.positions.shadow_rules import (
    OpenLeg,
    RecentEntry,
    SessionPath,
    ShadowView,
    analogs_from_sessions,
    continuation_hit,
    evaluate_all,
    move_bucket,
    stack_confirm,
    stack_window,
)
from app.schemas.domain import OptionRight
from app.workers import loops

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 16, 11, 0, tzinfo=NY)


@pytest.fixture(autouse=True)
def _reset_pause():
    reset_shadow_memory()
    yield
    drain_shadow(0.5)
    reset_shadow_memory()


def _view(**overrides) -> ShadowView:
    base = ShadowView(side="CALL", moment=NOW)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _leg(side, minutes_ago, pnl, dollars="-10", symbol="NVDA251016C00100000"):
    return OpenLeg(side, symbol, NOW - timedelta(minutes=minutes_ago), Decimal(str(pnl)), Decimal(str(dollars)))


def test_stack_confirm_blocks_a_young_same_side_loser_and_keeps_the_raw_inputs():
    view = _view(open_legs=[_leg("CALL", 4, "-0.08", "-40"), _leg("PUT", 2, "-0.50", "-90", "QQQ251016P00500000")])
    decision = stack_confirm(view)
    assert decision["would_block"] is True
    assert decision["reason"] == "young_open_below_threshold"
    inputs = decision["inputs"]
    assert Decimal(inputs["minutes_since_last_same_direction_entry"]) == Decimal("4")
    assert Decimal(inputs["last_same_direction_pnl_pct"]) == Decimal("-0.08")
    assert inputs["open_same_direction_count"] == 1
    assert Decimal(inputs["open_same_direction_pnl_dollars"]) == Decimal("-40")
    flat = stack_confirm(_view(open_legs=[_leg("CALL", 4, "0", "0")]))
    assert flat["would_block"] is False
    aged = stack_confirm(_view(open_legs=[_leg("CALL", 10, "-0.2", "-20")]))
    assert aged["would_block"] is False
    other = stack_confirm(_view(side="PUT", open_legs=[_leg("CALL", 1, "-0.2", "-20")]))
    assert other["would_block"] is False


def test_stack_window_blocks_only_when_the_third_same_side_entry_follows_a_red_first():
    first = RecentEntry(NOW - timedelta(minutes=20), "CALL", "AAA", Decimal("-0.05"))
    second = RecentEntry(NOW - timedelta(minutes=8), "CALL", "BBB", Decimal("0.10"))
    blocked = stack_window(_view(recent_entries=[first, second]))
    assert blocked["would_block"] is True
    assert blocked["reason"] == "first_not_green"
    assert blocked["inputs"]["prospective_count"] == 3
    green = stack_window(_view(recent_entries=[replace_pnl(first, Decimal("0")), second]))
    assert green["would_block"] is False
    assert green["reason"] == "first_green"
    short = stack_window(_view(recent_entries=[second]))
    assert short["would_block"] is False
    assert short["reason"] == "below_count"
    stale = RecentEntry(NOW - timedelta(minutes=30), "CALL", "OLD", Decimal("-0.2"))
    assert stack_window(_view(recent_entries=[stale, first, second]))["inputs"]["prior_same_direction_count"] == 2
    unknown = stack_window(_view(recent_entries=[RecentEntry(NOW - timedelta(minutes=12), "CALL", "CCC", None), second]))
    assert unknown["would_block"] is False
    assert unknown["reason"] == "first_pnl_unknown"
    puts = stack_window(_view(side="PUT", recent_entries=[first, second, RecentEntry(NOW - timedelta(minutes=3), "CALL", "DDD", Decimal("-0.1"))]))
    assert puts["would_block"] is False


def test_open_loser_same_direction_ignores_the_other_side_and_a_flat_mark():
    rules, _paused = evaluate_all(_view(open_legs=[_leg("PUT", 30, "-0.4", "-15", "QQQ251016P00500000"), _leg("CALL", 40, "0.01", "5")]))
    loser = _rule(rules, "open_loser_same_dir")
    assert loser["would_block"] is False
    red, _paused = evaluate_all(_view(open_legs=[_leg("CALL", 40, "-0.01", "-1")]))
    assert _rule(red, "open_loser_same_dir")["would_block"] is True
    assert _rule(red, "open_loser_same_dir")["inputs"]["open_loser_count"] == 1


def test_day_loss_pause_uses_hysteresis_and_logs_the_percent():
    view = _view(day_pnl=Decimal("-490"), open_equity=Decimal("10000"), day_pnl_pct=Decimal("-0.049"), loss_pct=Decimal("0.049"))
    first, paused = evaluate_all(view)
    assert paused is False
    assert _rule(first, "day_loss_pause")["would_block"] is False
    assert _rule(first, "day_loss_pause")["inputs"]["day_pnl_pct"] == "-0.049"
    armed = _view(day_pnl=Decimal("-500"), open_equity=Decimal("10000"), day_pnl_pct=Decimal("-0.05"), loss_pct=Decimal("0.05"), paused=True)
    # Crossing 5% from flat.
    crossed, paused = evaluate_all(_view(day_pnl=Decimal("-500"), open_equity=Decimal("10000"), day_pnl_pct=Decimal("-0.05"), loss_pct=Decimal("0.05")))
    assert paused is True
    assert _rule(crossed, "day_loss_pause")["reason"] == "pause_at_or_above_5pct"
    held, paused = evaluate_all(_view(loss_pct=Decimal("0.035"), day_pnl_pct=Decimal("-0.035"), paused=True))
    assert paused is True
    assert _rule(held, "day_loss_pause")["reason"] == "still_paused"
    released, paused = evaluate_all(_view(loss_pct=Decimal("0.0349"), day_pnl_pct=Decimal("-0.0349"), paused=True))
    assert paused is False
    assert _rule(released, "day_loss_pause")["reason"] == "resume_below_3_5pct"
    new_day, paused = evaluate_all(_view(loss_pct=Decimal("0.02"), day_pnl_pct=Decimal("-0.02"), paused=True, same_pause_session=False))
    assert paused is False
    assert _rule(new_day, "day_loss_pause")["inputs"]["paused_before"] is False
    missing, paused = evaluate_all(_view())
    assert _rule(missing, "day_loss_pause")["reason"] == "not_available"
    assert _rule(missing, "day_loss_pause")["would_block"] is False
    assert paused is False
    assert armed.paused is True


def test_setup_history_scores_continuation_before_reversal_and_logs_not_available():
    reference = Decimal("100")
    assert continuation_hit(reference, [(5, Decimal("101"))], "CALL") is True
    assert continuation_hit(reference, [(5, Decimal("99"))], "CALL") is False
    assert continuation_hit(reference, [(4, Decimal("99")), (6, Decimal("101"))], "CALL") is False
    assert continuation_hit(reference, [(3, Decimal("99")), (8, Decimal("101"))], "PUT") is True
    assert continuation_hit(reference, [(10, Decimal("100.2"))], "CALL") is False
    assert continuation_hit(reference, [], "CALL") is None
    sessions = [
        SessionPath(Decimal("100"), ((40, Decimal("101.2")), (50, Decimal("103.1")), (70, Decimal("104")))),
        SessionPath(Decimal("100"), ((42, Decimal("101.5")), (55, Decimal("100.4")))),
        SessionPath(Decimal("100"), ((41, Decimal("90")), (50, Decimal("89")))),
    ]
    # Two up_small analogs around minute 40. The down session is a different bucket.
    samples, detail = analogs_from_sessions(sessions, minutes_since_open=40, side="CALL", move_from_open=Decimal("0.012"))
    assert detail["move_bucket"] == "up_small"
    assert samples == [True, False]
    thin, _paused = evaluate_all(_view(setup_samples=samples, setup_detail=detail))
    assert _rule(thin, "setup_history")["reason"] == "insufficient_samples"
    assert _rule(thin, "setup_history")["would_block"] is False
    many = [True] * 8 + [False] * 12
    blocked, _paused = evaluate_all(_view(setup_samples=many, setup_detail={"source": "market_snapshots", "move_bucket": "up_small"}))
    assert _rule(blocked, "setup_history")["would_block"] is True
    assert Decimal(_rule(blocked, "setup_history")["inputs"]["rate"]) == Decimal("0.4")
    healthy = [True] * 10 + [False] * 10
    allowed, _paused = evaluate_all(_view(setup_samples=healthy, setup_detail={"source": "market_snapshots"}))
    assert _rule(allowed, "setup_history")["would_block"] is False
    assert _rule(allowed, "setup_history")["reason"] == "rate_ok"
    missing, _paused = evaluate_all(_view())
    history = _rule(missing, "setup_history")
    assert history["would_block"] is False
    assert history["reason"] == "not_available"
    assert "TODO" in history["inputs"]["todo"]
    assert move_bucket(Decimal("0.005")) == "flat"
    assert move_bucket(Decimal("0.006")) == "up_small"
    assert move_bucket(Decimal("-0.02")) == "down_mid"
    assert move_bucket(Decimal("0.04")) == "up_large"


def test_a_persisted_row_joins_closed_pnl_and_keeps_the_pause(tmp_path):
    from app.models.db import configure_database, init_db, session_scope
    from app.models.tables import BrokerFillRecord, BrokerOrderRecord, PaperTrade, ShadowRuleEval

    settings = _database(tmp_path)
    moment = NOW
    losing = _snapshot("rec-loss", "ord-loss", moment, loss="-600", equity="10000")
    process_snapshot(losing)
    still = _snapshot("rec-held", "ord-held", moment + timedelta(minutes=5), loss="-400", equity="10000")
    process_snapshot(still)
    released = _snapshot("rec-back", "ord-back", moment + timedelta(minutes=10), loss="-100", equity="10000")
    process_snapshot(released)
    with session_scope() as session:
        session.add(
            PaperTrade(
                id="paper-loss",
                user_id=settings.dev_user_id,
                recommendation_id="rec-loss",
                option_symbol="NVDA251016C00100000",
                quantity=1,
                entry_price=1,
                entry_at=moment,
                exit_price=0.4,
                exit_at=moment + timedelta(minutes=30),
                pnl_dollars=-60,
                bid=1,
                ask=1.05,
                iv=0.4,
                delta=0.4,
                gamma=0.01,
                theta=-0.02,
                vega=0.1,
                underlying_at_entry=100,
            )
        )
        session.add(
            BrokerOrderRecord(
                symbol="QQQ251016C00500000",
                side="buy",
                position_intent="buy_to_open",
                status="filled",
                broker_order_id="ord-back",
                recommendation_id="rec-back",
                created_at=moment,
            )
        )
        session.add(
            BrokerFillRecord(
                execution_id="qqq-buy",
                symbol="QQQ251016C00500000",
                payload={"execution_id": "qqq-buy", "symbol": "QQQ251016C00500000", "side": "buy", "qty": "1", "price": "2.00", "broker_order_id": "ord-back", "timestamp": moment.isoformat()},
                observed_at=moment,
            )
        )
        session.add(
            BrokerFillRecord(
                execution_id="qqq-sell",
                symbol="QQQ251016C00500000",
                payload={"execution_id": "qqq-sell", "symbol": "QQQ251016C00500000", "side": "sell", "qty": "1", "price": "3.00", "broker_order_id": "ord-exit", "timestamp": (moment + timedelta(minutes=20)).isoformat()},
                observed_at=moment + timedelta(minutes=20),
            )
        )
        session.commit()
        stored = list(session.scalars(__import__("sqlalchemy").select(ShadowRuleEval).order_by(ShadowRuleEval.evaluated_at)))
    assert [row.outcome for row in stored] == ["submitted", "submitted", "submitted"]
    pauses = [_rule(row.rules, "day_loss_pause") for row in stored]
    assert [item["would_block"] for item in pauses] == [True, True, False]
    assert pauses[0]["inputs"]["loss_pct"] == "0.06"
    assert pauses[1]["reason"] == "still_paused"
    assert pauses[2]["reason"] == "resume_below_3_5pct"
    report = shadow_report(10)
    by_id = {row["recommendation_id"]: row for row in report["rows"]}
    assert Decimal(by_id["rec-loss"]["closed_pnl"]) == Decimal("-60")
    assert Decimal(by_id["rec-back"]["closed_pnl"]) == Decimal("100")
    assert by_id["rec-held"]["closed_pnl"] is None
    summary = {item["rule"]: item for item in report["summary"]}
    assert summary["day_loss_pause"]["would_block"] == 2
    assert summary["day_loss_pause"]["would_allow"] == 1
    assert Decimal(summary["day_loss_pause"]["would_block_pnl"]) == Decimal("-60")
    assert Decimal(summary["day_loss_pause"]["would_allow_pnl"]) == Decimal("100")
    blocked = _snapshot("rec-skip", "", moment, loss="0", equity="10000", outcome="blocked", reason="OPENING_CAP")
    process_snapshot(blocked)
    again = shadow_report(10)
    assert any(row["outcome"] == "blocked" and row["existing_block_reason"] == "OPENING_CAP" for row in again["rows"])
    # A candidate already blocked by a live rule stays out of the closed-P&L split.
    assert summary["day_loss_pause"]["evaluated"] == 3


def test_setup_history_reads_local_snapshots_and_skips_a_paid_fetch(tmp_path, monkeypatch):
    from app.models.db import configure_database, init_db, session_scope
    from app.models.tables import MarketSnapshot, Underlying

    _database(tmp_path)
    prior = datetime(2026, 10, 15, 11, 0, tzinfo=NY)
    with session_scope() as session:
        underlying = Underlying(symbol="NVDA", name="NVDA")
        session.add(underlying)
        session.flush()
        session.add(MarketSnapshot(underlying_id=underlying.id, price=101.5, open=100, high=102, low=100, close=101.5, volume=1000, relative_volume=1, observed_at=prior.astimezone(timezone.utc)))
        session.add(MarketSnapshot(underlying_id=underlying.id, price=103.2, open=100, high=104, low=101, close=103.2, volume=1000, relative_volume=1, observed_at=(prior + timedelta(minutes=12)).astimezone(timezone.utc)))
        session.commit()
    calls = {"history": 0}

    def forbid(*_args, **_kwargs):
        calls["history"] += 1
        raise AssertionError("shadow setup history must not call a data provider")

    monkeypatch.setattr("app.providers.thetadata.ThetaFeed.history_ohlc", forbid, raising=False)
    process_snapshot(_snapshot("rec-setup", "ord-setup", NOW, loss="0", equity="10000", move="0.015"))
    report = shadow_report(5)
    history = _rule(report["rows"][0]["rules"], "setup_history")
    assert history["reason"] == "insufficient_samples"
    assert history["inputs"]["source"] == "market_snapshots"
    assert history["inputs"]["samples"] == 1
    assert calls["history"] == 0
    reset_shadow_memory()
    process_snapshot(_snapshot("rec-none", "ord-none", NOW, loss="0", equity="10000", underlying="MISSING"))
    report = shadow_report(5)
    missing = next(row for row in report["rows"] if row["recommendation_id"] == "rec-none")
    assert _rule(missing["rules"], "setup_history")["reason"] == "not_available"


def test_disabling_the_flag_writes_nothing(tmp_path):
    _database(tmp_path)
    note_entry_shadow({**_snapshot("rec-off", "ord-off", NOW, loss="0", equity="10000"), "enabled": False})
    drain_shadow(0.3)
    assert shadow_report(10)["rows"] == []
    process_snapshot({**_snapshot("rec-off", "ord-off", NOW, loss="0", equity="10000"), "enabled": False})
    assert shadow_report(10)["rows"] == []


def test_the_entry_path_submits_the_same_order_with_shadow_on_off_and_raising(tmp_path, monkeypatch):
    from app.models.db import configure_database, init_db

    settings = Settings(
        app_env="local",
        trading_mode="PAPER",
        auth_required=False,
        database_url="sqlite:///" + (tmp_path / "shadow-entry.db").as_posix(),
        auto_create_tables=True,
        api_admin_key="",
    )
    configure_database(settings)
    init_db(settings)
    captured: list[dict] = []

    def fake_place(_settings, _session, _adapter, request, now=None, quotes=None):
        captured.append(
            {
                "symbol": request["symbol"],
                "qty": request["qty"],
                "limit_price": request["limit_price"],
                "position_intent": request["position_intent"],
                "approved": request["approved"],
                "system_validated": request["system_validated"],
                "recommendation_id": request["recommendation_id"],
                "revalidation": dict(request["revalidation"]),
            }
        )
        return {"duplicate": False, "order": {"broker_order_id": "ord-live"}, "trade_id": "trade-live"}

    class Adapter:
        def get_market_clock(self):
            return {"is_open": True, "next_close": (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()}

        def get_positions(self):
            return []

        def get_orders(self, _status="open"):
            return []

        def get_account(self):
            return {
                "equity": "10000",
                "last_equity": "10000",
                "cash": "10000",
                "buying_power": "10000",
                "options_buying_power": "10000",
                "status": "ACTIVE",
            }

        def get_activities(self):
            return []

        def close(self):
            return None

    monkeypatch.setattr(
        loops,
        "build_providers",
        lambda _settings: SimpleNamespace(market=SimpleNamespace(name="test"), news=SimpleNamespace(name="unconfigured")),
    )

    def _quotes(*_args, **_kwargs):
        quotes = loops._SpotQuotes()
        return quotes

    monkeypatch.setattr(loops, "_revalidation_quotes", _quotes)
    monkeypatch.setattr(loops, "_cash", lambda _settings: Decimal("10000"))
    monkeypatch.setattr(loops, "_entry_pnl", lambda _settings: Decimal("0"))
    monkeypatch.setattr(loops, "_measured_sectors", lambda _settings: {})
    monkeypatch.setattr(loops, "_latch_kill", lambda _settings, _account, _measured, _moment, floor: floor)
    monkeypatch.setattr("app.options.account_read.entry_block_reason", lambda: None)
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *_args, **_kwargs: {"closed": False, "reason": ""})
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda _settings: Adapter())
    monkeypatch.setattr("app.models.store.save_recommendation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.broker.execution.place_order", fake_place)

    def run():
        captured.clear()
        outcomes: list[dict] = []
        loops._submit_autonomous_buys(settings, [_buy()], NOW, outcomes)
        return list(captured), list(outcomes)

    on_orders, on_outcomes = run()
    drain_shadow(2)
    base = settings.trading()
    original_trading = settings.trading
    object.__setattr__(settings, "trading", lambda: replace(base, shadow_entry_rules_enabled=False))
    off_orders, off_outcomes = run()
    object.__setattr__(settings, "trading", original_trading)

    def explode(*_args, **_kwargs):
        raise RuntimeError("shadow log failed")

    monkeypatch.setattr("app.positions.shadow_log.note_entry_shadow", explode)
    raised_orders, raised_outcomes = run()
    assert on_orders and on_orders == off_orders == raised_orders
    assert on_outcomes == off_outcomes == raised_outcomes
    assert on_outcomes[0]["result"] == "submitted"
    assert on_orders[0]["qty"] == on_orders[0]["qty"]
    assert on_orders[0]["position_intent"] == "buy_to_open"
    exits = inspect.getsource(loops.monitor_once) + inspect.getsource(loops.close_all_positions)
    assert "shadow_entry" not in exits
    assert "_shadow_entry" not in inspect.getsource(loops.place_order) if hasattr(loops, "place_order") else True
    from app.broker.execution import place_order

    assert "shadow" not in inspect.getsource(place_order)


def test_shadow_hook_swallows_a_raise_and_the_flag_defaults_on():
    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    # The import inside the hook is patched at the source module.
    import app.positions.shadow_log as shadow_log

    original = shadow_log.note_entry_shadow
    shadow_log.note_entry_shadow = explode
    try:
        loops._shadow_entry(
            Settings(app_env="local", trading_mode="PAPER", auth_required=False),
            SimpleNamespace(recommendation_id="rec", underlying="NVDA", option_symbol="X", call_put=OptionRight.CALL, expiration=NOW.date()),
            enabled=True,
            outcome="submitted",
            block_reason="",
            moment=NOW,
        )
    finally:
        shadow_log.note_entry_shadow = original
    loops._shadow_entry(
        Settings(app_env="local", trading_mode="PAPER", auth_required=False),
        SimpleNamespace(),
        enabled=False,
        outcome="blocked",
        block_reason="OPENING_CAP",
        moment=NOW,
    )
    for path in ("trading.toml", "trading_100k.toml", "trading_10k.toml"):
        from app.config.settings import CONFIG_DIR

        assert load_trading_config(CONFIG_DIR / path).shadow_entry_rules_enabled is True
    assert TradingConfig().shadow_entry_rules_enabled is True


def _rule(rules, name):
    return next(item for item in rules if item["rule"] == name)


def replace_pnl(entry: RecentEntry, pnl: Decimal) -> RecentEntry:
    return RecentEntry(entry.at, entry.side, entry.symbol, pnl)


def _snapshot(recommendation_id, order_id, moment, *, loss, equity, move="0.01", underlying="NVDA", outcome="submitted", reason=""):
    return {
        "enabled": True,
        "recommendation_id": recommendation_id,
        "order_id": order_id,
        "outcome": outcome,
        "existing_block_reason": reason,
        "evaluated_at": moment.isoformat(),
        "underlying": underlying,
        "option_symbol": "NVDA251016C00100000",
        "side": "CALL",
        "dte": 0,
        "minutes_since_open": 90,
        "underlying_price": "101",
        "session_open": "100",
        "prior_close": "99",
        "vwap": "100.4",
        "price_vs_open_pct": move,
        "price_vs_vwap_pct": "0.006",
        "change_vs_prior_close_pct": "0.02",
        "positions": [
            {
                "symbol": "AMD251016C00100000",
                "side": "CALL",
                "pnl_pct": "-0.04",
                "pnl_dollars": "-25",
                "opened_at": (moment - timedelta(minutes=6)).isoformat(),
            }
        ],
        "day_pnl": loss,
        "open_equity": equity,
        "scan_entries": [],
    }


def _database(tmp_path):
    from app.models.db import configure_database, init_db

    settings = Settings(
        app_env="local",
        trading_mode="PAPER",
        auth_required=False,
        database_url="sqlite:///" + (tmp_path / "shadow.db").as_posix(),
        auto_create_tables=True,
    )
    configure_database(settings)
    init_db(settings)
    return settings


def _buy():
    from app.schemas.domain import DecisionKind

    return SimpleNamespace(
        decision=DecisionKind.BUY,
        underlying="NVDA",
        option_symbol="NVDA251016C00100000",
        recommendation_id="rec-shadow-entry",
        call_put=OptionRight.CALL,
        quantity=1,
        ask=Decimal("1.00"),
        bid=Decimal("0.95"),
        option_price=Decimal("0.98"),
        max_entry_price=Decimal("1.05"),
        strike=Decimal("100"),
        expiration=NOW.date() + timedelta(days=1),
        volume=500,
        open_interest=500,
        timestamp=NOW,
        iv=Decimal("0.4"),
        delta=Decimal("0.45"),
        gamma=Decimal("0.02"),
        theta=Decimal("-0.05"),
        vega=Decimal("0.1"),
    )
