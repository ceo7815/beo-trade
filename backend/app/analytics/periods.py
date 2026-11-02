"""Trading periods for analytics separation.

Period 1 keeps every session through 2026-11-02. Period 2 starts 2026-11-03.
No rows are deleted or rewritten — callers filter by New York session date
and, when set, by Alpaca account id/number on the trade or account snapshot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config.settings import CONFIG_DIR

PERIODS_PATH = CONFIG_DIR / "periods.json"


@dataclass(frozen=True)
class TradingPeriod:
    id: str
    label: str
    start: date
    end: date | None
    capital_basis_usd: Decimal | None
    alpaca_account_id: str
    alpaca_account_number: str

    def contains_day(self, day: date) -> bool:
        if day < self.start:
            return False
        if self.end is not None and day > self.end:
            return False
        return True


def load_periods(path: Path | None = None) -> list[TradingPeriod]:
    file_path = path or PERIODS_PATH
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    rows = []
    for item in raw.get("periods") or []:
        end_raw = item.get("end")
        basis = item.get("capital_basis_usd")
        rows.append(
            TradingPeriod(
                id=str(item["id"]),
                label=str(item.get("label") or item["id"]),
                start=date.fromisoformat(str(item["start"])),
                end=None if end_raw in {None, ""} else date.fromisoformat(str(end_raw)),
                capital_basis_usd=None if basis in {None, ""} else Decimal(str(basis)),
                alpaca_account_id=str(item.get("alpaca_account_id") or "").strip(),
                alpaca_account_number=str(item.get("alpaca_account_number") or "").strip(),
            )
        )
    return rows


def current_period_id(path: Path | None = None) -> str:
    file_path = path or PERIODS_PATH
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    return str(raw.get("current_id") or "")


def get_period(period_id: str, path: Path | None = None) -> TradingPeriod | None:
    for row in load_periods(path):
        if row.id == period_id:
            return row
    return None


def resolve_period(period_id: str | None, path: Path | None = None) -> TradingPeriod | None:
    """``all`` / empty → None (no date filter). Missing id falls back to current."""
    key = (period_id or "").strip()
    if key in {"", "all"}:
        return None
    if key == "current":
        key = current_period_id(path)
    found = get_period(key, path)
    if found is not None:
        return found
    return get_period(current_period_id(path), path)


def public_periods(path: Path | None = None) -> dict:
    current = current_period_id(path)
    return {
        "current_id": current,
        "periods": [
            {
                "id": row.id,
                "label": row.label,
                "start": row.start.isoformat(),
                "end": None if row.end is None else row.end.isoformat(),
                "capital_basis_usd": None if row.capital_basis_usd is None else str(row.capital_basis_usd),
                "alpaca_account_id": row.alpaca_account_id or None,
                "alpaca_account_number": row.alpaca_account_number or None,
                "is_current": row.id == current,
            }
            for row in load_periods(path)
        ],
    }


def session_day(moment: datetime | str | None, zone: str = "America/New_York") -> date | None:
    if moment is None or moment == "":
        return None
    if isinstance(moment, str):
        text = moment.replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if moment.tzinfo is None:
        from datetime import timezone

        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(zone)).date()


def trade_matches_period(
    trade: dict,
    period: TradingPeriod | None,
    *,
    zone: str = "America/New_York",
    account_id: str | None = None,
    account_number: str | None = None,
) -> bool:
    """True when the trade belongs in the period. ``period is None`` keeps everything."""
    if period is None:
        return True
    closed = trade.get("closed_at") or trade.get("exit_at") or trade.get("opened_at") or trade.get("entry_at")
    day = session_day(closed, zone)
    if day is None or not period.contains_day(day):
        return False
    return _account_ok(trade, period, account_id=account_id, account_number=account_number)


def _account_ok(
    trade: dict,
    period: TradingPeriod,
    *,
    account_id: str | None,
    account_number: str | None,
) -> bool:
    want_id = period.alpaca_account_id
    want_num = period.alpaca_account_number
    if not want_id and not want_num:
        return True
    have_id = str(trade.get("alpaca_account_id") or trade.get("account_id") or account_id or "").strip()
    have_num = str(trade.get("alpaca_account_number") or trade.get("account_number") or account_number or "").strip()
    if want_id and have_id and want_id == have_id:
        return True
    if want_num and have_num and want_num == have_num:
        return True
    # Period pins an account but the trade has no account tag (legacy paper_100k rows
    # on the same keys, or a DB row written before account tagging). Date filter alone
    # still applies; do not drop history that predates account tags.
    if not have_id and not have_num:
        return True
    return False


def filter_trades_for_period(
    trades: list[dict],
    period_id: str | None,
    *,
    zone: str = "America/New_York",
    account_id: str | None = None,
    account_number: str | None = None,
    path: Path | None = None,
) -> list[dict]:
    period = resolve_period(period_id, path)
    return [
        row
        for row in trades
        if trade_matches_period(row, period, zone=zone, account_id=account_id, account_number=account_number)
    ]
