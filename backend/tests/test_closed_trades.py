"""Finance, history, and the reconciliation check share one closed-trade book."""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.analytics.closed_trades import canonical_book, reconcile_closed, warn_mismatch
from app.analytics.read_cache import read_cache
from app.broker.store import remember_fill
from app.config.settings import Settings
from app.main import create_app
from app.models.db import session_scope

AMD = "AMD261030C00100000"
INTC = "INTC261030P00050000"
QQQ = "QQQ261030C00600000"


def _fill(execution_id: str, stamp: str, symbol: str, side: str, qty: str, price: str) -> dict:
    return {
        "execution_id": execution_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "timestamp": stamp,
    }


def _live_fills() -> list[dict]:
    return [
        _fill("amd-buy", "2026-10-07T15:00:00Z", AMD, "buy", "1", "2"),
        _fill("amd-sell", "2026-10-07T16:00:00Z", AMD, "sell", "1", "3"),
        _fill("intc-buy", "2026-10-08T15:00:00Z", INTC, "buy", "2", "1.50"),
        _fill("intc-sell", "2026-10-08T18:00:00Z", INTC, "sell", "2", "1.00"),
    ]


def _qqq_fills() -> list[dict]:
    return [
        _fill("qqq-buy", "2026-10-08T15:30:00Z", QQQ, "buy", "1", "1"),
        _fill("qqq-sell", "2026-10-08T17:00:00Z", QQQ, "sell", "1", "1.20"),
    ]


class _Adapter:
    def __init__(self, fills: list[dict]):
        self.fills = fills

    def get_all_fills(self, max_pages: int = 20, after: str = ""):
        return list(self.fills)

    def get_positions(self):
        return []

    def get_account(self):
        return {
            "equity": "100000",
            "last_equity": "100000",
            "cash": "100000",
            "buying_power": "100000",
            "portfolio_value": "100000",
        }

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D"):
        return {"points": []}

    def get_activities(self):
        return []

    def close(self):
        return None


def _settings(tmp_path) -> Settings:
    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "closed.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )


def test_finance_totals_equal_history_for_the_same_fills(tmp_path, monkeypatch):
    """Stored fills and Alpaca fills are one book. A live fill replaces a stored price."""
    read_cache.clear()
    live = _live_fills()
    monkeypatch.setattr("app.analytics.routes.open_paper_broker", lambda settings: _Adapter(live))
    with TestClient(create_app(_settings(tmp_path), allow_open_api=True)) as client:
        with session_scope() as session:
            remember_fill(session, _fill("amd-buy", "2026-10-07T15:00:00Z", AMD, "buy", "1", "9.99"))
            remember_fill(session, _fill("amd-sell", "2026-10-07T16:00:00Z", AMD, "sell", "1", "9.99"))
            for row in _qqq_fills():
                remember_fill(session, row)
            session.commit()
        read_cache.clear()
        finance = client.get("/api/v1/finance").json()
        history = client.get("/api/v1/report/trades", params={"period": "all"}).json()
    assert history["available"] is True
    assert history["summary"]["closed_count"] == 3
    assert history["summary"]["realized_pnl"] == "20.00"
    assert finance["trades"]["count"] == history["summary"]["closed_count"]
    assert finance["trades"]["gross"] == history["summary"]["realized_pnl"]
    assert finance["trades"]["net"] == history["summary"]["realized_pnl"]
    assert finance["trades"]["win_rate_pct"] == history["summary"]["win_rate_pct"]
    assert finance["trades"]["profit_factor"] == history["summary"]["profit_factor"]
    assert Decimal(finance["trades"]["win_rate"]) * 100 == Decimal(history["summary"]["win_rate_pct"])
    amd = next(row for row in history["closed"] if row["symbol"] == AMD)
    assert amd["pnl"] == "100.00"
    assert amd["entry_price"] == "2"
    assert any(row["symbol"] == QQQ and row["pnl"] == "20.00" for row in finance["closed_trades"])


def test_reconciliation_detects_a_missing_trade():
    fills = _live_fills()
    closed = canonical_book(fills, [], multiplier=100)["closed"]
    assert len(closed) == 2
    short = closed[1:]
    body = reconcile_closed(short, fills, multiplier=100)
    assert body["mismatch"] is True
    assert body["count_diff"] == -1
    assert Decimal(body["usd_diff"]) != 0
    quiet = reconcile_closed(closed, fills, multiplier=100)
    assert quiet["mismatch"] is False
    assert quiet["count_diff"] == 0
    assert quiet["usd_diff"] == "0.00"

    outside = canonical_book(fills + _qqq_fills(), [], multiplier=100)["closed"]
    kept = [row for row in outside if not str(row.get("closed_at") or "").startswith("2026-10-08T17")]
    ranged = reconcile_closed(kept, fills + _qqq_fills(), start="2026-10-08", end="2026-10-08", multiplier=100)
    assert ranged["mismatch"] is True
    assert ranged["count_diff"] == -1
    assert Decimal(ranged["usd_diff"]) == Decimal("-20.00")


def test_reconciliation_endpoint_warns_when_a_trade_is_missing(tmp_path, monkeypatch):
    read_cache.clear()
    fills = _live_fills()
    monkeypatch.setattr("app.analytics.routes.open_paper_broker", lambda settings: _Adapter(fills))
    from app.analytics import closed_trades as book_module
    from app.analytics import routes as routes_module

    def short_book(*args, **kwargs):
        report = book_module.canonical_book(*args, **kwargs)
        report["closed"] = report["closed"][:-1]
        return report

    monkeypatch.setattr(routes_module, "canonical_book", short_book)
    warnings = []
    monkeypatch.setattr(book_module.log, "warning", lambda event, **kwargs: warnings.append((event, kwargs)))
    with TestClient(create_app(_settings(tmp_path), allow_open_api=True)) as client:
        body = client.get("/api/v1/finance/reconciliation", params={"start": "2026-10-01", "end": "2026-10-31"}).json()
    assert body["available"] is True
    assert body["mismatch"] is True
    assert body["count_diff"] == -1
    assert Decimal(body["usd_diff"]) != 0
    assert warnings and warnings[0][0] == "closed_trade_reconciliation_mismatch"
    assert warnings[0][1]["count_diff"] == -1
    assert Decimal(warnings[0][1]["usd_diff"]) != 0
    assert warn_mismatch({"mismatch": False, "count_diff": 0, "usd_diff": "0.00"})["mismatch"] is False
    assert len(warnings) == 1
