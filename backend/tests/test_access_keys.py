from decimal import Decimal

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"


def _client(tmp_path, monkeypatch, admin: str = ADMIN, read: str = READ) -> TestClient:
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
    return TestClient(create_app(settings))


def test_without_a_key_only_health_and_ready_answer(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.get("/api/v1/system").status_code == 401
        assert client.post("/api/v1/control/close-all").status_code == 401
        assert client.get("/api/v1/system", headers={"X-Beo-Key": "wrong"}).status_code == 401


def test_the_admin_key_reaches_everything(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/system", headers={"X-Beo-Key": ADMIN}).status_code == 200
        assert client.get("/api/v1/integrations", headers={"X-Beo-Key": ADMIN}).status_code == 200


def test_the_read_key_reads_the_allowed_pages_and_nothing_else(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        for headers in ({"X-Beo-Key": READ}, {"Authorization": f"Bearer {READ}"}):
            assert client.get("/api/v1/system", headers=headers).status_code == 200
            assert client.get("/api/v1/desk", headers=headers).status_code == 200
        assert client.get(f"/api/v1/risk?key={READ}").status_code == 200
        assert client.get("/api/v1/integrations", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.get("/api/v1/broker/alpaca/account", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.get("/api/v1/settings", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.post("/api/v1/control/close-all", headers={"X-Beo-Key": READ}).status_code == 403
        assert client.post("/api/v1/control/trading", headers={"X-Beo-Key": READ}, json={"halted": True}).status_code == 403
        assert client.post("/api/v1/integrations/openai/secrets", headers={"X-Beo-Key": READ}, json={}).status_code == 403


def test_without_an_admin_key_configured_the_api_stays_open(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch, admin="", read="") as client:
        assert client.get("/api/v1/system").status_code == 200
