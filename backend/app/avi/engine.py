"""One cycle of the Avi options engine. Orders are QQQ debit spreads only."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog

from app.avi.calendar import macro_reason
from app.avi.config import SYMBOL, AviConfig, account_matches
from app.avi.store import Store
from app.avi.strategy import (
    WIDTHS,
    Bar,
    chase_price,
    choose_size,
    close_at,
    close_prices,
    entry_order,
    entry_prices,
    evaluate_trend,
    exit_limit,
    exit_order,
    exit_reason,
    mark_value,
    next_monday,
    prior_sessions,
    rth_sessions,
    session_open,
    sigma_at,
    signal_slot,
    spread_pnl,
    vwap_pairs,
    vwap_through,
    week_start,
    weekly_brake,
)
from app.spy.clock import NY, as_ny, entries_allowed

log = structlog.get_logger()
DEAD = {"canceled", "cancelled", "expired", "rejected", "suspended", "replaced", "done_for_day"}


def _dec(value) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _text(value: Decimal | None, places: str = "0.01") -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal(places)))


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def bars_from_rows(rows: list[dict]) -> list[Bar]:
    parsed: list[Bar] = []
    for row in rows:
        start = _parse_time(str(row.get("t") or row.get("start") or ""))
        if start is None:
            continue
        try:
            parsed.append(
                Bar(
                    start=start,
                    open=Decimal(str(row.get("o") or row.get("open"))),
                    high=Decimal(str(row.get("h") or row.get("high"))),
                    low=Decimal(str(row.get("l") or row.get("low"))),
                    close=Decimal(str(row.get("c") or row.get("close"))),
                    volume=Decimal(str(row.get("v") or row.get("volume") or 0)),
                )
            )
        except Exception:
            continue
    parsed.sort(key=lambda bar: bar.start)
    return parsed


def _row(item) -> dict:
    return {key: item[key] for key in item.keys()}


def _detail(row) -> dict:
    raw = row["detail"] if row is not None else None
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


class Engine:
    def __init__(self, cfg: AviConfig, store: Store, broker) -> None:
        self.cfg = cfg
        self.store = store
        self.broker = broker

    def cycle(self, now: datetime, bars: list[Bar], vix: Decimal | None, clock: dict | None, account: dict | None) -> None:
        session = as_ny(now).date()
        session_date = session.isoformat()
        matched = bool(account) and account_matches(self.cfg.account_last4, str(account.get("account_number") or ""))
        if account is not None:
            self.store.set_meta("account_mismatch", "0" if matched else "1")
        hard_block = self.store.meta("account_mismatch") == "1"
        equity = _dec(account.get("equity")) if matched else None
        saved = _dec(self.store.meta(f"equity_open:{session_date}"))
        bankroll = self._bankroll(session_date, equity) if matched else (self.cfg.bankroll or saved)
        try:
            if self._kill_requested():
                self.store.set_meta("halt_reason", "KILL")
                if not hard_block:
                    self._flatten(now, "KILL")
            elif not hard_block:
                self._refresh("entry", now)
                self._refresh("exit", now)
                self._latch_week(session, bankroll)
                if self.store.open_trade() is not None:
                    self._manage_exit(now, bars, session)
                elif self.store.working("entry") is not None:
                    self._chase_entry(now, session_date)
                else:
                    self._resume_entry(now, session_date)
                    if matched and self.store.working("entry") is None and self.store.open_trade() is None:
                        self._maybe_enter(now, bars, vix, clock, session, bankroll)
        except Exception as exc:
            log.warning("avi.cycle_failed", error=type(exc).__name__)
        self._write_status(now, session, bankroll, equity, matched, bars, vix)

    def _kill_requested(self) -> bool:
        if os.environ.get("AVI_KILL_SWITCH", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        return self.store.path.with_name("avi_kill.flag").exists()

    def _bankroll(self, session_date: str, equity: Decimal | None) -> Decimal | None:
        if self.cfg.bankroll is not None:
            return self.cfg.bankroll
        key = f"equity_open:{session_date}"
        saved = _dec(self.store.meta(key))
        if saved is not None and saved > 0:
            return saved
        if equity is None or equity <= 0:
            return None
        text = str(equity.quantize(Decimal("0.01")))
        self.store.set_meta(key, text)
        return Decimal(text)

    def _latch_week(self, session, bankroll: Decimal | None) -> None:
        until = self.store.meta("week_halt_until") or ""
        if until and session.isoformat() >= until:
            self.store.set_meta("week_halt_until", "")
            until = ""
        if until or bankroll is None:
            return
        if weekly_brake(self._pnl_since(week_start(session), self._unrealized()), bankroll):
            self.store.set_meta("week_halt_until", next_monday(session).isoformat())
            log.info("avi.weekly_brake", until=next_monday(session).isoformat())

    def _week_halted(self, session) -> bool:
        until = self.store.meta("week_halt_until") or ""
        return bool(until) and session.isoformat() < until

    def _refresh(self, purpose: str, now: datetime) -> None:
        row = self.store.working(purpose)
        if row is None:
            return
        live = self.broker.get_order(str(row["client_order_id"]))
        if not isinstance(live, dict):
            return
        status = str(live.get("status") or row["status"])
        filled = str(live.get("filled_avg_price") or "") or None
        self.store.save_order({**_row(row), "status": status, "filled_avg_price": filled, "broker_order_id": live.get("id") or row["broker_order_id"]})
        if status == "filled":
            if purpose == "entry":
                self._open_from_fill(row, live, now)
            else:
                self._close_from_fill(row, live, now)

    def _open_from_fill(self, row, live: dict, now: datetime) -> None:
        if self.store.open_trade() is not None:
            return
        detail = _detail(row)
        fallback = _dec(row["limit_price"]) or Decimal(0)
        debit = abs(_dec(live.get("filled_avg_price")) or fallback)
        self.store.insert_trade(
            {
                "session_date": row["session_date"],
                "side": row["side"],
                "qty": int(row["qty"]),
                "long_symbol": row["long_symbol"],
                "short_symbol": row["short_symbol"],
                "long_strike": detail.get("long_strike"),
                "short_strike": detail.get("short_strike"),
                "expiry": detail.get("expiry"),
                "width": detail.get("width"),
                "debit": str(debit),
                "entry_at": now.astimezone(timezone.utc).isoformat(),
            }
        )
        self.store.set_meta("replace_entry", "")

    def _close_from_fill(self, row, live: dict, now: datetime) -> None:
        trade = self.store.open_trade()
        if trade is None:
            return
        debit = _dec(trade["debit"]) or Decimal(0)
        credit = abs(_dec(live.get("filled_avg_price")) or _dec(row["limit_price"]) or Decimal(0))
        pnl = spread_pnl(debit, credit, int(trade["qty"]))
        reason = _detail(row).get("exit_reason") or "EXIT"
        self.store.close_trade(int(trade["id"]), now.astimezone(timezone.utc).isoformat(), str(credit), str(reason), _text(pnl) or "0")

    def _chase_entry(self, now: datetime, session_date: str) -> None:
        row = self.store.working("entry")
        if row is None:
            return
        detail = _detail(row)
        started = _parse_time(str(detail.get("started_at") or row["submitted_at"])) or now
        elapsed = (now - started).total_seconds()
        mid = _dec(detail.get("mid")) or _dec(row["limit_price"]) or Decimal(0)
        natural = _dec(row["natural_price"]) or mid
        desired = chase_price(mid, natural, elapsed)
        if desired is None:
            self._cancel(row)
            self.store.set_meta("replace_entry", "")
            log.info("avi.entry_canceled", reason="unfilled")
            return
        current = _dec(row["limit_price"]) or mid
        if desired <= current:
            return
        if detail.get("canceling"):
            return
        detail["canceling"] = True
        detail["replace_limit"] = str(desired)
        self.store.save_order({**_row(row), "detail": json.dumps(detail)})
        self.store.set_meta("replace_entry", json.dumps({**detail, "session_date": session_date, "side": row["side"], "qty": int(row["qty"]), "long_symbol": row["long_symbol"], "short_symbol": row["short_symbol"], "natural": str(natural), "started_at": started.isoformat()}))
        self._cancel(row)

    def _resume_entry(self, now: datetime, session_date: str) -> None:
        raw = self.store.meta("replace_entry") or ""
        if not raw or self.store.working("entry") is not None or self.store.open_trade() is not None:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.store.set_meta("replace_entry", "")
            return
        started = _parse_time(str(payload.get("started_at") or "")) or now
        if (now - started).total_seconds() >= 300:
            self.store.set_meta("replace_entry", "")
            return
        limit = _dec(payload.get("replace_limit"))
        natural = _dec(payload.get("natural"))
        if limit is None or natural is None:
            self.store.set_meta("replace_entry", "")
            return
        attempt = int(payload.get("attempt") or 1) + 1
        self._submit_entry(session_date, payload, limit, natural, started, attempt)
        self.store.set_meta("replace_entry", "")

    def _manage_exit(self, now: datetime, bars: list[Bar], session) -> None:
        trade = self.store.open_trade()
        if trade is None:
            return
        if self._resume_exit(now, trade):
            return
        working = self.store.working("exit")
        if working is not None:
            self._chase_exit(now, working)
            return
        if self._kill_requested():
            self._submit_exit(now, trade, "KILL", force_natural=True)
            return
        minute = as_ny(now).hour * 60 + as_ny(now).minute
        today = rth_sessions(bars).get(session, [])
        value = self._mark(trade)
        if value is not None:
            self.store.update_mark(int(trade["id"]), _text(value, "0.0001") or "0")
        reason = exit_reason(
            side=str(trade["side"]),
            debit=_dec(trade["debit"]) or Decimal(0),
            value=value,
            closes=vwap_pairs(today, minute),
            minute=minute,
        )
        if reason:
            self._submit_exit(now, trade, reason, force_natural=reason in {"TIME", "KILL"})

    def _chase_exit(self, now: datetime, row) -> None:
        detail = _detail(row)
        started = _parse_time(str(detail.get("started_at") or row["submitted_at"])) or now
        elapsed = (now - started).total_seconds()
        mid = _dec(detail.get("mid")) or _dec(row["limit_price"]) or Decimal("0.01")
        natural = _dec(row["natural_price"]) or Decimal("0.01")
        desired = exit_limit(mid, natural, elapsed)
        current = _dec(row["limit_price"]) or mid
        if desired >= current:
            return
        if detail.get("canceling"):
            return
        detail["canceling"] = True
        detail["replace_limit"] = str(desired)
        self.store.save_order({**_row(row), "detail": json.dumps(detail)})
        self.store.set_meta("replace_exit", json.dumps({**detail, "side": row["side"], "qty": int(row["qty"]), "long_symbol": row["long_symbol"], "short_symbol": row["short_symbol"], "natural": str(natural), "started_at": started.isoformat(), "session_date": row["session_date"], "exit_reason": detail.get("exit_reason") or "EXIT"}))
        self._cancel(row)

    def _flatten(self, now: datetime, reason: str) -> None:
        if self.store.working("entry") is not None:
            self._cancel(self.store.working("entry"))
        trade = self.store.open_trade()
        if trade is not None and self.store.working("exit") is None:
            self._submit_exit(now, trade, reason, force_natural=True)

    def _maybe_enter(self, now: datetime, bars: list[Bar], vix: Decimal | None, clock: dict | None, session, bankroll: Decimal | None) -> None:
        session_date = session.isoformat()
        recorded = self.store.signal_keys(session_date)
        slot = signal_slot(now, recorded)
        if self._kill_requested():
            if slot is not None:
                self._record(session_date, slot, "skipped", "kill", vix=vix)
            return
        if self.store.meta("account_mismatch") == "1":
            return
        if not entries_allowed(clock):
            return
        if self.store.trade_count(session_date) >= 1:
            return
        if slot is None:
            return
        if self._week_halted(session):
            self._record(session_date, slot, "skipped", "weekly", vix=vix)
            return
        if macro_reason(session):
            self._record(session_date, slot, "skipped", "macro", vix=vix)
            return
        minute = slot.hour * 60 + slot.minute
        sessions = rth_sessions(bars)
        today = sessions.get(session, [])
        prior = prior_sessions(sessions, session)
        opened = session_open(today)
        price = close_at(today, minute)
        sigma = sigma_at(prior, minute)
        vwap = vwap_through(today, minute) if today else None
        state, reason, upper, lower = evaluate_trend(price=price, open_px=opened, sigma=sigma, vwap=vwap, vix=vix)
        if reason in {"data", "vix_missing"}:
            return
        base = {
            "price": _text(price, "0.0001"),
            "open_px": _text(opened, "0.0001"),
            "sigma": _text(sigma, "0.00000001"),
            "upper_px": _text(upper, "0.0001"),
            "lower_px": _text(lower, "0.0001"),
            "vwap": _text(vwap, "0.0001"),
            "vix": _text(vix, "0.01"),
        }
        if state not in {"up", "down"}:
            self._record(session_date, slot, state, reason, **base)
            return
        if bankroll is None:
            return
        try:
            priced = self._price_widths(session, state, price or Decimal(0))
        except Exception as exc:
            log.warning("avi.chain_failed", error=type(exc).__name__)
            return
        if priced is None:
            self._record(session_date, slot, "skipped", "no_expiry", detail=state, **base)
            return
        debits, pairs = priced
        decision = choose_size(debits, bankroll, self.cfg.risk_pct, self.cfg.max_contracts, self.cfg.max_debit_pct)
        if decision.contracts < 1 or decision.width is None or decision.width not in pairs:
            log.info("avi.skip", reason="SKIP_DEBIT_CAP", side=state)
            self._record(session_date, slot, "skipped", "SKIP_DEBIT_CAP", detail=state, **base)
            return
        self._record(session_date, slot, state, reason, detail=decision.reason, **base)
        pair = pairs[decision.width]
        limit = Decimal(pair["mid"])
        natural = Decimal(pair["natural"])
        payload = {**pair, "side": state, "qty": decision.contracts, "attempt": 1, "mid": str(limit)}
        self._submit_entry(session_date, payload, limit, natural, now, 1)

    def _price_widths(self, session, side: str, price: Decimal):
        right = "call" if side == "up" else "put"
        rows = self.broker.contracts(right, (session + timedelta(days=1)).isoformat())
        book_by_exp: dict = {}
        for row in rows:
            try:
                expiry = datetime.fromisoformat(str(row.get("expiration_date"))[:10]).date()
                strike = Decimal(str(row.get("strike_price")))
                symbol = str(row.get("symbol") or "")
            except Exception:
                continue
            if not symbol:
                continue
            book_by_exp.setdefault(expiry, {})[strike] = symbol
        from app.avi.strategy import choose_expiry

        expiry = choose_expiry(session, list(book_by_exp))
        if expiry is None:
            return None
        book = book_by_exp[expiry]
        strikes = list(book)
        debits: dict[Decimal, Decimal | None] = {}
        pairs: dict[Decimal, dict] = {}
        from app.avi.strategy import wing_strikes

        for width in WIDTHS:
            chosen = wing_strikes(price, strikes, side, width)
            if chosen is None:
                debits[width] = None
                continue
            long_strike, short_strike = chosen
            long_symbol = book[long_strike]
            short_symbol = book[short_strike]
            quotes = self.broker.quotes([long_symbol, short_symbol])
            prices = entry_prices(quotes.get(long_symbol), quotes.get(short_symbol))
            if prices is None:
                debits[width] = None
                continue
            debits[width] = prices[0]
            pairs[width] = {
                "long_symbol": long_symbol,
                "short_symbol": short_symbol,
                "long_strike": str(long_strike),
                "short_strike": str(short_strike),
                "expiry": expiry.isoformat(),
                "mid": str(prices[0]),
                "natural": str(prices[1]),
                "width": str(width),
            }
        return debits, pairs

    def _submit_entry(self, session_date: str, payload: dict, limit: Decimal, natural: Decimal, started: datetime, attempt: int) -> None:
        slot = started.astimezone(NY)
        client_order_id = f"avi-{session_date.replace('-', '')}-{slot.strftime('%H%M')}-e{attempt}"[:48]
        body = entry_order(str(payload["long_symbol"]), str(payload["short_symbol"]), int(payload["qty"]), limit, client_order_id)
        detail = {
            "started_at": started.astimezone(timezone.utc).isoformat(),
            "mid": str(payload.get("mid") or limit),
            "width": payload.get("width"),
            "expiry": payload.get("expiry"),
            "long_strike": payload.get("long_strike"),
            "short_strike": payload.get("short_strike"),
            "attempt": attempt,
        }
        try:
            live = self.broker.submit(body)
        except Exception as exc:
            log.warning("avi.entry_rejected", error=type(exc).__name__)
            self.store.save_order(
                {
                    "client_order_id": client_order_id,
                    "purpose": "entry",
                    "side": payload["side"],
                    "qty": int(payload["qty"]),
                    "limit_price": str(limit),
                    "natural_price": str(natural),
                    "status": "rejected",
                    "long_symbol": payload["long_symbol"],
                    "short_symbol": payload["short_symbol"],
                    "session_date": session_date,
                    "submitted_at": started.astimezone(timezone.utc).isoformat(),
                    "detail": json.dumps(detail),
                }
            )
            return
        self.store.save_order(
            {
                "client_order_id": client_order_id,
                "purpose": "entry",
                "side": payload["side"],
                "qty": int(payload["qty"]),
                "limit_price": str(limit),
                "natural_price": str(natural),
                "status": str(live.get("status") or "new"),
                "broker_order_id": live.get("id"),
                "long_symbol": payload["long_symbol"],
                "short_symbol": payload["short_symbol"],
                "session_date": session_date,
                "submitted_at": started.astimezone(timezone.utc).isoformat(),
                "detail": json.dumps(detail),
            }
        )

    def _submit_exit(self, now: datetime, trade, reason: str, force_natural: bool) -> None:
        quotes = {}
        try:
            quotes = self.broker.quotes([str(trade["long_symbol"]), str(trade["short_symbol"])])
        except Exception as exc:
            log.warning("avi.exit_quote_failed", error=type(exc).__name__)
        prices = close_prices(quotes.get(str(trade["long_symbol"])), quotes.get(str(trade["short_symbol"])))
        if prices is None:
            credit = natural = Decimal("0.01")
        else:
            mid, natural = prices
            credit = natural if force_natural else mid
        self._place_exit(now, trade, reason, credit, natural, now)

    def _resume_exit(self, now: datetime, trade) -> bool:
        raw = self.store.meta("replace_exit") or ""
        if not raw or self.store.working("exit") is not None:
            return False
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.store.set_meta("replace_exit", "")
            return False
        limit = _dec(payload.get("replace_limit"))
        natural = _dec(payload.get("natural")) or limit
        if limit is None or natural is None:
            self.store.set_meta("replace_exit", "")
            return False
        started = _parse_time(str(payload.get("started_at") or "")) or now
        self.store.set_meta("replace_exit", "")
        self._place_exit(now, trade, str(payload.get("exit_reason") or "EXIT"), limit, natural, started)
        return True

    def _place_exit(self, now: datetime, trade, reason: str, credit: Decimal, natural: Decimal, started: datetime) -> None:
        del now
        attempt = int(self.store.meta("exit_attempt") or "0") + 1
        self.store.set_meta("exit_attempt", str(attempt))
        client_order_id = f"avi-{str(trade['session_date']).replace('-', '')}-x{attempt}"[:48]
        body = exit_order(str(trade["long_symbol"]), str(trade["short_symbol"]), int(trade["qty"]), credit, client_order_id)
        detail = {"started_at": started.astimezone(timezone.utc).isoformat(), "mid": str(credit), "exit_reason": reason, "attempt": attempt}
        try:
            live = self.broker.submit(body)
        except Exception as exc:
            log.warning("avi.exit_rejected", error=type(exc).__name__)
            return
        self.store.save_order(
            {
                "client_order_id": client_order_id,
                "purpose": "exit",
                "side": trade["side"],
                "qty": int(trade["qty"]),
                "limit_price": str(credit),
                "natural_price": str(natural),
                "status": str(live.get("status") or "new"),
                "broker_order_id": live.get("id"),
                "long_symbol": trade["long_symbol"],
                "short_symbol": trade["short_symbol"],
                "session_date": trade["session_date"],
                "submitted_at": started.astimezone(timezone.utc).isoformat(),
                "detail": json.dumps(detail),
            }
        )

    def _mark(self, trade) -> Decimal | None:
        try:
            quotes = self.broker.quotes([str(trade["long_symbol"]), str(trade["short_symbol"])])
        except Exception:
            return None
        return mark_value(quotes.get(str(trade["long_symbol"])), quotes.get(str(trade["short_symbol"])))

    def _cancel(self, row) -> None:
        if row is None:
            return
        order_id = str(row["broker_order_id"] or "")
        try:
            self.broker.cancel(order_id)
        except Exception as exc:
            log.warning("avi.cancel_failed", error=type(exc).__name__)
        kept = _row(row)
        kept["status"] = "pending_cancel"
        kept["detail"] = None
        self.store.save_order(kept)

    def _record(self, session_date: str, slot: datetime, state: str, reason: str, **fields) -> None:
        detail = fields.pop("detail", None)
        self.store.insert_signal(
            {
                "session_date": session_date,
                "decision_at": slot.isoformat(),
                "state": state,
                "reason": reason,
                "detail": None if detail is None else str(detail),
                **fields,
            }
        )

    def _unrealized(self) -> Decimal:
        trade = self.store.open_trade()
        if trade is None:
            return Decimal(0)
        debit = _dec(trade["debit"])
        mark = _dec(trade["current_value"])
        if debit is None or mark is None:
            return Decimal(0)
        return spread_pnl(debit, mark, int(trade["qty"]))

    def _pnl_since(self, start, unreal: Decimal) -> Decimal:
        total = Decimal(0)
        start_text = start.isoformat()
        for row in self.store.trades(limit=500):
            if str(row["session_date"]) < start_text:
                continue
            if row["status"] == "closed" and row["pnl"] not in {None, ""}:
                total += Decimal(str(row["pnl"]))
        return total + unreal

    def _write_status(self, now: datetime, session, bankroll: Decimal | None, equity: Decimal | None, matched: bool, bars: list[Bar], vix: Decimal | None) -> None:
        session_date = session.isoformat()
        signal = self.store.latest_signal(session_date)
        trade = self.store.open_trade()
        unreal = self._unrealized()
        position: dict = {"open": False}
        if trade is not None:
            debit = _dec(trade["debit"]) or Decimal(0)
            value = _dec(trade["current_value"])
            pnl = None if value is None else spread_pnl(debit, value, int(trade["qty"]))
            position = {
                "open": True,
                "side": trade["side"],
                "qty": int(trade["qty"]),
                "long_symbol": trade["long_symbol"],
                "short_symbol": trade["short_symbol"],
                "long_strike": trade["long_strike"],
                "short_strike": trade["short_strike"],
                "expiry": trade["expiry"],
                "width": trade["width"],
                "debit": _text(debit, "0.01"),
                "value": _text(value, "0.01"),
                "pnl": _text(pnl, "0.01"),
                "legs": [
                    {"symbol": trade["long_symbol"], "side": "long", "strike": trade["long_strike"]},
                    {"symbol": trade["short_symbol"], "side": "short", "strike": trade["short_strike"]},
                ],
            }
        killed = self._kill_requested() or (self.store.meta("halt_reason") or "") == "KILL"
        payload = {
            "ok": True,
            "enabled": True,
            "mode": "running",
            "paper_only": True,
            "symbol": SYMBOL,
            "account_last4": self.cfg.account_last4,
            "account_match": matched and self.store.meta("account_mismatch") != "1",
            "session_date": session_date,
            "signal": {
                "state": "none" if signal is None else signal["state"],
                "reason": "" if signal is None else signal["reason"],
                "at": None if signal is None else signal["decision_at"],
                "price": None if signal is None else signal["price"],
                "vix": None if signal is None else signal["vix"],
                "detail": None if signal is None else signal["detail"],
            },
            "position": position,
            "day_pnl": _text(self._pnl_since(session, unreal)),
            "week_pnl": _text(self._pnl_since(week_start(session), unreal)),
            "bankroll": _text(bankroll),
            "equity": _text(equity),
            "vix": _text(vix, "0.01"),
            "brakes": {
                "kill": killed,
                "weekly": self._week_halted(session),
                "daily": self.store.trade_count(session_date) >= 1,
                "entries_today": self.store.trade_count(session_date),
                "entries_max": 1,
                "account": self.store.meta("account_mismatch") == "1",
                "halt_reason": "KILL" if killed else ("WEEK" if self._week_halted(session) else ""),
            },
        }
        self._publish(payload, now)
        del bars

    def _publish(self, payload: dict, now: datetime) -> None:
        path = self.store.path.with_name("avi_status.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        self.store.path.with_name("avi_heartbeat.txt").write_text(now.astimezone(timezone.utc).isoformat(), encoding="utf-8")


def write_idle_status(cfg: AviConfig, mode: str, now: datetime | None = None) -> None:
    moment = now or datetime.now(timezone.utc)
    killed = os.environ.get("AVI_KILL_SWITCH", "").strip().lower() in {"1", "true", "yes", "on"} or cfg.db.with_name("avi_kill.flag").exists()
    payload = {
        "ok": True,
        "enabled": cfg.enabled,
        "mode": mode,
        "paper_only": True,
        "symbol": SYMBOL,
        "account_last4": cfg.account_last4,
        "account_match": False,
        "signal": {"state": "none" if mode == "disabled" else "skipped", "reason": mode, "at": None, "price": None, "vix": None, "detail": None},
        "position": {"open": False},
        "day_pnl": None,
        "week_pnl": None,
        "bankroll": _text(cfg.bankroll),
        "equity": None,
        "brakes": {
            "kill": killed,
            "weekly": False,
            "daily": False,
            "entries_today": 0,
            "entries_max": 1,
            "account": mode == "not_configured",
            "halt_reason": "LIVE_URL" if mode == "refused" else ("NOT_CONFIGURED" if mode == "not_configured" else ""),
        },
    }
    path = cfg.db.with_name("avi_status.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    cfg.db.with_name("avi_heartbeat.txt").write_text(moment.isoformat(), encoding="utf-8")
