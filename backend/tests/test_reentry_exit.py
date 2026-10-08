from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from app.broker.execution import _resolve_client_order_id, client_order_id_for
from app.config.settings import Settings
from app.integrations.alpaca import PaperOnlyError
from app.models.db import configure_database, init_db, session_scope
from app.models.tables import PositionState
from app.positions.state import load_open_state, locked_levels, open_state, rebase_entry, retire_absent


SYMBOL = "HOOD261009P00110000"
NOW = datetime(2026, 10, 7, 19, 51, tzinfo=timezone.utc)


def _session_without_local_orders() -> Mock:
    session = Mock()
    session.scalar.return_value = None
    return session


def _broker(known: dict[str, str]) -> Mock:
    adapter = Mock()
    adapter.get_order_by_client_id.side_effect = lambda cid: {"client_order_id": cid, "internal_state": known[cid]} if cid in known else None
    return adapter


def test_a_filled_sell_from_an_earlier_round_trip_does_not_block_the_next_exit():
    first = client_order_id_for(SYMBOL, "sell_to_close")
    adapter = _broker({first: "FILLED"})
    client_order_id, existing, remote = _resolve_client_order_id(_session_without_local_orders(), adapter, SYMBOL, "sell_to_close")
    assert client_order_id == client_order_id_for(SYMBOL, "sell_to_close", 1)
    assert client_order_id != first
    assert existing is None and remote is None


def test_a_live_exit_order_is_still_not_resent():
    first = client_order_id_for(SYMBOL, "sell_to_close")
    second = client_order_id_for(SYMBOL, "sell_to_close", 1)
    adapter = _broker({first: "EXPIRED", second: "ACCEPTED"})
    client_order_id, _, remote = _resolve_client_order_id(_session_without_local_orders(), adapter, SYMBOL, "sell_to_close")
    assert client_order_id == second
    assert remote is not None


def test_a_filled_buy_is_never_bought_again():
    first = client_order_id_for("trade-1", "buy_to_open")
    adapter = _broker({first: "FILLED"})
    client_order_id, _, remote = _resolve_client_order_id(_session_without_local_orders(), adapter, "trade-1", "buy_to_open")
    assert client_order_id == first
    assert remote is not None


def test_unknown_exit_state_stays_blocked():
    adapter = Mock()
    adapter.get_order_by_client_id.side_effect = TimeoutError("lost")
    with pytest.raises(PaperOnlyError, match="לא ידוע"):
        _resolve_client_order_id(_session_without_local_orders(), adapter, SYMBOL, "sell_to_close")


def _settings(tmp_path) -> Settings:
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "state.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    return settings


def _enter(session, config, price: str, quantity: int, at: datetime) -> PositionState:
    return open_state(
        session,
        trade_id=SYMBOL,
        symbol=SYMBOL,
        underlying="HOOD",
        entry_price=Decimal(price),
        quantity=quantity,
        entry_time=at,
        config=config,
    )


def test_a_closed_contract_reentered_gets_a_fresh_record(tmp_path):
    config = _settings(tmp_path).trading()
    with session_scope() as session:
        _enter(session, config, "2.33", 21, NOW - timedelta(hours=3))
        session.commit()
    with session_scope() as session:
        assert retire_absent(session, set()) == 1
        session.commit()
    with session_scope() as session:
        assert load_open_state(session, SYMBOL) is None
        _enter(session, config, "2.25", 21, NOW)
        session.commit()
    with session_scope() as session:
        row = load_open_state(session, SYMBOL)
        assert row is not None
        assert Decimal(str(row.entry_fill_price)) == Decimal("2.25")
        assert row.entry_time.replace(tzinfo=timezone.utc) == NOW


def test_a_held_contract_is_not_retired(tmp_path):
    config = _settings(tmp_path).trading()
    with session_scope() as session:
        _enter(session, config, "2.25", 21, NOW)
        session.commit()
    with session_scope() as session:
        assert retire_absent(session, {SYMBOL}) == 0


def test_a_stale_entry_is_reanchored_to_the_broker_and_keeps_the_peak(tmp_path):
    config = _settings(tmp_path).trading()
    with session_scope() as session:
        row = _enter(session, config, "2.29", 18, NOW)
        row.peak_option_price = 6.10
        assert rebase_entry(row, Decimal("2.79"), 17, config) is True
        levels = locked_levels(row)
        one_r = Decimal("2.79") * Decimal(str(config.initial_stop_decline_pct))
        assert row.entry_fill_quantity == 17
        assert levels["protected_stop_price"] == Decimal(str(float(Decimal("2.79") - one_r * Decimal(str(config.protective_stop_r)))))
        assert levels["trailing_active"] is True
        assert levels["trailing_trigger"] == Decimal(str(float(Decimal("6.10") * (Decimal("1") - Decimal(str(config.trailing_pct))))))
        assert rebase_entry(row, Decimal("2.79"), 17, config) is False
