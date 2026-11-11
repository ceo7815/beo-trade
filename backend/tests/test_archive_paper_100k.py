"""Paper 100K archive: read-only Alpaca copy, idempotent, admin trigger, read key."""

import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.archive.job import run_paper_100k_archive
from app.archive.reader import ArchiveNotConfigured, ReadOnlyAlpaca
from app.archive.tables import ArchiveAccount, ArchivePosition
from app.config.settings import Settings
from app.main import create_app
from app.models.db import session_scope
from app.models.tables import BrokerTrade, BrokerTradeEvent

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"


class FakeReader:
    def __init__(self) -> None:
        self.order_calls = 0
        self.activity_calls = 0
        self.methods: list[str] = []

    def account(self) -> dict:
        self.methods.append("account")
        return {
            "equity": "100000.12",
            "cash": "40000.00",
            "last_equity": "99000.00",
            "account_number": "PA3Y5GUBNXN0",
            "id": "do-not-store",
        }

    def positions(self) -> list[dict]:
        self.methods.append("positions")
        return [{"symbol": "AAPL", "qty": "1"}]

    def orders_page(self, *, until, limit):
        self.methods.append("orders")
        self.order_calls += 1
        if until:
            return [
                {
                    "id": "o-old",
                    "status": "canceled",
                    "symbol": "QQQ",
                    "submitted_at": "2026-01-01T00:00:00Z",
                    "side": "buy",
                    "qty": "1",
                }
            ]
        return [
            {
                "id": "o-new",
                "status": "filled",
                "symbol": "SPY",
                "submitted_at": "2026-06-01T00:00:00Z",
                "side": "buy",
                "qty": "2",
                "filled_avg_price": "1.20",
            },
            {
                "id": "o-mid",
                "status": "expired",
                "symbol": "IWM",
                "submitted_at": "2026-03-01T00:00:00Z",
                "side": "sell",
                "qty": "1",
            },
        ][:limit]

    def activities_page(self, *, page_token, limit):
        self.methods.append("activities")
        self.activity_calls += 1
        if page_token:
            return [{"id": "act-2", "activity_type": "DIV", "symbol": "SPY", "date": "2026-02-01"}]
        return [
            {
                "id": "act-1",
                "activity_type": "FILL",
                "symbol": "SPY",
                "side": "buy",
                "qty": "1",
                "price": "1.10",
                "transaction_time": "2026-06-01T14:00:00Z",
            },
            {
                "id": "act-3",
                "activity_type": "FILL",
                "symbol": "QQQ",
                "side": "sell",
                "qty": "1",
                "price": "2.20",
                "transaction_time": "2026-05-01T14:00:00Z",
            },
        ][:limit]

    def portfolio_history(self, period: str, timeframe: str) -> dict:
        self.methods.append(f"history:{period}")
        if period == "1W":
            raise RuntimeError("unavailable")
        if period != "all":
            return {"timestamp": [1_700_000_000], "equity": [100], "profit_loss": [0], "profit_loss_pct": [0]}
        return {
            "timestamp": [1_700_000_000, 1_700_086_400, 1_700_172_800],
            "equity": [100000, 100100, 100050],
            "profit_loss": [0, 100, 50],
            "profit_loss_pct": [0, 0.001, 0.0005],
        }


def _client(tmp_path, monkeypatch) -> tuple[TestClient, FakeReader]:
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    fake = FakeReader()
    monkeypatch.setattr("app.archive.routes.build_reader", lambda settings: fake)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "archive.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=ADMIN,
        api_read_key=READ,
    )
    return TestClient(create_app(settings)), fake


def _seed_journal() -> None:
    inside = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
    outside = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
    with session_scope() as session:
        session.add(BrokerTrade(id="trade-in", state="closed", source="journal", created_at=inside))
        session.add(
            BrokerTradeEvent(
                trade_id="trade-in",
                state="exit",
                source="journal",
                reason="STOP",
                created_at=inside,
            )
        )
        session.add(BrokerTrade(id="trade-out", state="closed", source="journal", created_at=outside))
        session.commit()


