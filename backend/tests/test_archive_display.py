"""Paper 100K display reads: New York sessions, equity levels, closed trades. No writes."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.archive.tables import ArchiveAccount, ArchiveEquity, ArchiveLocal
from app.config.settings import Settings
from app.main import create_app
from app.models.db import session_scope
from app.models.tables import BrokerFillRecord

READ = "read-key-0123456789abcdef"
ADMIN = "admin-key-0123456789abcdef"

SESSIONS = [
    date(2026, 10, 7),
    date(2026, 10, 8),
    date(2026, 10, 9),
    date(2026, 10, 12),
    date(2026, 10, 13),
    date(2026, 10, 14),
    date(2026, 10, 15),
    date(2026, 10, 16),
    date(2026, 10, 19),
    date(2026, 10, 20),
    date(2026, 10, 21),
    date(2026, 10, 22),
    date(2026, 10, 23),
    date(2026, 10, 26),
    date(2026, 10, 27),
    date(2026, 10, 28),
    date(2026, 10, 29),
    date(2026, 10, 30),
    date(2026, 11, 2),
]
CHANGES = {
    date(2026, 10, 7): Decimal("4048.20"),
    date(2026, 10, 14): Decimal("15078.00"),
    date(2026, 10, 23): Decimal("-8171.00"),
}


def _settings(tmp_path, name: str) -> Settings:
    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / name).as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=ADMIN,
        api_read_key=READ,
    )


def _utc(day: date) -> int:
    """Midnight UTC on the next calendar day. New York still calls that the session `day`."""
    moment = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
    return int(moment.timestamp())


def _seed_equity() -> None:
    equity = Decimal("100000.00")
    rows = [
        ArchiveEquity(
            period="all",
            timeframe="1D",
            ts=_utc(date(2026, 10, 6)),
            equity=str(equity),
            profit_loss="0",
            profit_loss_pct="0",
            archived_at=datetime.now(timezone.utc),
        )
    ]
    for day in SESSIONS:
        equity += CHANGES.get(day, Decimal("0"))
        rows.append(
            ArchiveEquity(
                period="all",
                timeframe="1D",
                ts=_utc(day),
                equity=f"{equity:.2f}",
                profit_loss=str(CHANGES.get(day, Decimal("0"))),
                profit_loss_pct="0",
                archived_at=datetime.now(timezone.utc),
            )
        )
    with session_scope() as session:
        session.add(
            ArchiveAccount(
                id="snapshot",
                equity="110955.20",
                cash="110955.20",
                last_equity="110955.20",
                account_last4="NXN0",
                payload={"account_last4": "NXN0"},
                archived_at=datetime.now(timezone.utc),
            )
        )
        session.add_all(rows)
        session.commit()
    assert equity == Decimal("110955.20")


def _fill(execution_id: str, symbol: str, side: str, price: str, stamp: datetime) -> BrokerFillRecord:
    return BrokerFillRecord(
        id=execution_id,
        environment="PAPER",
        execution_id=execution_id,
        trade_id="",
        broker_order_id="",
        symbol=symbol,
        payload={
            "execution_id": execution_id,
            "symbol": symbol,
            "side": side,
            "qty": "1",
            "price": price,
            "timestamp": stamp.astimezone(timezone.utc).isoformat(),
        },
        observed_at=stamp,
    )


def test_archive_finance_uses_new_york_sessions_and_equity_levels(tmp_path):
    assert len(SESSIONS) == 19
    with TestClient(create_app(_settings(tmp_path, "display.db"))) as client:
        _seed_equity()
        opened = datetime(2026, 10, 14, 14, 30, tzinfo=timezone.utc)
        closed = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
        with session_scope() as session:
            session.add(_fill("amd-buy", "AMD261030C00100000", "buy", "1.00", opened))
            session.add(_fill("amd-sell", "AMD261030C00100000", "sell", "2.00", closed))
            session.add(_fill("qqq-buy", "QQQ261030P00400000", "buy", "1.20", opened))
            session.add(_fill("qqq-sell", "QQQ261030P00400000", "sell", "0.40", closed + timedelta(minutes=1)))
            session.add(_fill("hood-buy", "HOOD261030C00080000", "buy", "0.50", opened))
            session.add(_fill("hood-sell", "HOOD261030C00080000", "sell", "0.80", closed + timedelta(minutes=2)))
            for index in range(8):
                session.add(
                    ArchiveLocal(
                        source="broker_activities",
                        source_id=f"journal-{index}",
                        payload={},
                        archived_at=datetime.now(timezone.utc),
                    )
                )
            session.commit()
        headers = {"X-Beo-Key": READ}
        equity = client.get("/api/v1/archive/paper_100k/equity", params={"period": "all"}, headers=headers)
        assert equity.status_code == 200, equity.text
        points = equity.json()["points"]
        october_14 = next(row for row in points if row["session_date"] == "2026-10-14")
        assert october_14["t"].startswith("2026-10-15")
        assert october_14["day_pnl"] == "15078.00"
        assert october_14["session_date"] != october_14["t"][:10]

        finance = client.get("/api/v1/finance", params={"trading_period": "paper_100k"}, headers=headers)
        assert finance.status_code == 200, finance.text
        body = finance.json()
        daily = {row["date"]: row["pnl"] for row in body["daily"]}
        assert len(body["daily"]) == 19
        assert daily["2026-10-14"] == "15078.00"
        assert daily["2026-10-23"] == "-8171.00"
        assert body["periods"]["cumulative"] == "10955.20"
        assert body["archive_result"]["final_equity"] == "110955.20"
        assert body["archive_result"]["pnl"] == "10955.20"
        assert body["archive_result"]["return_pct"] == "10.96"
        assert body["archive_result"]["sessions"] == 19
        assert body["archive_result"]["start"] == "2026-10-07"
        assert body["archive_result"]["end"] == "2026-11-02"
        assert body["today_pnl"] == "0.00"
        assert "2026-11-02" in body["day_pnl_note"]
        assert body["account"]["equity"] == "110955.20"
        assert set(body["by_side"]) >= {"CALL", "PUT"}
        assert set(body["by_symbol"]) >= {"AMD", "QQQ", "HOOD"}
        assert body["by_dte"]["2DTE+"]["enough"] is True
        assert body["best_worst"]["best_symbol"]
        assert body["best_worst"]["worst_symbol"]
        assert any(row["month"] == "2026-10" and row["pnl"] not in {None, "", "0.00"} for row in body["monthly"])
        assert body["trades"]["enough"] is True
        assert body["trades"]["count"] == 3

        report = client.get("/api/v1/report/trades", params={"period": "paper_100k"}, headers=headers)
        assert report.status_code == 200, report.text
        reported = report.json()
        assert reported["source"] == "archive"
        assert reported["read_only"] is True
        assert len(reported["closed"]) == 3
        assert all(row.get("pnl") not in {None, ""} and row.get("entry_price") and row.get("exit_price") for row in reported["closed"])
        assert reported["archive_result"]["pnl"] == "10955.20"
        assert reported["archive_result"]["final_equity"] == "110955.20"
        assert any(row["session_date"] == "2026-10-14" and row["day_pnl"] == "15078.00" for row in reported["equity_curve"])

        trades = client.get("/api/v1/archive/paper_100k/trades", headers=headers)
        assert trades.status_code == 200, trades.text
        page = trades.json()
        assert page["total"] == 3
        assert all(item.get("symbol") and item.get("pnl") not in {None, ""} for item in page["items"])
        assert all("source" not in item for item in page["items"])
        journal = client.get("/api/v1/archive/paper_100k/journal", headers=headers)
        assert journal.status_code == 200
        assert journal.json()["total"] == 8
        assert any(item.get("symbol") in {None, ""} for item in journal.json()["items"])

        risk = client.get("/api/v1/risk", params={"trading_period": "paper_100k"}, headers=headers)
        assert risk.status_code == 200, risk.text
        assert risk.json()["display_pnl"] == "0.00"
        assert "2026-11-02" in risk.json()["day_pnl_note"]


def test_archive_trades_page_closed_round_trips_not_the_journal(tmp_path):
    with TestClient(create_app(_settings(tmp_path, "pages.db"))) as client:
        base = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
        with session_scope() as session:
            fills = []
            for index in range(205):
                symbol = f"AMD261030C{100000 + index:08d}"
                opened = base + timedelta(minutes=index)
                closed = opened + timedelta(seconds=40)
                fills.append(_fill(f"buy-{index}", symbol, "buy", "2.00", opened))
                fills.append(_fill(f"sell-{index}", symbol, "sell", "2.10", closed))
            session.add_all(fills)
            for index in range(12):
                session.add(
                    ArchiveLocal(
                        source="broker_activities",
                        source_id=f"empty-{index}",
                        payload={"activity_type": "FILL"},
                        archived_at=datetime.now(timezone.utc),
                    )
                )
            session.commit()
        headers = {"X-Beo-Key": READ}
        first = client.get("/api/v1/archive/paper_100k/trades", params={"limit": 50, "offset": 0}, headers=headers)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["total"] == 205
        assert body["limit"] == 50
        assert len(body["items"]) == 50
        assert body["items"][0]["closed_at"] >= body["items"][-1]["closed_at"]
        assert all(item["symbol"] and item["entry_price"] and item["exit_price"] and item["pnl"] not in {None, ""} for item in body["items"])
        assert all(Decimal(item["pnl"]) == Decimal("10.00") for item in body["items"])
        last = client.get("/api/v1/archive/paper_100k/trades", params={"limit": 50, "offset": 200}, headers=headers)
        assert last.status_code == 200
        assert len(last.json()["items"]) == 5
        assert last.json()["total"] == 205
        journal = client.get("/api/v1/archive/paper_100k/journal", params={"limit": 50}, headers=headers)
        assert journal.json()["total"] == 12
        assert all(item.get("pnl") in {None, ""} for item in journal.json()["items"])
