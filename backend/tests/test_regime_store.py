"""The worker writes the regime where the API can read it. The scanner still uses its own dict."""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

import app.market.regime as regime
from app.analytics.read_cache import read_cache
from app.config.settings import Settings
from app.main import create_app
from app.market.regime import last_regime, read_shared_regime, regime_from_context
from app.models.db import session_scope
from app.models.store import paper_equity
from app.models.tables import BrokerAccountSnapshot

QUOTES = {
    "SPY": {"symbol": "SPY", "price": 500, "change_percent": 0.4},
    "QQQ": {"symbol": "QQQ", "price": 400, "change_percent": 0.5},
    "IWM": {"symbol": "IWM", "price": 200, "change_percent": 0.3},
    "VIX": {"symbol": "VIX", "price": 18, "change_percent": None},
}


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(regime, "store_path", lambda: tmp_path / "regime.json")
    regime._LAST = {"status": "WAITING_FOR_MARKET_DATA"}
    read_cache.clear()


def test_a_calculated_regime_round_trips_without_changing_scanner_memory(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    ready = regime_from_context(QUOTES, {"source": "fred", "series": [{"id": "FEDFUNDS", "value": 3.75, "date": "2026-09-01"}]})
    assert ready["status"] == "CALCULATED"
    assert ready["market_regime"] == "UP/CALM/OPEN"
    assert "updated_at" not in ready
    assert "inputs" not in ready
    assert last_regime() == ready

    regime._LAST = {"status": "WAITING_FOR_MARKET_DATA"}
    assert last_regime()["status"] == "WAITING_FOR_MARKET_DATA"

    shared = read_shared_regime()
    assert shared["status"] == "CALCULATED"
    assert shared["market_regime"] == "UP/CALM/OPEN"
    assert shared["trend_state"] == "UP"
    assert shared["volatility_state"] == "CALM"
    assert shared["risk_state"] == "OPEN"
    assert shared["inputs"]["spy"] == {"price": 500.0, "change_percent": 0.4}
    assert shared["inputs"]["vix"]["price"] == 18.0
    assert shared["inputs"]["macro"]["series"][0]["id"] == "FEDFUNDS"
    updated = datetime.fromisoformat(shared["updated_at"])
    assert updated.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - updated).total_seconds()) < 10
    assert last_regime()["status"] == "WAITING_FOR_MARKET_DATA"


def test_waiting_is_persisted_and_a_broken_file_does_not_invent_a_regime(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    waiting = regime_from_context({}, {"source": "fred", "series": []})
    assert waiting["status"] == "WAITING_FOR_MARKET_DATA"
    assert "updated_at" not in waiting
    shared = read_shared_regime()
    assert shared["status"] == "WAITING_FOR_MARKET_DATA"
    assert shared["market_regime"] is None
    assert shared["inputs"]["spy"] is None
    assert shared["updated_at"]

    (tmp_path / "regime.json").write_text("{", encoding="utf-8")
    missing = read_shared_regime()
    assert missing["status"] == "WAITING_FOR_MARKET_DATA"
    assert missing["updated_at"] is None


def test_a_failed_write_still_returns_the_same_regime(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)

    def broken():
        raise OSError("disk")

    monkeypatch.setattr(regime, "store_path", broken)
    ready = regime_from_context(QUOTES)
    assert ready["market_regime"] == "UP/CALM/OPEN"
    assert last_regime()["market_regime"] == "UP/CALM/OPEN"
    assert "updated_at" not in last_regime()


def test_the_api_reads_the_saved_regime_and_alpaca_equity(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    regime_from_context(QUOTES)
    regime._LAST = {"status": "WAITING_FOR_MARKET_DATA"}
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "beo.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    with TestClient(create_app(settings, allow_open_api=True)) as client:
        before = client.get("/api/v1/system").json()
        assert before["regime_status"]["market_regime"] == "UP/CALM/OPEN"
        assert before["regime_status"]["updated_at"]
        assert before["paper_equity"] is None
        assert before["equity_basis"] == "unavailable"
        assert before["paper_equity"] != "1000.00"
        settings_view = client.get("/api/v1/settings").json()
        assert "paper_account_balance" not in settings_view["thresholds"]
        with session_scope() as session:
            user_id = settings.dev_user_id
            assert paper_equity(session, user_id) == Decimal("1000.00")
            session.add(
                BrokerAccountSnapshot(
                    payload={"equity": "73421.55", "cash": "12000"},
                    observed_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        after = client.get("/api/v1/system").json()
        assert after["paper_equity"] == "73421.55"
        assert after["equity_basis"] == "alpaca"
        health = client.get("/api/v1/health").json()
        assert health["items"]["regime"]["detail"] == "UP/CALM/OPEN"
        assert health["items"]["regime"]["updated_at"]
