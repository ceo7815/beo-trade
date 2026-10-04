from unittest.mock import Mock

import pytest

from app.broker.execution import place_order
from app.control.halt import is_halted, set_halted
from app.integrations.alpaca import PaperOnlyError


def test_halt_file_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr("app.control.halt.HALT_PATH", tmp_path / "trading_halt.txt")
    assert is_halted() is False
    body = set_halted(True)
    assert body["halted"] is True
    assert is_halted() is True
    set_halted(False)
    assert is_halted() is False


def test_halt_blocks_a_new_buy_and_allows_a_protective_sell(monkeypatch):
    monkeypatch.setattr("app.broker.execution.is_halted", lambda: True)
    adapter = Mock()
    with pytest.raises(PaperOnlyError, match="כניסות חדשות כבויות"):
        place_order(None, None, adapter, {"position_intent": "buy_to_open"})
    assert adapter.mock_calls == []
    adapter.get_order_by_client_id.return_value = {"client_order_id": "known", "status": "accepted"}
    session = Mock()
    session.scalar.return_value = None
    session.get.return_value = None
    result = place_order(
        None,
        session,
        adapter,
        {"position_intent": "sell_to_close", "symbol": "NVDA", "qty": 1, "limit_price": "1.00", "trade_id": "exit-1"},
    )
    assert result["duplicate"] is True
    adapter.submit_exit_order.assert_not_called()


def test_unknown_broker_state_is_not_resent(monkeypatch):
    monkeypatch.setattr("app.broker.execution.is_halted", lambda: False)
    adapter = Mock()
    adapter.get_order_by_client_id.side_effect = TimeoutError("lost")
    session = Mock()
    session.scalar.return_value = None
    with pytest.raises(PaperOnlyError, match="לא ידוע"):
        place_order(
            None,
            session,
            adapter,
            {"position_intent": "buy_to_open", "symbol": "NVDA", "qty": 1, "limit_price": "1.00", "trade_id": "buy-1"},
        )
    adapter.submit_entry_order.assert_not_called()
