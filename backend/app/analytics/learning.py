"""Per-trade price paths, excursions, and replays of exit rules on recorded quotes.

Every number here comes from a stored quote: the monitor's own checks, the rest of the
session after an exit, or ThetaData minute history. A trade without a path is reported
as uncovered, never filled in.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from itertools import product
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config.settings import Settings, TradingConfig
from app.models.db import database_ready, session_scope
from app.models.tables import PositionState, TradeMark

EXCHANGE = ZoneInfo("America/New_York")
SESSION_CLOSE = time(16, 0)
MARK_SPACING_SECONDS = 15
SHADOW_SPACING_SECONDS = 60

_last_mark: dict[str, datetime] = {}
_last_shadow: datetime | None = None


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parsed(value) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    try:
        return _utc(datetime.fromisoformat(str(value or "").replace("Z", "+00:00")))
    except ValueError:
        return None


def _num(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def session_end(day: date, minutes_before: int = 0) -> datetime:
    close = datetime.combine(day, SESSION_CLOSE, tzinfo=EXCHANGE) - timedelta(minutes=minutes_before)
    return close.astimezone(timezone.utc)


def record_mark(symbol: str, at: datetime, bid, ask, underlying_price, source: str = "monitor") -> bool:
    """At most one mark per contract per MARK_SPACING_SECONDS."""
    if not database_ready():
        return False
    key = symbol.replace(" ", "").upper()
    moment = _utc(at)
    previous = _last_mark.get(key)
    if previous is not None and (moment - previous).total_seconds() < MARK_SPACING_SECONDS:
        return False
    _last_mark[key] = moment
    with session_scope() as session:
        session.add(TradeMark(symbol=key, at=moment, bid=_num(bid), ask=_num(ask), underlying_price=_num(underlying_price), source=source))
        session.commit()
    return True


def shadow_symbols(moment: datetime, held: set[str]) -> list[str]:
    """Contracts exited today and no longer held. Their quotes show what holding would have done."""
    if not database_ready():
        return []
    day = moment.astimezone(EXCHANGE).date()
    start = datetime.combine(day, time(0, 0), tzinfo=EXCHANGE).astimezone(timezone.utc)
    with session_scope() as session:
        rows = session.scalars(select(PositionState).where(PositionState.status == "closed"))
        symbols = []
        for row in rows:
            touched = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=timezone.utc)
            symbol = row.symbol.replace(" ", "").upper()
            if touched >= start and symbol not in held:
                symbols.append(symbol)
    return list(dict.fromkeys(symbols))


def shadow_due(moment: datetime) -> bool:
    global _last_shadow
    if _last_shadow is not None and (moment - _last_shadow).total_seconds() < SHADOW_SPACING_SECONDS:
        return False
    _last_shadow = moment
    return True


def marks_for(session, symbol: str, start: datetime, end: datetime) -> list[TradeMark]:
    rows = session.scalars(
        select(TradeMark)
        .where(TradeMark.symbol == symbol.replace(" ", "").upper(), TradeMark.at >= _utc(start).replace(tzinfo=None), TradeMark.at <= _utc(end).replace(tzinfo=None))
        .order_by(TradeMark.at.asc())
    )
    return list(rows)


@dataclass
class Point:
    at: datetime
    bid: Decimal
    ask: Decimal | None
    underlying: Decimal | None
    source: str


def path_points(marks: list[TradeMark]) -> list[Point]:
    points = []
    for mark in marks:
        bid = _num(mark.bid)
        if bid is None or bid <= 0:
            continue
        points.append(Point(_utc(mark.at), bid, _num(mark.ask), _num(mark.underlying_price), mark.source))
    return points


def trade_window(trade: dict, config: TradingConfig) -> tuple[datetime, datetime, datetime] | None:
    """Entry, exit, and the forced session exit on the entry day."""
    opened = _parsed(trade.get("opened_at"))
    closed = _parsed(trade.get("closed_at"))
    if opened is None or closed is None:
        return None
    forced = session_end(opened.astimezone(EXCHANGE).date(), config.session_exit_minutes)
    return opened, closed, forced


def excursions(trade: dict, points: list[Point], config: TradingConfig) -> dict:
    """Peak, trough, and giveback while held; best and worst bid for the rest of the exit day."""
    entry = _num(trade.get("entry_price"))
    exit_price = _num(trade.get("exit_price"))
    qty = _num(trade.get("qty")) or Decimal("0")
    window = trade_window(trade, config)
    if entry is None or entry <= 0 or window is None:
        return {"covered": False}
    opened, closed, _forced = window
    held = [point for point in points if opened <= point.at <= closed]
    after_end = session_end(closed.astimezone(EXCHANGE).date())
    after = [point for point in points if closed < point.at <= after_end]
    multiplier = Decimal(config.contract_multiplier)

    def pct(price: Decimal) -> str:
        return str(((price / entry) - 1) * 100)

    body: dict = {"covered": bool(held), "marks": len(held), "after_marks": len(after)}
    plan = trade.get("plan") if isinstance(trade.get("plan"), dict) else {}
    stored_peak = _num(plan.get("peak_price"))
    if held:
        peak = max(held, key=lambda point: point.bid)
        trough = min(held, key=lambda point: point.bid)
        body.update(
            {
                "peak_price": str(peak.bid),
                "peak_pct": pct(peak.bid),
                "peak_minutes": round((peak.at - opened).total_seconds() / 60, 1),
                "trough_price": str(trough.bid),
                "trough_pct": pct(trough.bid),
                "trough_minutes": round((trough.at - opened).total_seconds() / 60, 1),
                "peak_source": "marks",
            }
        )
        if exit_price is not None:
            body["giveback"] = str((peak.bid - exit_price) * qty * multiplier)
    elif stored_peak is not None and stored_peak > 0:
        body.update({"peak_price": str(stored_peak), "peak_pct": pct(stored_peak), "peak_source": "position_state"})
        if exit_price is not None:
            body["giveback"] = str((stored_peak - exit_price) * qty * multiplier)
    if after:
        best = max(after, key=lambda point: point.bid)
        worst = min(after, key=lambda point: point.bid)
        body.update({"after_best_pct": pct(best.bid), "after_worst_pct": pct(worst.bid), "after_last_pct": pct(after[-1].bid)})
    return body


@dataclass(frozen=True)
class Rules:
    stop: float
    protect_at: float
    protective: float
    trail_arm: float
    trail: float
    invalidation: float

    def label(self) -> dict:
        return {
            "stop_pct": self.stop,
            "protect_at_pct": self.protect_at,
            "protective_pct": self.protective,
            "trail_arm_pct": self.trail_arm,
            "trail_pct": self.trail,
            "invalidation_pct": self.invalidation,
        }


def current_rules(config: TradingConfig) -> Rules:
    one_r = config.initial_stop_decline_pct
    return Rules(
        stop=one_r,
        protect_at=one_r * config.protect_at_r,
        protective=-one_r * config.protective_stop_r,
        trail_arm=one_r * config.trail_activate_r,
        trail=config.trailing_pct,
        invalidation=config.invalidation_underlying_percent,
    )


def simulate(trade: dict, points: list[Point], rules: Rules, config: TradingConfig) -> dict | None:
    """Replay one trade on its recorded bids. The first mark at or after entry is the start.

    The path ends at the forced session exit of the entry day. If the recorded path stops
    before that and no rule fired, the actual exit stands and the result is partial.
    """
    entry = _num(trade.get("entry_price"))
    qty = _num(trade.get("qty")) or Decimal("0")
    actual_exit = _num(trade.get("exit_price"))
    window = trade_window(trade, config)
    if entry is None or entry <= 0 or window is None:
        return None
    opened, closed, forced = window
    path = [point for point in points if opened <= point.at <= max(session_end(opened.astimezone(EXCHANGE).date()), closed)]
    if not path:
        return None
    multiplier = Decimal(config.contract_multiplier)
    entry_f = float(entry)
    right = str(trade.get("right") or "").upper()
    plan = trade.get("plan") if isinstance(trade.get("plan"), dict) else {}
    base = _num(plan.get("underlying_at_entry")) or next((point.underlying for point in path if point.underlying), None)
    peak = entry_f
    protected = False
    for point in path:
        bid = float(point.bid)
        if point.at > forced:
            return _result(trade, point.bid, "SESSION_CLOSE", point.at, entry, qty, multiplier, complete=True)
        peak = max(peak, bid)
        gain = peak / entry_f - 1
        if rules.protect_at and gain >= rules.protect_at:
            protected = True
        level = entry_f * (1 - rules.stop)
        if protected:
            level = max(level, entry_f * (1 + rules.protective))
        if rules.trail_arm and gain >= rules.trail_arm:
            level = max(level, peak * (1 - rules.trail))
        if bid <= level:
            reason = "TRAILING" if rules.trail_arm and gain >= rules.trail_arm else "PROTECTED_STOP" if protected else "STOP"
            return _result(trade, point.bid, reason, point.at, entry, qty, multiplier, complete=True)
        if rules.invalidation and base and point.underlying:
            move = float(point.underlying / base - 1)
            against = -move if right == "CALL" else move
            if against >= rules.invalidation:
                return _result(trade, point.bid, "INVALIDATION", point.at, entry, qty, multiplier, complete=True)
    last = path[-1]
    if last.at >= forced - timedelta(minutes=2):
        return _result(trade, last.bid, "SESSION_CLOSE", last.at, entry, qty, multiplier, complete=True)
    if actual_exit is None:
        return None
    return _result(trade, actual_exit, "AS_TRADED", closed, entry, qty, multiplier, complete=False)


def _result(trade: dict, price: Decimal, reason: str, at: datetime, entry: Decimal, qty: Decimal, multiplier: Decimal, complete: bool) -> dict:
    return {
        "symbol": trade.get("symbol"),
        "exit_price": str(price),
        "reason": reason,
        "at": at.isoformat(),
        "pnl": (price - entry) * qty * multiplier,
        "complete": complete,
    }


def default_grid(config: TradingConfig) -> list[Rules]:
    grid = []
    for stop, arm, trail, invalidation in product((0.25, 0.30, 0.40, 0.50), (0.30, 0.40, 0.60, 0.80), (0.10, 0.15, 0.20, 0.25), (0.0, 0.01, 0.015)):
        grid.append(Rules(stop, stop * config.protect_at_r, -stop * config.protective_stop_r, arm, trail, invalidation))
    current = current_rules(config)
    if current not in grid:
        grid.append(current)
    return grid


def lab(trades: list[dict], paths: dict[str, list[Point]], config: TradingConfig, grid: list[Rules] | None = None, top: int = 20) -> dict:
    current = current_rules(config)
    rows = []
    covered = [trade for trade in trades if paths.get(_key(trade))]
    actual = sum((_num(trade.get("pnl")) or Decimal("0") for trade in covered), Decimal("0"))
    for rules in grid or default_grid(config):
        total = Decimal("0")
        wins = 0
        complete = 0
        counted = 0
        for trade in covered:
            outcome = simulate(trade, paths[_key(trade)], rules, config)
            if outcome is None:
                continue
            counted += 1
            total += outcome["pnl"]
            wins += 1 if outcome["pnl"] > 0 else 0
            complete += 1 if outcome["complete"] else 0
        rows.append({**rules.label(), "pnl": str(total), "wins": wins, "trades": counted, "complete": complete, "current": rules == current})
    rows.sort(key=lambda row: Decimal(row["pnl"]), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    current_row = next((row for row in rows if row["current"]), None)
    return {
        "trades_total": len(trades),
        "trades_covered": len(covered),
        "actual_pnl": str(actual),
        "current": current_row,
        "best": rows[:top],
        "combinations": len(rows),
    }


def _key(trade: dict) -> str:
    return f"{trade.get('symbol')}|{trade.get('opened_at')}"


def load_paths(trades: list[dict], config: TradingConfig) -> dict[str, list[Point]]:
    if not database_ready():
        return {}
    paths: dict[str, list[Point]] = {}
    with session_scope() as session:
        for trade in trades:
            window = trade_window(trade, config)
            if window is None:
                continue
            opened, closed, _forced = window
            end = session_end(closed.astimezone(EXCHANGE).date())
            paths[_key(trade)] = path_points(marks_for(session, str(trade.get("symbol") or ""), opened - timedelta(minutes=1), end))
    return paths


BACKFILL: dict = {"running": False, "trades": 0, "done": 0, "marks": 0, "skipped": 0, "error": None, "finished_at": None}
_backfill_lock = threading.Lock()


def start_backfill(settings: Settings, trades: list[dict]) -> bool:
    if not _backfill_lock.acquire(blocking=False):
        return False
    BACKFILL.update({"running": True, "trades": len(trades), "done": 0, "marks": 0, "skipped": 0, "error": None, "finished_at": None})

    def run() -> None:
        try:
            backfill(settings, trades)
        except Exception as exc:
            BACKFILL["error"] = type(exc).__name__
        finally:
            BACKFILL["running"] = False
            BACKFILL["finished_at"] = datetime.now(timezone.utc).isoformat()
            _backfill_lock.release()

    threading.Thread(target=run, name="learning-backfill", daemon=True).start()
    return True


def backfill(settings: Settings, trades: list[dict], feed=None) -> None:
    """Minute quotes from ThetaData history for trades the monitor did not record."""
    from app.providers.thetadata import ThetaFeed, _stamp

    config = settings.trading()
    feed = feed or ThetaFeed(settings)
    for trade in trades:
        window = trade_window(trade, config)
        symbol = str(trade.get("symbol") or "").replace(" ", "").upper()
        if window is None or not trade.get("underlying") or not trade.get("expiration") or trade.get("strike") in {None, ""}:
            BACKFILL["skipped"] += 1
            continue
        opened, closed, _forced = window
        first = opened.astimezone(EXCHANGE).date()
        last = closed.astimezone(EXCHANGE).date()
        with session_scope() as session:
            existing = marks_for(session, symbol, opened, closed)
        if len(existing) >= 10:
            BACKFILL["done"] += 1
            continue
        span = {"start_date": first.strftime("%Y%m%d"), "end_date": last.strftime("%Y%m%d"), "interval": "1m", "start_time": "09:30:00", "end_time": "16:00:00"}
        quotes = _rows_of(
            feed,
            "/v3/option/history/quote",
            {
                "symbol": trade["underlying"],
                "expiration": str(trade["expiration"]).replace("-", ""),
                "strike": f"{Decimal(str(trade['strike'])):.3f}",
                "right": "call" if str(trade.get("right") or "").upper() == "CALL" else "put",
                **span,
            },
        )
        stock = _rows_of(feed, "/v3/stock/history/ohlc", {"symbol": trade["underlying"], **span})
        prices: dict[datetime, Decimal] = {}
        for row in stock:
            stamp = _stamp(row.get("timestamp"))
            close = _num(row.get("close"))
            if stamp is not None and close is not None and close > 0:
                prices[_utc(stamp).replace(second=0, microsecond=0)] = close
        added = 0
        with session_scope() as session:
            for row in quotes:
                stamp = _stamp(row.get("timestamp"))
                bid = _num(row.get("bid"))
                if stamp is None or bid is None or bid <= 0:
                    continue
                moment = _utc(stamp)
                session.add(
                    TradeMark(
                        symbol=symbol,
                        at=moment,
                        bid=bid,
                        ask=_num(row.get("ask")),
                        underlying_price=prices.get(moment.replace(second=0, microsecond=0)),
                        source="history",
                    )
                )
                added += 1
            session.commit()
        BACKFILL["marks"] += added
        BACKFILL["done" if added else "skipped"] += 1


def _rows_of(feed, path: str, params: dict) -> list[dict]:
    try:
        return feed._get(path, params, required=False)
    except Exception:
        return []
