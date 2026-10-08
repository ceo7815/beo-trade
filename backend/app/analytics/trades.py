from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.broker.normalize import parse_option_symbol

CENT = Decimal("0.01")
ZERO = Decimal("0")


def _decimal(value) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _moment(value) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT))


def _percent(gain: Decimal, base: Decimal) -> str | None:
    if base <= 0:
        return None
    return str((gain / base * Decimal("100")).quantize(CENT))


def _minutes(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(int((end - start).total_seconds() // 60), 0)


@dataclass
class _Episode:
    symbol: str
    opened_at: datetime | None
    held: Decimal = ZERO
    bought: Decimal = ZERO
    cost: Decimal = ZERO
    sold: Decimal = ZERO
    proceeds: Decimal = ZERO
    closed_at: datetime | None = None
    buys: int = 0
    sells: int = 0
    orders: list[str] = field(default_factory=list)


def _describe(episode: _Episode, multiplier: int) -> dict:
    contract = parse_option_symbol(episode.symbol) or {}
    entry = episode.cost / episode.bought / multiplier if episode.bought > 0 else ZERO
    expiration = contract.get("expiration")
    dte = None
    if expiration and episode.opened_at is not None:
        try:
            dte = (datetime.fromisoformat(expiration).date() - episode.opened_at.date()).days
        except ValueError:
            dte = None
    return {
        "symbol": episode.symbol,
        "underlying": contract.get("underlying") or episode.symbol,
        "right": contract.get("right"),
        "strike": contract.get("strike"),
        "expiration": expiration,
        "dte_at_entry": dte,
        "qty": str(episode.bought.normalize()),
        "entry_price": str(entry.quantize(Decimal("0.0001")).normalize()),
        "opened_at": None if episode.opened_at is None else episode.opened_at.isoformat(),
        "entry_fills": episode.buys,
        "exit_fills": episode.sells,
        "order_ids": list(dict.fromkeys(episode.orders)),
    }


def round_trips(fills: list[dict], positions: list[dict] | None = None, now: datetime | None = None, multiplier: int = 100) -> dict:
    """One trade is a contract held from flat back to flat. Fill prices are the only source of P&L."""
    moment = now or datetime.now(timezone.utc)
    ordered = sorted(
        (row for row in fills if _decimal(row.get("qty")) and _decimal(row.get("price")) is not None),
        key=lambda row: (_moment(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc), str(row.get("execution_id") or "")),
    )
    unit = Decimal(multiplier)
    live: dict[str, _Episode] = {}
    closed: list[dict] = []
    orphan_sells = 0
    for row in ordered:
        symbol = str(row.get("symbol") or "").replace(" ", "").upper()
        side = str(row.get("side") or "").lower()
        qty = abs(_decimal(row.get("qty")))
        price = _decimal(row.get("price"))
        stamp = _moment(row.get("timestamp"))
        order_id = str(row.get("broker_order_id") or "")
        if side == "buy":
            episode = live.get(symbol)
            if episode is None:
                episode = live[symbol] = _Episode(symbol, stamp)
            episode.held += qty
            episode.bought += qty
            episode.cost += qty * price * unit
            episode.buys += 1
            if order_id:
                episode.orders.append(order_id)
            continue
        if side != "sell":
            continue
        episode = live.get(symbol)
        if episode is None or episode.held <= 0:
            orphan_sells += 1
            continue
        taken = min(qty, episode.held)
        episode.held -= taken
        episode.sold += taken
        episode.proceeds += taken * price * unit
        episode.sells += 1
        episode.closed_at = stamp
        if order_id:
            episode.orders.append(order_id)
        if episode.held == 0:
            pnl = episode.proceeds - episode.cost
            exit_price = episode.proceeds / episode.sold / unit
            closed.append(
                {
                    **_describe(episode, multiplier),
                    "exit_price": str(exit_price.quantize(Decimal("0.0001")).normalize()),
                    "invested": _money(episode.cost),
                    "proceeds": _money(episode.proceeds),
                    "pnl": _money(pnl),
                    "return_pct": _percent(pnl, episode.cost),
                    "outcome": "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "FLAT",
                    "closed_at": None if episode.closed_at is None else episode.closed_at.isoformat(),
                    "held_minutes": _minutes(episode.opened_at, episode.closed_at),
                }
            )
            del live[symbol]

    marks = {str(row.get("symbol") or "").replace(" ", "").upper(): row for row in positions or []}
    still_open = []
    for symbol, episode in live.items():
        average = episode.cost / episode.bought if episode.bought > 0 else ZERO
        remaining_cost = average * episode.held
        realized = episode.proceeds - average * episode.sold
        mark = marks.get(symbol, {})
        current = _decimal(mark.get("current_price"))
        value = None if current is None else current * episode.held * unit
        unrealized = None if value is None else value - remaining_cost
        still_open.append(
            {
                **_describe(episode, multiplier),
                "qty_open": str(episode.held.normalize()),
                "invested": _money(remaining_cost),
                "current_price": None if current is None else str(current),
                "market_value": None if value is None else _money(value),
                "unrealized_pnl": None if unrealized is None else _money(unrealized),
                "realized_partial": _money(realized),
                "return_pct": None if unrealized is None else _percent(unrealized, remaining_cost),
                "held_minutes": _minutes(episode.opened_at, moment),
                "at_broker": symbol in marks,
            }
        )
    closed.sort(key=lambda row: row.get("closed_at") or "", reverse=True)
    still_open.sort(key=lambda row: row.get("opened_at") or "", reverse=True)
    return {"closed": closed, "open": still_open, "orphan_sells": orphan_sells}


PERIODS = ("all", "today", "yesterday", "month", "range")


def _previous_session(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except (AttributeError, ValueError):
        return None


def window(period: str, start: str = "", end: str = "", now: datetime | None = None, zone: str = "America/New_York") -> dict:
    """Trading dates are New York dates. Yesterday is the previous weekday session."""
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone)).date()
    kind = period if period in PERIODS else "all"
    first: date | None = None
    last: date | None = None
    if kind == "today":
        first = last = today
    elif kind == "yesterday":
        first = last = _previous_session(today)
    elif kind == "month":
        first, last = today.replace(day=1), today
    elif kind == "range":
        first, last = _day(start), _day(end)
        if first and last and first > last:
            first, last = last, first
    return {
        "period": kind,
        "start": None if first is None else first.isoformat(),
        "end": None if last is None else last.isoformat(),
        "includes_today": last is None or last >= today,
        "zone": zone,
    }


def within(moment: datetime | str | None, frame: dict) -> bool:
    if isinstance(moment, str):
        moment = _moment(moment)
    if moment is None:
        return frame["start"] is None and frame["end"] is None
    day = moment.astimezone(ZoneInfo(frame["zone"])).date().isoformat()
    if frame["start"] and day < frame["start"]:
        return False
    if frame["end"] and day > frame["end"]:
        return False
    return True


def summarize(closed: list[dict], still_open: list[dict], ai_cost: Decimal | None = None) -> dict:
    pnls = [Decimal(row["pnl"]) for row in closed]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    invested = sum((Decimal(row["invested"]) for row in closed), ZERO)
    realized = sum(pnls, ZERO)
    open_invested = sum((Decimal(row["invested"]) for row in still_open), ZERO)
    marked = [Decimal(row["unrealized_pnl"]) for row in still_open if row.get("unrealized_pnl") is not None]
    unrealized = sum(marked, ZERO)
    partial = sum((Decimal(row["realized_partial"]) for row in still_open), ZERO)
    realized += partial
    total = realized + unrealized
    holds = [row["held_minutes"] for row in closed if row.get("held_minutes") is not None]
    best = max(closed, key=lambda row: Decimal(row["pnl"]), default=None)
    worst = min(closed, key=lambda row: Decimal(row["pnl"]), default=None)
    gross_loss = abs(sum(losses, ZERO))
    return {
        "closed_count": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": None if not closed else _percent(Decimal(len(wins)), Decimal(len(closed))),
        "invested_closed": _money(invested),
        "realized_pnl": _money(realized),
        "realized_return_pct": _percent(realized, invested),
        "average_win": None if not wins else _money(sum(wins, ZERO) / len(wins)),
        "average_loss": None if not losses else _money(sum(losses, ZERO) / len(losses)),
        "profit_factor": None if gross_loss == 0 else str((sum(wins, ZERO) / gross_loss).quantize(CENT)),
        "best": None if best is None else {"symbol": best["symbol"], "pnl": best["pnl"], "return_pct": best["return_pct"]},
        "worst": None if worst is None else {"symbol": worst["symbol"], "pnl": worst["pnl"], "return_pct": worst["return_pct"]},
        "average_hold_minutes": None if not holds else int(sum(holds) / len(holds)),
        "open_count": len(still_open),
        "open_invested": _money(open_invested),
        "unrealized_pnl": _money(unrealized),
        "unrealized_complete": len(marked) == len(still_open),
        "total_pnl": _money(total),
        "total_return_pct": _percent(total, invested + open_invested),
        "ai_cost": None if ai_cost is None else _money(ai_cost),
        "net_after_ai": None if ai_cost is None else _money(total - ai_cost),
    }
