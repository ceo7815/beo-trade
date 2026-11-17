"""Options 2 (v2610_40k) since 17.11: no LOSS_STREAK brake, and no new buys before 09:50 New York.

The 20K book (20k_ils) keeps LOSS_STREAK and has no opening wait.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.config.settings import Settings, load_trading_config
from app.ops.safety import policy_errors
from app.positions.entry_gates import (
    LOSS_STREAK,
    loss_streak_enabled,
    loss_streak_gate,
    opening_wait_gate,
    opening_wait_reason,
    public_loss_streak,
    public_opening_wait,
)
from tests.test_entry_gates import _close, _fill, _spots

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
O2_TOML = ROOT / "app" / "config" / "trading_v2610_40k.toml"
K20_TOML = ROOT / "app" / "config" / "trading.toml"
# 2026-11-13 is New York standard time (UTC-5): 09:30 NY = 14:30 UTC.
WINTER_OPEN = datetime(2026, 11, 13, 14, 30, tzinfo=UTC)
# 2026-07-15 is New York daylight time (UTC-4): 09:30 NY = 13:30 UTC.
SUMMER_OPEN = datetime(2026, 7, 15, 13, 30, tzinfo=UTC)


def _o2():
    return load_trading_config(O2_TOML)


def _k20():
    return load_trading_config(K20_TOML)


def test_profiles_carry_the_new_settings_and_match_their_official_policy():
    o2, k20 = _o2(), _k20()
    assert o2.capital_profile == "v2610_40k"
    assert k20.capital_profile == "20k_ils"
    assert o2.loss_streak_enabled is False and o2.opening_wait_minutes == 20
    assert k20.loss_streak_enabled is True and k20.opening_wait_minutes == 0
    assert policy_errors(o2) == []
    assert policy_errors(k20) == []
    # Untouched on Options 2.
    assert o2.daily_kill_loss_usd == 1400
    assert o2.opening_entry_cap == 2 and o2.opening_entry_until_minutes == 600


def test_opening_wait_window_is_0930_to_0949_59_new_york_and_follows_dst():
    for open_utc in (WINTER_OPEN, SUMMER_OPEN):
        assert opening_wait_reason(open_utc - timedelta(seconds=1), 20) is None
        assert opening_wait_reason(open_utc, 20) == "OPENING_WAIT_20M"
        assert opening_wait_reason(open_utc + timedelta(minutes=10), 20) == "OPENING_WAIT_20M"
        assert opening_wait_reason(open_utc + timedelta(minutes=19, seconds=59), 20) == "OPENING_WAIT_20M"
        assert opening_wait_reason(open_utc + timedelta(minutes=20), 20) is None
        assert opening_wait_reason(open_utc + timedelta(hours=3), 20) is None
        assert opening_wait_reason(open_utc + timedelta(minutes=5), 0) is None
    at_0940 = WINTER_OPEN + timedelta(minutes=10)
    at_0950 = WINTER_OPEN + timedelta(minutes=20)
    assert opening_wait_gate(_o2(), at_0940) == "OPENING_WAIT_20M"
    assert opening_wait_gate(_o2(), at_0950) is None
    assert opening_wait_gate(_k20(), at_0940) is None


def test_two_losses_do_not_block_options2_and_still_block_20k():
    now = WINTER_OPEN + timedelta(hours=1)
    closes = [_close(1, "-120", day=WINTER_OPEN), _close(2, "-580", day=WINTER_OPEN)]
    assert loss_streak_enabled(_o2()) is False
    assert loss_streak_enabled(_k20()) is True
    assert loss_streak_gate(_o2(), closes, now) is None
    assert loss_streak_gate(_k20(), closes, now).startswith(LOSS_STREAK)
    shown = public_loss_streak(closes, now, enabled=False)
    assert shown["enabled"] is False and shown["blocking"] is False and shown["reason"] is None
    assert shown["current_streak"] == 2
    assert public_loss_streak(closes, now)["blocking"] is True
    wait = public_opening_wait(_o2(), WINTER_OPEN + timedelta(minutes=10))
    assert wait == {
        "rule": "OPENING_WAIT_20M",
        "enabled": True,
        "minutes": 20,
        "first_entry_ny": "09:50",
        "blocking": True,
        "reason": "OPENING_WAIT_20M",
    }
    assert public_opening_wait(_k20())["enabled"] is False


def test_scan_suppresses_on_opening_wait_and_ignores_the_streak_when_off():
    from app.recommendations.pipeline import run_scan
    from app.schemas.domain import DecisionKind
    from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market

    underlying, option, news = sample_market()
    losses = [_close(1, "-10", day=AS_OF - timedelta(hours=1)), _close(2, "-5", day=AS_OF - timedelta(hours=1))]

    def scan(config, closes=None):
        return run_scan(
            [underlying], [option], news, config, StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("100000"), set(), session_closes=closes
        )

    on = loose_config()
    assert scan(on, losses)[0].suppress_reason.startswith(LOSS_STREAK)
    off = replace(on, loss_streak_enabled=False)
    assert scan(off, losses)[0].decision is DecisionKind.BUY
    # AS_OF is 11:00 New York; a 100-minute wait still covers it, a 20-minute wait does not.
    waiting = scan(replace(off, opening_wait_minutes=100), losses)[0]
    assert waiting.decision is DecisionKind.SUPPRESS
    assert waiting.suppress_reason == "OPENING_WAIT_100M"
    assert waiting.policy_trace["opening_wait"] == "OPENING_WAIT_100M"
    assert scan(replace(off, opening_wait_minutes=20), losses)[0].decision is DecisionKind.BUY


def _submit_setup(tmp_path, monkeypatch, toml: Path):
    from types import SimpleNamespace

    from app.models.db import configure_database, init_db
    from app.workers import loops

    settings = Settings(
        app_env="local",
        trading_mode="PAPER",
        auth_required=False,
        database_url="sqlite:///" + (tmp_path / "o2.db").as_posix(),
        auto_create_tables=True,
        trading_config_path=str(toml),
    )
    configure_database(settings)
    init_db(settings)
    placed: list[str] = []
    monkeypatch.setattr(loops, "_cash", lambda _settings: Decimal("40000"))
    monkeypatch.setattr(loops, "_entry_pnl", lambda _settings: Decimal("0"))
    monkeypatch.setattr(loops, "_measured_sectors", lambda _settings: {})
    monkeypatch.setattr("app.options.account_read.entry_block_reason", lambda: None)
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *args, **kwargs: {"closed": False, "reason": ""})
    monkeypatch.setattr("app.capital.session_lock.open_equity_from_account", lambda *args, **kwargs: Decimal("40000"))
    monkeypatch.setattr("app.positions.open_guard.refresh_open_guard", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.positions.open_guard.entry_block", lambda *args, **kwargs: None)
    state = {"fills": []}

    class Adapter:
        def get_market_clock(self):
            return {"is_open": True, "next_close": "2026-11-13T21:00:00+00:00"}

        def get_positions(self):
            return []

        def get_orders(self, _status=None):
            return []

        def get_account(self):
            return {"equity": "39300", "last_equity": "40000", "cash": "39300", "buying_power": "39300", "options_buying_power": "39300"}

        def get_activities(self):
            return []

        def get_all_fills(self, max_pages=20, after=""):
            return list(state["fills"])

        def close(self):
            return None

    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda _settings: Adapter())
    monkeypatch.setattr(loops, "_revalidation_quotes", lambda _settings, _live, _buys: _spots(Decimal("100")))

    def place_order(*args, **kwargs):
        placed.append("placed")
        return {"duplicate": False, "order": {}}

    monkeypatch.setattr("app.broker.execution.place_order", place_order)
    return settings, state, placed, SimpleNamespace


def _buy(moment: datetime, rec_id: str, right: str = "PUT"):
    from app.schemas.domain import DecisionKind, OptionRight, Recommendation

    return Recommendation(
        recommendation_id=rec_id,
        decision=DecisionKind.BUY,
        timestamp=moment,
        underlying="NVDA",
        underlying_price=Decimal("100"),
        option_symbol=f"NVDA261113{right[0]}00100000",
        call_put=OptionRight(right),
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


def _two_losing_closes(day: datetime) -> list[dict]:
    first, second = "ARM261113C00100000", "COIN261113C00100000"
    return [
        _fill("a1", first, "buy", "5.95", day),
        _fill("a2", first, "sell", "4.30", day + timedelta(minutes=5)),
        _fill("b1", second, "buy", "2.36", day + timedelta(minutes=6)),
        _fill("b2", second, "sell", "1.91", day + timedelta(minutes=9)),
    ]


def _events(rec_id: str) -> list[str]:
    from sqlalchemy import select

    from app.models.db import session_scope
    from app.models.tables import BrokerTradeEvent

    with session_scope() as session:
        return [
            f"{row.state}:{row.reason}"
            for row in session.scalars(select(BrokerTradeEvent).where(BrokerTradeEvent.trade_id == rec_id))
        ]


def test_options2_submit_waits_until_0950_and_keeps_trading_after_two_losses(tmp_path, monkeypatch):
    from app.workers.loops import _submit_autonomous_buys

    settings, state, placed, _ = _submit_setup(tmp_path, monkeypatch, O2_TOML)
    assert settings.trading().capital_profile == "v2610_40k"

    at_0940 = WINTER_OPEN + timedelta(minutes=10)
    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [_buy(at_0940, "rec-o2-0940")], at_0940, outcomes)
    assert outcomes[0]["result"] == "blocked"
    assert outcomes[0]["reason"] == "OPENING_WAIT_20M"
    assert placed == []
    assert "BLOCKED:OPENING_WAIT_20M" in _events("rec-o2-0940")

    at_0950 = WINTER_OPEN + timedelta(minutes=20)
    outcomes = []
    _submit_autonomous_buys(settings, [_buy(at_0950, "rec-o2-0950")], at_0950, outcomes)
    assert outcomes[0]["result"] == "submitted", outcomes
    assert placed == ["placed"]

    # Two losing closes in a row (like ARM -495, COIN -315 on 17.11): Options 2 keeps going.
    later = WINTER_OPEN + timedelta(hours=1)
    state["fills"] = _two_losing_closes(WINTER_OPEN + timedelta(minutes=21))
    outcomes = []
    _submit_autonomous_buys(settings, [_buy(later, "rec-o2-streak")], later, outcomes)
    assert outcomes[0]["result"] == "submitted", outcomes
    assert placed == ["placed", "placed"]


def test_20k_submit_still_stops_on_two_losses_and_has_no_opening_wait(tmp_path, monkeypatch):
    from app.workers.loops import _submit_autonomous_buys

    settings, state, placed, _ = _submit_setup(tmp_path, monkeypatch, K20_TOML)
    assert settings.trading().capital_profile == "20k_ils"

    at_0940 = WINTER_OPEN + timedelta(minutes=10)
    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [_buy(at_0940, "rec-20k-0940")], at_0940, outcomes)
    assert not str(outcomes[0].get("reason") or "").startswith("OPENING_WAIT")
    assert outcomes[0]["result"] == "submitted", outcomes

    later = WINTER_OPEN + timedelta(hours=1)
    state["fills"] = _two_losing_closes(WINTER_OPEN + timedelta(minutes=21))
    outcomes = []
    _submit_autonomous_buys(settings, [_buy(later, "rec-20k-streak")], later, outcomes)
    assert outcomes[0]["result"] == "blocked"
    assert outcomes[0]["reason"].startswith(LOSS_STREAK)
    assert placed == ["placed"]


def _plain_settings(tmp_path, name: str, toml: Path) -> Settings:
    from app.models.db import configure_database, init_db

    settings = Settings(
        app_env="local",
        trading_mode="PAPER",
        auth_required=False,
        database_url="sqlite:///" + (tmp_path / name).as_posix(),
        auto_create_tables=True,
        trading_config_path=str(toml),
    )
    configure_database(settings)
    init_db(settings)
    return settings


def _revalidate(settings, moment: datetime, fills: list[dict]) -> str:
    from app.broker.execution import place_order
    from app.integrations.alpaca import PaperOnlyError
    from app.models.db import session_scope
    from tests.test_day_floor import _Book

    book = _Book("40000", "40000")
    book.orders = []
    book.get_all_fills = lambda max_pages=20, after="": list(fills)
    with session_scope() as session:
        try:
            place_order(
                settings,
                session,
                book,
                {"symbol": "NVDA261113P00100000", "qty": 1, "limit_price": "1.00", "position_intent": "buy_to_open", "approved": False, "system_validated": True},
                moment,
            )
        except PaperOnlyError as exc:
            return str(exc)
    return ""


def test_broker_revalidation_applies_the_wait_and_the_streak_per_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.account_read.entry_block_reason", lambda: None)
    monkeypatch.setattr("app.positions.open_guard.entry_block", lambda *args, **kwargs: None)
    o2 = _plain_settings(tmp_path, "o2.db", O2_TOML)
    at_0940 = WINTER_OPEN + timedelta(minutes=10)
    later = WINTER_OPEN + timedelta(hours=1)
    losses = _two_losing_closes(WINTER_OPEN + timedelta(minutes=21))
    assert "OPENING_WAIT_20M" in _revalidate(o2, at_0940, [])
    assert "OPENING_WAIT_20M" not in _revalidate(o2, WINTER_OPEN + timedelta(minutes=20), [])
    assert LOSS_STREAK not in _revalidate(o2, later, losses)

    k20 = _plain_settings(tmp_path, "k20.db", K20_TOML)
    assert "OPENING_WAIT" not in _revalidate(k20, at_0940, [])
    assert LOSS_STREAK in _revalidate(k20, later, losses)


def test_rules_book_and_release_notes_describe_both_changes():
    import json

    from app.analytics.trading_rules import build_trading_rules

    o2 = Settings(app_env="local", trading_mode="PAPER", auth_required=False, trading_config_path=str(O2_TOML))
    entry = build_trading_rules(o2)["active"]["sections"]["entry"]
    wait = next(item for item in entry if item["id"] == "opening_wait")
    brake = next(item for item in entry if item["id"] == "loss_streak")
    assert "09:50" in wait["value"] and "OPENING_WAIT_20M" in wait["explain"]
    assert "כבוי" in brake["value"]
    k20 = Settings(app_env="local", trading_mode="PAPER", auth_required=False)
    entry = build_trading_rules(k20)["active"]["sections"]["entry"]
    assert next(item for item in entry if item["id"] == "opening_wait")["value"] == "כבוי"
    assert next(item for item in entry if item["id"] == "loss_streak")["value"] == "2 סגירות מפסידות ברצף"
    latest = json.loads((ROOT / "app" / "config" / "releases.json").read_text(encoding="utf-8"))["releases"][0]
    assert latest["version"] == "2026.11.17.1"
    assert any("OPENING_WAIT_20M" in line for line in latest["changes"])
    assert any("LOSS_STREAK" in line for line in latest["changes"])


def test_every_entry_path_reads_the_profile_switches():
    from app.broker import execution
    from app.recommendations import pipeline
    from app.workers import loops

    submit = inspect.getsource(loops._submit_autonomous_buys)
    assert "loss_streak_enabled" in submit and "opening_wait_gate" in submit
    revalidate = inspect.getsource(execution)
    assert "loss_streak_enabled" in revalidate and "opening_wait_gate" in revalidate
    scan = inspect.getsource(pipeline.run_scan)
    assert "loss_streak_gate" in scan and "opening_wait_gate" in scan