def test_archive_copies_pages_and_is_idempotent(tmp_path, monkeypatch):
    client, fake = _client(tmp_path, monkeypatch)
    with client:
        _seed_journal()
        headers = {"X-Beo-Key": ADMIN}
        with session_scope() as session:
            body = run_paper_100k_archive(session, fake, order_page_size=2, activity_page_size=2)
        assert body["ok"] is True
        assert body["counts"]["orders"] == 3
        assert body["counts"]["activities"] == 3
        assert body["counts"]["days"] == 3
        assert body["counts"]["positions"] == 1
        assert body["counts"]["local_rows"] == 2
        assert body["account"]["last4"] == "NXN0"
        assert body["account"]["account_match"] is True
        assert body["account"]["equity"] == "100000.12"
        assert "equity_skip:1W:" in body["run"]["detail"]
        assert "submit" not in fake.methods
        assert fake.order_calls >= 2
        assert fake.activity_calls >= 2

        posted = client.post("/api/v1/archive/paper_100k/run", headers=headers)
        assert posted.status_code == 202, posted.text
        assert posted.json()["run"]["id"]
        again = _wait_archive(client, headers, posted.json()["run"]["id"])["counts"]
        assert again["orders"] == 3
        assert again["activities"] == 3
        assert again["days"] == 3
        assert again["local_rows"] == 2
        assert again["positions"] == 1

        with session_scope() as session:
            stored = session.get(ArchiveAccount, "snapshot")
            assert stored is not None
            assert "account_number" not in stored.payload
            assert "id" not in stored.payload
            snapshots = session.query(ArchivePosition).count()
            assert snapshots == 2

        read = {"X-Beo-Key": READ}
        summary = client.get("/api/v1/archive/paper_100k/summary", headers=read)
        assert summary.status_code == 200
        assert summary.json()["counts"]["orders"] == 3
        assert summary.json()["counts"]["activities"] == 3
        assert summary.json()["counts"]["days"] == 3
        orders = client.get("/api/v1/archive/paper_100k/orders", headers=read, params={"limit": 2, "offset": 0})
        assert orders.status_code == 200
        assert orders.json()["total"] == 3
        assert len(orders.json()["items"]) == 2
        equity = client.get("/api/v1/archive/paper_100k/equity", headers=read, params={"period": "all"})
        assert equity.status_code == 200
        points = equity.json()["points"]
        assert equity.json()["days"] == 3
        assert points[1]["day_pnl"] == "100.00"
        assert points[2]["day_pnl"] == "-50.00"
        activities = client.get("/api/v1/archive/paper_100k/activities", headers=read)
        assert activities.status_code == 200
        assert activities.json()["total"] == 3
        trades = client.get("/api/v1/archive/paper_100k/journal", headers=read)
        assert trades.status_code == 200
        sources = {item["source"] for item in trades.json()["items"]}
        assert "broker_trades" in sources
        assert "broker_trade_events" in sources
        assert "fill" in sources
        systems = client.get("/api/v1/systems", headers=read).json()["systems"]
        assert [row["id"] for row in systems] == ["options", "eli", "options2"]
        assert systems[2]["remote"] is True
        assert client.get("/api/v1/archive/paper_100k/summary").status_code == 401
        assert client.post("/api/v1/archive/paper_100k/run", headers=read).status_code == 403


def test_missing_keys_archive_local_only_and_do_not_call_alpaca(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))

    def _missing(settings):
        raise ArchiveNotConfigured("missing")

    monkeypatch.setattr("app.archive.routes.build_reader", _missing)

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "local-only.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=ADMIN,
        api_read_key=READ,
    )
    with TestClient(create_app(settings)) as client:
        _seed_journal()
        response = client.post("/api/v1/archive/paper_100k/run", headers={"X-Beo-Key": ADMIN})
        assert response.status_code == 202, response.text
        assert "ALPACA_100K" in response.json()["detail"]
        body = _wait_archive(client, {"X-Beo-Key": ADMIN}, response.json()["run"]["id"])
        assert body["counts"]["orders"] == 0
        assert body["counts"]["local_rows"] == 2
        assert body["last_run"]["status"] == "local_only"


def _wait_archive(client: TestClient, headers: dict, run_id: str) -> dict:
    last = {}
    for _ in range(50):
        last = client.get("/api/v1/archive/paper_100k/summary", headers=headers).json()
        row = last.get("last_run") or {}
        if row.get("id") == run_id and row.get("finished_at"):
            return last
        time.sleep(0.05)
    raise AssertionError(last)


def test_reader_refuses_writes():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.url.path.endswith("/account"):
            return httpx.Response(200, json={"equity": "1", "cash": "1", "last_equity": "1"})
        return httpx.Response(500, json={"detail": "unexpected"})

    client = ReadOnlyAlpaca("key", "secret", transport=httpx.MockTransport(handler))
    try:
        assert client.account()["equity"] == "1"
        for method, path in (("POST", "/v2/orders"), ("DELETE", "/v2/orders/abc"), ("PATCH", "/v2/orders/abc")):
            try:
                client._send(method, path)
            except RuntimeError as exc:
                assert "read-only" in str(exc)
            else:
                raise AssertionError(method)
        assert seen == ["GET"]
        assert not hasattr(client, "submit")
        assert not hasattr(client, "cancel")
    finally:
        client.close()


def test_archive_package_never_places_orders():
    root = Path(__file__).resolve().parents[1] / "app" / "archive"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").replace("@app.post", "")
        lowered = text.lower()
        assert ".post(" not in lowered
        assert "submit(" not in lowered
        assert ".delete(" not in lowered
        assert "cancel(" not in lowered
        assert "session.delete" not in lowered
        assert '"post"' not in lowered
        assert '"delete"' not in lowered
        assert '"patch"' not in lowered
