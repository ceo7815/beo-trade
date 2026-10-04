from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from statistics import median
from zoneinfo import ZoneInfo

FUNDING_ACTIVITY_TYPES = frozenset({"CSD", "CSW", "JNLC", "JNLS", "TRANS", "ACATC", "ACATS"})
_NEW_YORK = ZoneInfo("America/New_York")


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def _ratio(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.0001")))


def today_pnl(equity: object, last_equity: object) -> str | None:
    if equity in {None, ""} or last_equity in {None, ""}:
        return None
    return _money(Decimal(str(equity)) - Decimal(str(last_equity)))


def _activity_day(stamp: object) -> date | None:
    if isinstance(stamp, datetime):
        moment = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)
        return moment.astimezone(_NEW_YORK).date()
    if isinstance(stamp, date):
        return stamp
    text = str(stamp or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            if len(text) == 10:
                return date.fromisoformat(text)
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment.astimezone(_NEW_YORK).date()
        except ValueError:
            return None
    return None


def trading_day_pnl(equity: object, last_equity: object, activities: list[dict] | None, session_date: date) -> Decimal | None:
    """Trading P&L since the prior equity mark, after same-day deposits, withdrawals, and transfers.

    A missing activity amount on a funding row makes the baseline unusable.
    """
    if equity in {None, ""} or last_equity in {None, ""} or activities is None:
        return None
    movement = Decimal(str(equity)) - Decimal(str(last_equity))
    funding = Decimal("0")
    for row in activities:
        kind = str(row.get("activity_type") or "").upper()
        if kind not in FUNDING_ACTIVITY_TYPES:
            continue
        day = _activity_day(row.get("date") or row.get("transaction_time"))
        if day is None:
            return None
        if day != session_date:
            continue
        net = row.get("net_amount")
        if net in {None, ""}:
            return None
        funding += Decimal(str(net))
    return movement - funding


def drawdown_from_equity(points: list[dict]) -> dict:
    values = [Decimal(str(point["equity"])) for point in points if point.get("equity") not in {None, ""}]
    if len(values) < 2:
        return {"enough": False, "note": "אין עדיין היסטוריה מספקת"}
    peak = values[0]
    worst = Decimal("0")
    current = Decimal("0")
    for value in values:
        peak = max(peak, value)
        drop = peak - value
        worst = max(worst, drop)
        current = drop
    worst_pct = None if peak == 0 else worst / peak
    current_pct = None if peak == 0 else current / peak
    return {
        "enough": True,
        "current": _money(current),
        "maximum": _money(worst),
        "current_percent": _ratio(current_pct),
        "maximum_percent": _ratio(worst_pct),
        "note": None,
    }


def summarize_trades(trades: list[dict]) -> dict:
    if not trades:
        return {"enough": False, "note": "אין מספיק מידע לניתוח", "count": 0}
    pnls = [Decimal(str(row.get("gross_pnl") or row["pnl"])) for row in trades if row.get("gross_pnl") not in {None, ""} or row.get("pnl") not in {None, ""}]
    if not pnls:
        return {"enough": False, "note": "אין מספיק מידע לניתוח", "count": 0}
    fees_missing = any("net_pnl" in row and row.get("net_pnl") in {None, ""} for row in trades)
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_win = sum(wins, Decimal("0"))
    gross_loss = sum(losses, Decimal("0"))
    factor = None
    if gross_loss < 0:
        factor = gross_win / abs(gross_loss)
    returns = [Decimal(str(row["return_pct"])) for row in trades if row.get("return_pct") not in {None, ""}]
    return {
        "enough": True,
        "note": None,
        "count": len(pnls),
        "gross": _money(sum(pnls, Decimal("0"))),
        "net": None if fees_missing else _money(sum((Decimal(str(row["net_pnl"])) if row.get("net_pnl") not in {None, ""} else Decimal(str(row["pnl"])) for row in trades if row.get("net_pnl") not in {None, ""} or row.get("pnl") not in {None, ""}), Decimal("0"))),
        "fees_note": "עמלות טרם זמינות" if fees_missing else None,
        "gross_win": _money(gross_win),
        "gross_loss": _money(gross_loss),
        "average_win": _money(gross_win / len(wins)) if wins else None,
        "average_loss": _money(gross_loss / len(losses)) if losses else None,
        "max_win": _money(max(pnls)),
        "max_loss": _money(min(pnls)),
        "win_rate": _ratio(Decimal(len(wins)) / Decimal(len(pnls))),
        "loss_rate": _ratio(Decimal(len(losses)) / Decimal(len(pnls))),
        "profit_factor": _ratio(factor),
        "average_return": _ratio(sum(returns, Decimal("0")) / Decimal(len(returns))) if returns else None,
        "median_return": _ratio(Decimal(str(median(returns)))) if returns else None,
    }


def split_by(trades: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for row in trades:
        label = str(row.get(key) or "")
        if not label:
            continue
        groups.setdefault(label, []).append(row)
    return {label: summarize_trades(rows) for label, rows in groups.items()}


def dte_bucket(dte: int | None) -> str | None:
    if dte is None:
        return None
    if dte <= 0:
        return "0DTE"
    if dte == 1:
        return "1DTE"
    return "2DTE+"


def _dated(points: list[dict], field: str) -> list[tuple[int, Decimal]]:
    dated = []
    for point in points:
        stamp = point.get("timestamp")
        value = point.get(field)
        if stamp in {None, ""} or value in {None, ""}:
            continue
        dated.append((int(stamp), Decimal(str(value))))
    return dated


def _movement(points: list[dict]) -> list[tuple[int, Decimal]]:
    profit = _dated(points, "profit_loss")
    if len(profit) >= 2:
        return profit
    equity = _dated(points, "equity")
    if len(equity) >= 2 and all(value > 0 for _, value in equity):
        return equity
    return []


def window_change(points: list[dict], seconds: int) -> str | None:
    dated = _movement(points)
    if len(dated) < 2:
        return None
    end_time, end_value = dated[-1]
    start_value = None
    for stamp, value in dated:
        if stamp >= end_time - seconds:
            start_value = value
            break
    if start_value is None:
        return None
    return _money(end_value - start_value)


def daily_rows(points: list[dict]) -> list[dict]:
    grouped: dict[str, list[Decimal]] = {}
    for point in points:
        stamp = point.get("timestamp")
        equity = point.get("equity")
        if stamp in {None, ""} or equity in {None, ""}:
            continue
        day = datetime.fromtimestamp(int(stamp), timezone.utc).date().isoformat()
        grouped.setdefault(day, []).append(Decimal(str(equity)))
    rows = []
    for day, values in grouped.items():
        if len(values) < 2:
            continue
        change = values[-1] - values[0]
        rows.append({"date": day, "start": _money(values[0]), "end": _money(values[-1]), "pnl": _money(change)})
    return rows

def period_cards(points: list[dict]) -> dict[str, str | None]:
    dated = _movement(points)
    cards: dict[str, str | None] = {"yesterday": None, "week": None, "month": None, "quarter": None, "year": None, "cumulative": None}
    if len(dated) >= 3:
        cards["yesterday"] = _money(dated[-2][1] - dated[-3][1])
    if len(dated) >= 2:
        cards["cumulative"] = _money(dated[-1][1] - dated[0][1])
    cards["week"] = window_change(points, 7 * 86400)
    cards["month"] = window_change(points, 30 * 86400)
    cards["quarter"] = window_change(points, 90 * 86400)
    cards["year"] = window_change(points, 365 * 86400)
    return cards


def monthly_rows(points: list[dict]) -> list[dict]:
    grouped: dict[str, list[Decimal]] = {}
    for stamp, value in _movement(points):
        month = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m")
        grouped.setdefault(month, []).append(value)
    rows = []
    for month, values in grouped.items():
        if len(values) < 2:
            continue
        rows.append({"month": month, "pnl": _money(values[-1] - values[0])})
    return rows


def filter_trades(
    trades: list[dict],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    side: str = "",
    outcome: str = "",
    symbol: str = "",
    dte: str = "",
    exit_reason: str = "",
    hour: str = "",
) -> list[dict]:
    chosen = []
    for row in trades:
        moment = row.get("closed_at")
        if start is not None or end is not None:
            if not isinstance(moment, datetime):
                continue
            if start is not None and moment < start:
                continue
            if end is not None and moment > end:
                continue
        if side and str(row.get("right") or "").upper() != side.upper():
            continue
        if symbol and symbol.upper() not in str(row.get("symbol") or "").upper() and symbol.upper() not in str(row.get("underlying") or "").upper():
            continue
        pnl = row.get("pnl")
        if outcome == "win" and not (pnl not in {None, ""} and Decimal(str(pnl)) > 0):
            continue
        if outcome == "loss" and not (pnl not in {None, ""} and Decimal(str(pnl)) < 0):
            continue
        bucket = dte_bucket(row.get("dte"))
        if dte and bucket != dte:
            continue
        if exit_reason and not str(row.get("exit_reason") or "").startswith(exit_reason):
            continue
        if hour and str(row.get("hour") or "") != hour:
            continue
        chosen.append(row)
    return chosen
