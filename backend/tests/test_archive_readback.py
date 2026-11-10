"""Paper 100K journal copy, last_run, and reads that never call Alpaca."""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.archive.job import _pull_local_source, run_paper_100k_archive
from app.archive.tables import ArchiveAccount
from app.broker.store import remember_fill
from app.config.settings import Settings
from app.main import create_app
from app.models.db import session_scope

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"


class Fake:
    def account(self):
        return {"equity": "101000.00", "cash": "50000", "last_equity": "100000.00", "account_number": "PA3Y5GUBNXN0"}

    def positions(self):
        return []

    def orders_page(self, *, until, limit):
        return []

    def activities_page(self, *, page_token, limit):
        return []

    def portfolio_history(self, period, timeframe):
        return {"timestamp": [1_700_000_000, 1_700_086_400], "equity": [100000, 101000], "profit_loss": [0, 1000], "profit_loss_pct": [0, 0.01]}


def _settings(tmp_path) -> Settings:
    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "readback.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=ADMIN,
        api_read_key=READ,
    )


def _fill(execution_id: str, stamp: str, symbol: str, side: str, qty: str, price: str) -> dict:
    return {"execution_id": execution_id, "symbol": symbol, "side": side, "qty": qty, "price": price, "timestamp": stamp}


def test_stored_fill_timestamps_are_archived_and_last_run_is_visible(tmp_path, monkeypatch):
    real = _pull_local_source

    def flaky(session, spec, moment, **kwargs):
        if spec[1] == "position_states":
            raise RuntimeError("schema")
        return real(session, spec, moment, **kwargs)

    monkeypatch.setattr("app.archive.job._pull_local_source", flaky)
    with TestClient(create_app(_settings(tmp_path))) as client:
        with session_scope() as session:
            session.execute(
                text(
                    "insert into broker_fills (id, environment, execution_id, trade_id, broker_order_id, symbol, payload, observed_at) "
                    "values ('f-oct','PAPER','e-oct','','','SMCI','{\"execution_id\":\"e-oct\",\"symbol\":\"SMCI\",\"side\":\"buy\",\"qty\":\"1\",\"price\":\"2.4\",\"timestamp\":\"2026-10-07T15:04:00Z\"}','2026-10-07 15:04:00.000000')"
                )
            )
            session.execute(
                text(
                    "insert into broker_fills (id, environment, execution_id, trade_id, broker_order_id, symbol, payload, observed_at) "
                    "values ('f-late','PAPER','e-late','','','HOOD','{\"execution_id\":\"e-late\",\"symbol\":\"HOOD\",\"side\":\"buy\",\"qty\":\"1\",\"price\":\"1\",\"timestamp\":\"2026-10-20T15:00:00Z\"}','2026-11-06 15:00:00.000000')"
                )
            )
            session.execute(
                text(
                    "insert into broker_fills (id, environment, execution_id, trade_id, broker_order_id, symbol, payload, observed_at) "
                    "values ('f-after','PAPER','e-after','','','QQQ','{\"execution_id\":\"e-after\",\"symbol\":\"QQQ\",\"side\":\"buy\",\"qty\":\"1\",\"price\":\"1\",\"timestamp\":\"2026-11-06T15:00:00Z\"}','2026-11-06 15:00:00.000000')"
                )
            )
            session.commit()
            body = run_paper_100k_archive(session, Fake())
        assert body["counts"]["local_rows"] >= 2
        assert body["last_run"]["finished_at"]
        assert "position_states:RuntimeError" in body["run"]["detail"]
        summary = client.get("/api/v1/archive/paper_100k/summary", headers={"X-Beo-Key": READ})
        assert summary.status_code == 200
        last = summary.json()["last_run"]
        assert last["status"] in {"ok", "partial"}
        assert last["finished_at"]
        trades = client.get("/api/v1/archive/paper_100k/journal", headers={"X-Beo-Key": READ}).json()
        symbols = {item["symbol"] for item in trades["items"]}
        assert "SMCI" in symbols
        assert "HOOD" in symbols
        assert "QQQ" not in symbols


def test_paper_100k_reads_skip_alpaca(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("alpaca")

    monkeypatch.setattr("app.analytics.routes.open_paper_broker", boom)
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", boom)
    monkeypatch.setattr("app.broker.accounts.open_view_broker", boom)
    monkeypatch.setattr("app.broker.accounts.view_broker_reachable", boom)
    with TestClient(create_app(_settings(tmp_path))) as client:
        with session_scope() as session:
            session.add(
                ArchiveAccount(
                    id="snapshot",
                    equity="101000.00",
                    cash="50000.00",
                    last_equity="100000.00",
                    account_last4="NXN0",
                    payload={"account_last4": "NXN0"},
                    archived_at=datetime.now(timezone.utc),
                )
            )
            remember_fill(session, _fill("amd-buy", "2026-10-07T15:00:00Z", "AMD261030C00100000", "buy", "1", "2.00"))
            remember_fill(session, _fill("amd-sell", "2026-10-07T16:00:00Z", "AMD261030C00100000", "sell", "1", "3.00"))
            remember_fill(
                session,
                _fill("late-buy", "2026-11-06T15:00:00Z", "QQQ261106C00600000", "buy", "1", "1.00"),
                observed_at=datetime(2026, 11, 6, 15, tzinfo=timezone.utc),
            )
            session.commit()
        headers = {"X-Beo-Key": READ}
        report = client.get("/api/v1/report/trades", params={"period": "paper_100k"}, headers=headers)
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["available"] is True
        assert body["source"] == "archive"
        symbols = {row["symbol"] for row in body["closed"]}
        assert "AMD261030C00100000" in symbols
        assert "QQQ261106C00600000" not in symbols
        risk = client.get("/api/v1/risk", params={"trading_period": "paper_100k"}, headers=headers)
        assert risk.status_code == 200, risk.text
        assert risk.json()["state"] == "ארכיון"
        assert risk.json()["display_pnl"] == "1000.00"
        finance = client.get("/api/v1/finance", params={"trading_period": "paper_100k"}, headers=headers)
        assert finance.status_code == 200, finance.text
        assert finance.json()["available"] is True
        assert finance.json()["account"]["equity"] == "101000.00"
        periods = client.get("/api/v1/trading-periods", headers=headers)
        assert periods.status_code == 200
        paper = next(row for row in periods.json()["periods"] if row["id"] == "paper_100k")
        assert paper["view_mode"] == "archive"
