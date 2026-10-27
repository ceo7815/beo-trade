import re
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"

CLOSED_ROUTES = (
    ("GET", "/api/v1/system"),
    ("GET", "/api/v1/report/trades"),
    ("GET", "/api/v1/learning/trades"),
    ("GET", "/metrics"),
    ("POST", "/api/v1/control/close-all"),
    ("POST", "/api/v1/control/trading"),
    ("POST", "/api/v1/scan"),
    ("POST", "/api/v1/paper/trades"),
    ("POST", "/api/v1/paper/trades/trade-1/exit"),
    ("POST", "/api/v1/backtests"),
    ("POST", "/api/v1/watchlist"),
    ("DELETE", "/api/v1/watchlist/TSLA"),
    ("POST", "/api/v1/recommendations/rec-1/approve"),
    ("POST", "/api/v1/integrations/alpaca/secrets"),
    ("POST", "/api/v1/integrations/alpaca/test"),
    ("POST", "/api/v1/broker/alpaca/orders"),
    ("DELETE", "/api/v1/broker/alpaca/orders/ord-1"),
    ("POST", "/api/v1/broker/alpaca/updates"),
    ("POST", "/api/v1/broker/alpaca/sync"),
    ("POST", "/api/v1/learning/backfill"),
)


def _client(tmp_path, monkeypatch, admin: str = ADMIN, read: str = READ, allow_open_api: bool = False) -> TestClient:
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "keys.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=admin,
        api_read_key=read,
    )
    return TestClient(create_app(settings, allow_open_api=allow_open_api))


def test_without_a_key_only_health_and_ready_answer(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        for method, path in CLOSED_ROUTES:
            response = client.request(method, path)
            assert response.status_code == 401, path
            assert "symbol" not in response.text
        assert client.get("/api/v1/system", headers={"X-Beo-Key": "wrong"}).status_code == 401
        assert client.get("/api/v1/report/trades", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_the_admin_key_reaches_everything(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/system", headers={"X-Beo-Key": ADMIN}).status_code == 200
        assert client.get("/api/v1/integrations", headers={"X-Beo-Key": ADMIN}).status_code == 200
        assert client.get("/api/v1/report/trades", headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 200


def test_the_read_key_reads_the_allowed_pages_and_nothing_else(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        for headers in ({"X-Beo-Key": READ}, {"Authorization": f"Bearer {READ}"}):
            assert client.get("/api/v1/system", headers=headers).status_code == 200
            assert client.get("/api/v1/desk", headers=headers).status_code == 200
            assert client.get("/api/v1/report/trades", headers=headers).status_code == 200
            assert client.get("/api/v1/learning/trades", headers=headers).status_code == 200
        assert client.get("/api/v1/integrations", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.get("/api/v1/broker/alpaca/account", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.get("/api/v1/settings", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.post("/api/v1/control/close-all", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.post("/api/v1/control/trading", headers={"X-Beo-Key": READ}, json={"halted": True}).status_code == 403
        assert client.post("/api/v1/integrations/openai/secrets", headers={"X-Beo-Key": READ}, json={}).status_code == 403


def test_query_string_key_is_ignored(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/system", params={"key": ADMIN}).status_code == 401
        assert client.get("/api/v1/risk", params={"key": READ}).status_code == 401
        assert client.get("/api/v1/report/trades", params={"key": READ}).status_code == 401
        assert client.post("/api/v1/control/close-all", params={"key": ADMIN}).status_code == 401
        kept = client.get("/api/v1/desk", params={"key": "ignored"}, headers={"X-Beo-Key": READ})
        assert kept.status_code == 200


def test_without_an_admin_key_the_api_fails_closed(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, admin="", read=READ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        for method, path in CLOSED_ROUTES:
            response = client.request(method, path)
            assert response.status_code == 503, path
            assert response.json()["detail"]
            assert "symbol" not in response.text
        still_closed = client.get("/api/v1/report/trades", headers={"X-Beo-Key": READ})
        assert still_closed.status_code == 503
        assert client.get("/api/v1/learning/trades", headers={"Authorization": f"Bearer {READ}"}).status_code == 503


def test_open_api_flag_does_not_bypass_a_configured_key(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, allow_open_api=True) as client:
        assert client.get("/api/v1/report/trades").status_code == 401
        assert client.post("/api/v1/control/close-all").status_code == 401


def test_trading_process_does_not_call_the_dashboard_proxy():
    root = Path(__file__).resolve().parents[1] / "app"
    for name in ("workers", "paper_trading", "options", "broker"):
        for path in (root / name).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "BACKEND_URL" not in text, path
            assert "/backend/" not in text, path
            assert "x-beo-key" not in text.lower(), path
            assert "import httpx" not in text, path
            assert "import urllib" not in text, path
            assert "import requests" not in text, path


def test_thetadata_client_key_is_not_hardcoded():
    root = Path(__file__).resolve().parents[1] / "app"
    pattern = re.compile(
        r"""(?i)(?:key|secret|token)\s*=\s*['"][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}['"]"""
    )
    for path in root.rglob("*.py"):
        assert pattern.search(path.read_text(encoding="utf-8")) is None, path
    assert Settings().thetadata_client_key == ""
