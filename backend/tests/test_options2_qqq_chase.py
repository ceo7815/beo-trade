"""Options 2 (v2610_40k) since 23.11: QQQ_CHASE_075.

No new CALL once QQQ is already >= +0.75% from today's 09:30 New York open, no new PUT once
QQQ is <= -0.75%. Missing QQQ data does not block. The 20K book (20k_ils) has it off.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from app.ops.safety import policy_errors
from app.positions.entry_gates import (
    index_chase_gate,
    index_chase_pct,
    index_chase_reading,
    public_index_chase,
)
from tests.test_options2_opening_wait import (
    K20_TOML,
    O2_TOML,
    WINTER_OPEN,
    _buy,
    _events,
    _k20,
    _o2,
    _plain_settings,
    _submit_setup,
)

# 10:30 New York, after OPENING_WAIT_20M and after the open_guard window.
LATER = WINTER_OPEN + timedelta(hours=1)
OPEN = Decimal("600")
UP_08 = Decimal("604.80")  # +0.80%
UP_05 = Decimal("603.00")  # +0.50%
DOWN_08 = Decimal("595.20")  # -0.80%
DOWN_05 = Decimal("597.00")  # -0.50%


def test_profiles_carry_the_threshold_and_match_their_official_policy():
    o2, k20 = _o2(), _k20()
    assert o2.index_chase_pct == 0.0075
    assert k20.index_chase_pct == 0.0
    assert index_chase_pct(o2) == Decimal("0.0075")
    assert index_chase_pct(k20) is None
    assert policy_errors(o2) == []
    assert policy_errors(k20) == []
    assert policy_errors(replace(o2, index_chase_pct=0.01)) == ["index_chase_pct"]
    assert policy_errors(replace(k20, index_chase_pct=0.0075)) == ["index_chase_pct"]
    # Untouched on Options 2.
    assert o2.opening_wait_minutes == 20 and o2.loss_streak_enabled is False
    assert o2.daily_kill_loss_usd == 1400


def test_call_blocked_at_plus_08_allowed_at_plus_05():
    o2 = _o2()
    blocked = index_chase_gate(o2, "CALL", UP_08, OPEN)
    assert blocked is not None and blocked.startswith("QQQ_CHASE_075")
    assert "QQQ_move=0.8%" in blocked and "side=CALL" in blocked
    assert index_chase_gate(o2, "CALL", UP_05, OPEN) is None
    # A CALL is not blocked by a QQQ drop, whatever its size.
    assert index_chase_gate(o2, "CALL", DOWN_08, OPEN) is None
    # Exactly at the limit blocks (>=).
    assert index_chase_gate(o2, "CALL", Decimal("604.50"), OPEN).startswith("QQQ_CHASE_075")


def test_put_blocked_at_minus_08_allowed_at_minus_05():
    o2 = _o2()
    blocked = index_chase_gate(o2, "PUT", DOWN_08, OPEN)
    assert blocked is not None and blocked.startswith("QQQ_CHASE_075") and "side=PUT" in blocked
    assert index_chase_gate(o2, "PUT", DOWN_05, OPEN) is None
    assert index_chase_gate(o2, "PUT", UP_08, OPEN) is None
    assert index_chase_gate(o2, "PUT", Decimal("595.50"), OPEN).startswith("QQQ_CHASE_075")


def test_20k_profile_is_unaffected():
    k20 = _k20()
    for side, price in (("CALL", UP_08), ("PUT", DOWN_08), ("CALL", Decimal("700"))):
        assert index_chase_gate(k20, side, price, OPEN) is None
    assert public_index_chase(k20)["enabled"] is False
    shown = public_index_chase(_o2())
    assert shown["enabled"] is True and shown["rule"] == "QQQ_CHASE_075" and shown["limit_pct"] == "0.75"
    assert shown["blocks_on_missing_data"] is False


def test_missing_qqq_data_does_not_block():
    o2 = _o2()
    for price, opened in ((None, OPEN), (UP_08, None), (None, None), ("", "0"), ("bad", OPEN), (UP_08, Decimal("0"))):
        assert index_chase_gate(o2, "CALL", price, opened) is None
        reading = index_chase_reading("CALL", price, opened, Decimal("0.0075"), log_missing=False)
        assert reading["missing"] is True and reading["blocked"] is False and reading["reason"] is None


def test_options2_submit_records_the_block_and_20k_submits(tmp_path, monkeypatch):
    from app.workers import loops
    from app.workers.loops import _submit_autonomous_buys

    quote = {"price": UP_08, "open": OPEN}
    monkeypatch.setattr(loops, "_index_chase_inputs", lambda *args, **kwargs: (quote["price"], quote["open"]))
    settings, _state, placed, _ = _submit_setup(tmp_path, monkeypatch, O2_TOML)
    assert settings.trading().capital_profile == "v2610_40k"

    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [_buy(LATER, "rec-o2-call-up", "CALL")], LATER, outcomes)
    assert outcomes[0]["result"] == "blocked", outcomes
    assert outcomes[0]["reason"].startswith("QQQ_CHASE_075")
    assert placed == []
    assert any(item.startswith("BLOCKED:QQQ_CHASE_075") for item in _events("rec-o2-call-up"))

    # Same QQQ +0.8%: a PUT is with nothing to chase and goes in.
    outcomes = []
    _submit_autonomous_buys(settings, [_buy(LATER + timedelta(minutes=1), "rec-o2-put-up", "PUT")], LATER + timedelta(minutes=1), outcomes)
    assert outcomes[0]["result"] == "submitted", outcomes
    assert placed == ["placed"]

    # QQQ unreadable: fail open.
    quote.update(price=None, open=None)
    outcomes = []
    _submit_autonomous_buys(settings, [_buy(LATER + timedelta(minutes=10), "rec-o2-missing", "CALL")], LATER + timedelta(minutes=10), outcomes)
    assert outcomes[0]["result"] == "submitted", outcomes


def test_20k_submit_never_reads_qqq_for_the_gate(tmp_path, monkeypatch):
    from app.workers import loops
    from app.workers.loops import _submit_autonomous_buys

    calls: list[int] = []

    def inputs(*args, **kwargs):
        calls.append(1)
        return UP_08, OPEN

    monkeypatch.setattr(loops, "_index_chase_inputs", inputs)
    settings, _state, placed, _ = _submit_setup(tmp_path, monkeypatch, K20_TOML)
    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [_buy(LATER, "rec-20k-call", "CALL")], LATER, outcomes)
    assert outcomes[0]["result"] == "submitted", outcomes
    assert not str(outcomes[0].get("reason") or "").startswith("QQQ_CHASE")
    assert calls == []


def test_index_chase_inputs_reuse_a_loaded_qqq_spot_and_fall_back_to_the_open_guard(monkeypatch):
    from types import SimpleNamespace

    from app.workers import loops

    spots = {"QQQ": SimpleNamespace(price=UP_08, open=OPEN)}
    assert loops._index_chase_inputs(None, LATER, LATER, spots) == (UP_08, OPEN)
    monkeypatch.setattr("app.positions.open_guard.guard_for", lambda moment: {"open": "601"})
    spots = {"QQQ": SimpleNamespace(price=UP_08, open=Decimal("0"))}
    assert loops._index_chase_inputs(None, LATER, LATER, spots) == (UP_08, "601")

    def broken(_settings):
        raise RuntimeError("feed down")

    monkeypatch.setattr(loops, "build_providers", broken)
    monkeypatch.setattr("app.positions.open_guard.guard_for", lambda moment: {})
    assert loops._index_chase_inputs(None, LATER, LATER, {}) == (None, None)


def _revalidate_with_gate(settings, moment, right: str, qqq_price, qqq_open) -> str:
    from app.broker.execution import place_order
    from app.integrations.alpaca import PaperOnlyError
    from app.models.db import session_scope
    from tests.test_day_floor import _Book

    book = _Book("40000", "40000")
    book.orders = []
    book.get_all_fills = lambda max_pages=20, after="": []
    symbol = f"NVDA261113{right[0]}00100000"
    gate = {"side": right, "open": None, "price": None, "atr14": None, "qqq_open": qqq_open, "qqq_price": qqq_price}
    with session_scope() as session:
        try:
            place_order(
                settings,
                session,
                book,
                {"symbol": symbol, "qty": 1, "limit_price": "1.00", "position_intent": "buy_to_open", "approved": False, "system_validated": True, "entry_gate": gate},
                moment,
            )
        except PaperOnlyError as exc:
            return str(exc)
    return ""


def test_broker_revalidation_applies_the_gate_per_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.account_read.entry_block_reason", lambda: None)
    monkeypatch.setattr("app.positions.open_guard.entry_block", lambda *args, **kwargs: None)
    o2 = _plain_settings(tmp_path, "o2.db", O2_TOML)
    assert "QQQ_CHASE_075" in _revalidate_with_gate(o2, LATER, "CALL", str(UP_08), str(OPEN))
    assert "QQQ_CHASE_075" in _revalidate_with_gate(o2, LATER, "PUT", str(DOWN_08), str(OPEN))
    assert "QQQ_CHASE" not in _revalidate_with_gate(o2, LATER, "CALL", str(UP_05), str(OPEN))
    assert "QQQ_CHASE" not in _revalidate_with_gate(o2, LATER, "CALL", None, None)
    k20 = _plain_settings(tmp_path, "k20.db", K20_TOML)
    assert "QQQ_CHASE" not in _revalidate_with_gate(k20, LATER, "CALL", str(UP_08), str(OPEN))


def test_scan_suppresses_on_qqq_chase_only_when_the_profile_has_it():
    from app.recommendations.pipeline import run_scan
    from app.schemas.domain import DecisionKind
    from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market

    underlying, option, news = sample_market()

    def scan(config, qqq_price=None):
        names = [underlying]
        if qqq_price is not None:
            names.append(replace(underlying, symbol="QQQ", open=OPEN, price=qqq_price))
        rows = run_scan(names, [option], news, config, StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("100000"), set())
        return next(row for row in rows if row.underlying == underlying.symbol)

    off = loose_config()
    on = replace(off, index_chase_pct=0.0075)
    assert scan(off).decision is DecisionKind.BUY
    assert scan(off, UP_08).decision is DecisionKind.BUY
    blocked = scan(on, UP_08)
    assert blocked.decision is DecisionKind.SUPPRESS
    assert blocked.suppress_reason.startswith("QQQ_CHASE_075")
    assert blocked.policy_trace["index_chase"].startswith("QQQ_CHASE_075")
    assert scan(on, UP_05).decision is DecisionKind.BUY
    # No QQQ in the scan: fail open.
    assert scan(on).decision is DecisionKind.BUY


def test_rules_book_release_notes_and_every_entry_path():
    import json
    from pathlib import Path

    from app.analytics.trading_rules import build_trading_rules
    from app.broker import execution
    from app.config.settings import Settings
    from app.recommendations import pipeline
    from app.workers import loops

    o2 = Settings(app_env="local", trading_mode="PAPER", auth_required=False, trading_config_path=str(O2_TOML))
    entry = build_trading_rules(o2)["active"]["sections"]["entry"]
    rule = next(item for item in entry if item["id"] == "index_chase")
    assert "0.75%" in rule["value"] and "QQQ_CHASE_075" in rule["explain"]
    k20 = Settings(app_env="local", trading_mode="PAPER", auth_required=False)
    entry = build_trading_rules(k20)["active"]["sections"]["entry"]
    assert next(item for item in entry if item["id"] == "index_chase")["value"] == "כבוי"
    root = Path(__file__).resolve().parents[1]
    latest = json.loads((root / "app" / "config" / "releases.json").read_text(encoding="utf-8"))["releases"][0]
    assert latest["version"] == "2026.11.23.1"
    assert any("QQQ_CHASE_075" in line for line in latest["changes"])
    assert "index_chase_reading" in inspect.getsource(loops._submit_autonomous_buys)
    assert "index_chase_gate" in inspect.getsource(execution)
    assert "index_chase_reading" in inspect.getsource(pipeline.run_scan)
