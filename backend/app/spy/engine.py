"""One cycle of the SPY engine. Real orders are SPY only. Shadow symbols never reach the broker."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.spy.broker import DuplicateOrder, PdtRejected
from app.spy.clock import NY, active_slot, entries_allowed, minute_of, session_close_from_clock, should_flatten
from app.spy.config import SYMBOL, SpyConfig, redact
from app.spy.store import Store
from app.spy.strategy import (
    Bar,
    Decision,
    daily_vol,
    day_trades_left,
    drawdown_hit,
    evaluate_position,
    fit_order_qty,
    is_decision_minute,
    loss_hit,
    pdt_block_reason,
    prior_sessions,
    rth_sessions,
    share_qty,
    slot_snapshot,
)

FILLED = {"filled"}
WORKING = {"new", "accepted", "pending_new", "partially_filled"}


@dataclass
class CycleResult:
    mode: str
    orders: list[dict]
    signals: list[dict]


def _dec(value) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _text(value: Decimal | None, places: str = "0.0001") -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal(places)))


def _pnl(side: str, qty: int, entry: Decimal, exit_px: Decimal) -> Decimal:
    if side == "short":
        return (entry - exit_px) * Decimal(qty)
    return (exit_px - entry) * Decimal(qty)


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
    parsed.sort(key=lambda bar: bar.start)
    return parsed


def account_matches(cfg: SpyConfig, account: dict) -> bool:
    """An empty pin accepts the paper account the keys open. A pin must match exactly."""
    if not cfg.account:
        return True
    return str(account.get("account_number") or "") == cfg.account


def _client_id(session_date: str, slot: str, purpose: str, attempt: int = 1, symbol: str = SYMBOL) -> str:
    stamp = session_date.replace("-", "")
    clock = slot.replace(":", "")
    suffix = "" if attempt <= 1 else f"-r{attempt}"
    return f"spy-{stamp}-{clock}-{symbol.lower()}-{purpose}{suffix}"[:48]


def _order_body(symbol: str, side: str, qty: int, client_order_id: str) -> dict:
    return {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": client_order_id,
    }


def _signal_row(symbol: str, shadow: bool, session_date: str, decision_at: str, decision: Decision) -> dict:
    return {
        "symbol": symbol,
        "shadow": shadow,
        "session_date": session_date,
        "decision_at": decision_at,
        "minute_end": decision.minute_end,
        "price": _text(decision.price),
        "sigma": _text(decision.sigma, "0.00000001"),
        "upper_px": _text(decision.upper),
        "lower_px": _text(decision.lower),
        "vwap": _text(decision.vwap),
        "stop_px": _text(decision.stop),
        "action": decision.action,
        "reason": decision.reason,
        "qty": decision.qty,
    }


def _int_or_none(value) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fresh(bars: list[Bar], now: datetime) -> bool:
    if not bars:
        return False
    last = max(bar.start for bar in bars)
    return now - last <= timedelta(minutes=3)


class Engine:
    def __init__(self, cfg: SpyConfig, store: Store, broker) -> None:
        self.cfg = cfg
        self.store = store
        self.broker = broker

    def cycle(self, now: datetime, bars_by_symbol: dict[str, list[Bar]], clock: dict | None, account: dict | None) -> CycleResult:
        orders: list[dict] = []
        signals: list[dict] = []
        session_date = now.astimezone(NY).date().isoformat()
        self._consume_resume()
        if self._kill_requested():
            self.store.set_meta("halt_reason", "KILL")
        mismatch = bool(account) and not account_matches(self.cfg, account or {})
        if account is not None and mismatch:
            self.store.set_meta("account_mismatch", "1")
        elif account is not None:
            self.store.set_meta("account_mismatch", "0")
        blocked_account = self.store.meta("account_mismatch") == "1"
        equity = _dec((account or {}).get("equity")) if account and not mismatch else None
        daytrade_count = _int_or_none((account or {}).get("daytrade_count")) if account and not mismatch else None
        pattern = bool((account or {}).get("pattern_day_trader")) if account and not mismatch else False
        close_at = session_close_from_clock(now, clock)
        if equity is not None:
            self._mark_day(session_date, equity)
            self._update_peak(equity)
            if loss_hit(equity, _dec(self.store.day(session_date)["equity_start"])):
                self.store.update_day(session_date, loss_brake=1)
            if drawdown_hit(equity, _dec(self.store.meta("peak_equity"))):
                self.store.set_meta("halt_reason", "DRAWDOWN")
        else:
            self.store.day(session_date)
        traded = self.cfg.symbols or (SYMBOL,)
        qtys = {symbol: self._broker_qty(blocked_account, symbol) for symbol in traded}
        for symbol in traded:
            self._reconcile(session_date, qtys[symbol], bars_by_symbol.get(symbol, []), now, allow_writes=not blocked_account, symbol=symbol)
        protective = self._protective_reason(session_date, now, close_at)
        if protective and not blocked_account:
            for symbol in traded:
                if qtys[symbol]:
                    orders.extend(self._flatten(session_date, qtys[symbol], protective, now, symbol=symbol))
        elif not blocked_account:
            buying = _dec((account or {}).get("buying_power")) if account else None
            gross = self._gross(qtys, bars_by_symbol)
            slots_left = max(0, self.cfg.max_positions - sum(1 for qty in qtys.values() if qty))
            taken = 0
            for symbol in traded:
                count = None if daytrade_count is None else daytrade_count + taken
                slot_orders, slot_signals, reserved = self._decision(
                    symbol, session_date, now, close_at, clock, bars_by_symbol.get(symbol, []),
                    qtys[symbol], equity, count, pattern, buying, gross, slots_left,
                )
                orders.extend(slot_orders)
                signals.extend(slot_signals)
                if reserved > 0:
                    taken += 1
                    slots_left = max(0, slots_left - 1)
                    gross += reserved
                    if buying is not None:
                        buying -= reserved
        for symbol in self.cfg.shadow:
            shadow_signal = self._shadow(symbol, session_date, now, close_at, bars_by_symbol.get(symbol, []), equity)
            if shadow_signal is not None:
                signals.append(shadow_signal)
        self._write_status(
            now, session_date, equity, daytrade_count, pattern, qtys, not blocked_account, close_at, account, bars_by_symbol
        )
        return CycleResult(mode="running", orders=orders, signals=signals)

    def _kill_requested(self) -> bool:
        if os.environ.get("SPY_KILL_SWITCH", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        return self.store.path.with_name("spy_kill.flag").exists()

    def _consume_resume(self) -> None:
        flag = self.store.path.with_name("spy_resume.flag")
        if not flag.exists():
            return
        if self.store.meta("halt_reason") == "DRAWDOWN":
            self.store.set_meta("halt_reason", "")
        try:
            flag.unlink()
        except OSError:
            return

    def _broker_qty(self, blocked_account: bool, symbol: str = SYMBOL) -> int:
        if blocked_account:
            return self._db_qty(symbol)
        try:
            return int(self.broker.position_qty(symbol))
        except Exception:
            return self._db_qty(symbol)

    def _db_qty(self, symbol: str = SYMBOL) -> int:
        row = self.store.open_trade("spy_trades", symbol)
        if row is None:
            return 0
        qty = int(row["qty"])
        return -qty if row["side"] == "short" else qty

    def _mark_day(self, session_date: str, equity: Decimal) -> None:
        row = self.store.day(session_date)
        text = _text(equity, "0.01")
        if row["equity_start"] in {None, ""}:
            self.store.update_day(session_date, equity_start=text, equity_last=text, pnl="0.00")
            return
        start = _dec(row["equity_start"])
        pnl = None if start is None else equity - start
        self.store.update_day(
            session_date,
            equity_last=text,
            pnl=None if pnl is None else _text(pnl, "0.01"),
            entries=self.store.entry_count(session_date),
        )

    def _update_peak(self, equity: Decimal) -> None:
        peak = _dec(self.store.meta("peak_equity"))
        if peak is None or equity > peak:
            self.store.set_meta("peak_equity", _text(equity, "0.01") or "")

    def _gross(self, qtys: dict[str, int], bars_by_symbol: dict[str, list[Bar]]) -> Decimal:
        total = Decimal(0)
        for symbol, qty in qtys.items():
            if not qty:
                continue
            bars = bars_by_symbol.get(symbol) or []
            price = bars[-1].close if bars else None
            if price is None:
                continue
            total += abs(Decimal(qty)) * price
        return total

    def _reconcile(self, session_date: str, broker_qty: int, bars: list[Bar], now: datetime, allow_writes: bool, symbol: str = SYMBOL) -> None:
        if not allow_writes:
            return
        row = self.store.open_trade("spy_trades", symbol)
        if broker_qty == 0 and row is not None:
            price = bars[-1].close if bars else _dec(row["entry_price"]) or Decimal(0)
            self._close_row("spy_trades", row, now, price, "RECONCILE_FLAT")
        if broker_qty != 0 and row is None:
            self.store.insert_trade(
                "spy_trades",
                {
                    "symbol": symbol,
                    "session_date": session_date,
                    "side": "short" if broker_qty < 0 else "long",
                    "qty": abs(broker_qty),
                    "entry_at": now.isoformat(),
                    "entry_price": None,
                },
            )

    def _protective_reason(self, session_date: str, now: datetime, close_at: datetime) -> str | None:
        halt = self.store.meta("halt_reason") or ""
        if halt == "KILL":
            return "KILL"
        if halt == "DRAWDOWN":
            return "DRAWDOWN"
        if int(self.store.day(session_date)["loss_brake"] or 0):
            return "DAILY_LOSS"
        if should_flatten(now, close_at):
            return "FLATTEN"
        return None

    def _decision(
        self,
        symbol: str,
        session_date: str,
        now: datetime,
        close_at: datetime,
        clock: dict | None,
        bars: list[Bar],
        broker_qty: int,
        equity: Decimal | None,
        daytrade_count: int | None,
        pattern: bool,
        buying: Decimal | None,
        gross: Decimal,
        slots_left: int,
    ) -> tuple[list[dict], list[dict], Decimal]:
        slot = active_slot(now, close_at)
        if slot is None:
            return [], [], Decimal(0)
        decision_at = slot.isoformat()
        if self.store.signal_for(symbol, session_date, decision_at) is not None:
            return [], [], Decimal(0)
        minute_end = minute_of(slot)
        if not is_decision_minute(minute_end):
            return [], [], Decimal(0)
        sessions = rth_sessions(bars)
        price, _opened, _previous, sigma, upper, lower, vwap = slot_snapshot(sessions, slot.date(), minute_end)
        side = "flat" if broker_qty == 0 else ("short" if broker_qty < 0 else "long")
        decision = self._raw_decision(price, upper, lower, vwap, sigma, minute_end, side, bars, now, clock)
        if decision.action in {"ENTER_LONG", "ENTER_SHORT"}:
            decision = self._gate_entry(
                decision, symbol, session_date, equity, daytrade_count, pattern, sessions, slot.date(), buying, gross, slots_left
            )
        record = _signal_row(symbol, False, session_date, decision_at, decision)
        self.store.insert_signal(record)
        placed: list[dict] = []
        reserved = Decimal(0)
        if decision.action in {"ENTER_LONG", "ENTER_SHORT"} and decision.qty > 0 and decision.price is not None:
            placed.extend(self._submit_entry(symbol, session_date, slot, decision))
            if placed:
                reserved = decision.price * Decimal(decision.qty)
        elif decision.action == "EXIT" and broker_qty != 0:
            placed.extend(self._flatten(session_date, broker_qty, "TRAIL", now, slot=slot, symbol=symbol))
        return placed, [record], reserved

    def _raw_decision(self, price, upper, lower, vwap, sigma, minute_end, side, bars, now, clock) -> Decision:
        if price is None or upper is None or lower is None:
            return Decision("BLOCKED", "DATA_GAP", price, upper, lower, vwap, None, sigma, 0, minute_end)
        if side == "flat" and not _fresh(bars, now):
            return Decision("BLOCKED", "DATA_STALE", price, upper, lower, vwap, None, sigma, 0, minute_end)
        if side == "flat" and not entries_allowed(clock):
            return Decision("BLOCKED", "SESSION_CLOSED", price, upper, lower, vwap, None, sigma, 0, minute_end)
        decision = evaluate_position(price=price, upper=upper, lower=lower, vwap=vwap, side=side, allow_short=self.cfg.allow_short)
        return Decision(decision.action, decision.reason, price, upper, lower, vwap, decision.stop, sigma, 0, minute_end)

    def _blocked(self, decision: Decision, reason: str) -> Decision:
        fields = (decision.price, decision.upper, decision.lower, decision.vwap, decision.stop, decision.sigma, decision.minute_end)
        return Decision("BLOCKED", reason, *fields[:5], fields[5], 0, fields[6])

    def _gate_entry(
        self, decision: Decision, symbol: str, session_date: str, equity, daytrade_count, pattern, sessions, day,
        buying: Decimal | None, gross: Decimal, slots_left: int,
    ) -> Decision:
        halt = self.store.meta("halt_reason") or ""
        if halt in {"KILL", "DRAWDOWN"}:
            return self._blocked(decision, halt)
        if int(self.store.day(session_date)["loss_brake"] or 0):
            return self._blocked(decision, "DAILY_LOSS")
        if slots_left <= 0:
            return self._blocked(decision, "SLOT_LIMIT")
        if self.store.entry_count(session_date, symbol) >= self.cfg.per_symbol_entries:
            return self._blocked(decision, "ENTRY_CAP")
        if self.store.entry_count(session_date) >= self.cfg.max_entries_day:
            return self._blocked(decision, "ENTRY_CAP")
        if self.store.pdt_rejected(session_date, "entry") or self.store.meta("pdt_block_date") == session_date:
            return self._blocked(decision, "PDT_REJECTED")
        reason = pdt_block_reason(equity=equity, daytrade_count=daytrade_count, pattern_day_trader=pattern, guard=self.cfg.pdt_guard)
        if reason:
            return self._blocked(decision, reason)
        volatility = daily_vol(prior_sessions(sessions, day))
        qty = 0 if equity is None or decision.price is None or volatility is None else share_qty(equity, decision.price, volatility, self.cfg.max_positions)
        if qty >= 1 and decision.price is not None and equity is not None:
            qty = fit_order_qty(qty, decision.price, equity=equity, gross=gross, buying_power=buying)
        if qty < 1:
            blocked = "BUYING_POWER" if buying is not None and (buying <= 0 or (decision.price is not None and buying < decision.price)) else "SIZE_ZERO"
            return self._blocked(decision, blocked)
        return Decision(decision.action, decision.reason, decision.price, decision.upper, decision.lower, decision.vwap, decision.stop, decision.sigma, qty, decision.minute_end)

    def _submit_entry(self, symbol: str, session_date: str, slot: datetime, decision: Decision) -> list[dict]:
        side = "buy" if decision.action == "ENTER_LONG" else "sell"
        cid = _client_id(session_date, slot.astimezone(NY).strftime("%H:%M"), "buy" if side == "buy" else "sell", symbol=symbol)
        return self._place(session_date, cid, side, decision.qty, "entry", decision.price, symbol)

    def _flatten(self, session_date: str, broker_qty: int, reason: str, now: datetime, slot: datetime | None = None, symbol: str = SYMBOL) -> list[dict]:
        purpose = "flatten" if reason == "FLATTEN" else "exit"
        if self.store.pdt_rejected(session_date, purpose):
            return []
        side = "sell" if broker_qty > 0 else "buy"
        qty = abs(broker_qty)
        stamp = slot.astimezone(NY).strftime("%H:%M") if slot else now.astimezone(NY).strftime("%H:%M")
        placed: list[dict] = []
        for attempt in range(1, 6):
            cid = _client_id(session_date, stamp, reason.lower()[:6], attempt, symbol)
            existing = self.store.order(cid)
            if existing is not None and (existing["status"] == "pdt_rejected" or existing["status"] in FILLED | WORKING):
                return placed
            try:
                result = self._submit_raw(_order_body(symbol, side, qty, cid))
            except PdtRejected as exc:
                self._save_reject(session_date, cid, side, qty, purpose, str(exc), symbol)
                return placed
            except Exception:
                continue
            status = str(result.get("status") or "accepted")
            self.store.save_order(
                {
                    "client_order_id": cid,
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "purpose": purpose,
                    "status": status,
                    "broker_order_id": result.get("id"),
                    "filled_qty": result.get("filled_qty"),
                    "filled_avg_price": result.get("filled_avg_price"),
                    "session_date": session_date,
                }
            )
            placed.append({"symbol": symbol, "side": side, "qty": qty, "client_order_id": cid, "status": status, "purpose": purpose})
            filled_qty = str(result.get("filled_qty") or "0")
            if status in FILLED or (_dec(result.get("filled_avg_price")) is not None and filled_qty not in {"", "0"}):
                price = _dec(result.get("filled_avg_price"))
                row = self.store.open_trade("spy_trades", symbol)
                if row is not None and price is not None:
                    self._close_row("spy_trades", row, now, price, reason)
            if status in FILLED or status in WORKING:
                if reason == "FLATTEN":
                    self.store.update_day(session_date, flattened=1)
                return placed
        return placed

    def _place(self, session_date: str, cid: str, side: str, qty: int, purpose: str, price: Decimal | None, symbol: str = SYMBOL) -> list[dict]:
        if self.store.order(cid) is not None:
            return []
        try:
            result = self._submit_raw(_order_body(symbol, side, qty, cid))
        except PdtRejected as exc:
            self._save_reject(session_date, cid, side, qty, purpose, str(exc), symbol)
            return []
        except Exception:
            return []
        status = str(result.get("status") or "accepted")
        self.store.save_order(
            {
                "client_order_id": cid,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "purpose": purpose,
                "status": status,
                "broker_order_id": result.get("id"),
                "filled_qty": result.get("filled_qty"),
                "filled_avg_price": result.get("filled_avg_price"),
                "session_date": session_date,
            }
        )
        if purpose == "entry" and status in FILLED | WORKING and self.store.open_trade("spy_trades", symbol) is None:
            fill = _dec(result.get("filled_avg_price")) or price
            self.store.insert_trade(
                "spy_trades",
                {
                    "symbol": symbol,
                    "session_date": session_date,
                    "side": "long" if side == "buy" else "short",
                    "qty": qty,
                    "entry_at": datetime.now(timezone.utc).isoformat(),
                    "entry_price": _text(fill),
                },
            )
            self.store.update_day(session_date, entries=self.store.entry_count(session_date))
        return [{"symbol": symbol, "side": side, "qty": qty, "client_order_id": cid, "status": status, "purpose": purpose}]

    def _submit_raw(self, body: dict) -> dict:
        symbol = str(body.get("symbol") or "")
        if symbol not in set(self.cfg.symbols):
            raise RuntimeError("refusing a symbol outside the trade list")
        try:
            return self.broker.submit(body)
        except DuplicateOrder:
            found = self.broker.order_by_client_id(str(body.get("client_order_id") or ""))
            return found or {"status": "accepted", "client_order_id": body.get("client_order_id"), "symbol": symbol}

    def _save_reject(self, session_date: str, cid: str, side: str, qty: int, purpose: str, message: str, symbol: str = SYMBOL) -> None:
        self.store.save_order(
            {
                "client_order_id": cid,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "purpose": purpose,
                "status": "pdt_rejected",
                "reject_reason": redact(message, self.cfg),
                "session_date": session_date,
            }
        )
        self.store.set_meta("pdt_block_date", session_date)

    def _close_row(self, table: str, row, now: datetime, price: Decimal, reason: str) -> None:
        entry = _dec(row["entry_price"]) or price
        pnl = _pnl(str(row["side"]), int(row["qty"]), entry, price)
        self.store.close_trade(table, int(row["id"]), now.isoformat(), _text(price) or "", reason, _text(pnl, "0.01") or "0.00")

    def _shadow(self, symbol, session_date, now, close_at, bars: list[Bar], equity) -> dict | None:
        if symbol in set(self.cfg.symbols):
            return None
        open_row = self.store.open_trade("spy_shadow_trades", symbol)
        if open_row is not None and should_flatten(now, close_at):
            price = bars[-1].close if bars else _dec(open_row["entry_price"]) or Decimal(0)
            self._close_row("spy_shadow_trades", open_row, now, price, "FLATTEN")
            record = {
                "symbol": symbol,
                "shadow": True,
                "session_date": session_date,
                "decision_at": now.astimezone(NY).replace(second=0, microsecond=0).isoformat() + "#flat",
                "minute_end": minute_of(now),
                "price": _text(price),
                "action": "EXIT",
                "reason": "FLATTEN",
                "qty": int(open_row["qty"]),
            }
            self.store.insert_signal(record)
            return record
        slot = active_slot(now, close_at)
        if slot is None:
            return None
        decision_at = slot.isoformat()
        if self.store.signal_for(symbol, session_date, decision_at) is not None:
            return None
        minute_end = minute_of(slot)
        sessions = rth_sessions(bars)
        price, _opened, _previous, sigma, upper, lower, vwap = slot_snapshot(sessions, slot.date(), minute_end)
        side = "flat" if open_row is None else str(open_row["side"])
        if price is None or upper is None or lower is None:
            decision = Decision("BLOCKED", "DATA_GAP", price, upper, lower, vwap, None, sigma, 0, minute_end)
        else:
            decision = evaluate_position(price=price, upper=upper, lower=lower, vwap=vwap, side=side, allow_short=self.cfg.allow_short)
            if decision.action in {"ENTER_LONG", "ENTER_SHORT"}:
                volatility = None if equity is None else daily_vol(prior_sessions(sessions, slot.date()))
                qty = 0 if equity is None or volatility is None else share_qty(equity, price, volatility)
                if qty < 1:
                    decision = Decision("BLOCKED", "SIZE_ZERO", price, upper, lower, vwap, decision.stop, sigma, 0, minute_end)
                else:
                    decision = Decision(decision.action, decision.reason, price, upper, lower, vwap, decision.stop, sigma, qty, minute_end)
            else:
                decision = Decision(decision.action, decision.reason, price, upper, lower, vwap, decision.stop, sigma, 0, minute_end)
        record = _signal_row(symbol, True, session_date, decision_at, decision)
        if not self.store.insert_signal(record):
            return record
        if decision.action in {"ENTER_LONG", "ENTER_SHORT"} and decision.qty > 0 and open_row is None:
            self.store.insert_trade(
                "spy_shadow_trades",
                {
                    "symbol": symbol,
                    "session_date": session_date,
                    "side": "long" if decision.action == "ENTER_LONG" else "short",
                    "qty": decision.qty,
                    "entry_at": slot.isoformat(),
                    "entry_price": _text(price),
                },
            )
        elif decision.action == "EXIT" and open_row is not None and price is not None:
            self._close_row("spy_shadow_trades", open_row, slot, price, "TRAIL")
        return record

    def _symbol_card(self, symbol: str, qty: int, bars: list[Bar], session_date: str) -> dict:
        from app.spy.strategy import bands, session_open, sigma_at, vwap_through

        sessions = rth_sessions(bars)
        price = upper = lower = vwap = None
        if sessions:
            day = max(sessions)
            today = sessions[day]
            minute = today[-1].end_minute()
            prior = prior_sessions(sessions, day)
            sigma = sigma_at(prior, minute)
            opened = session_open(today)
            previous = prior[-1][-1].close if prior else None
            if opened is not None and previous is not None and sigma is not None:
                upper, lower = bands(opened, previous, sigma)
            vwap = vwap_through(today, minute)
            price = today[-1].close
        trade = self.store.open_trade("spy_trades", symbol)
        realized = Decimal(0)
        for row in self.store.recent_trades(200):
            if row["symbol"] == symbol and row["session_date"] == session_date and row["pnl"] not in {None, ""}:
                realized += Decimal(str(row["pnl"]))
        unreal = Decimal(0)
        if trade is not None and price is not None and trade["entry_price"] not in {None, ""}:
            unreal = _pnl(str(trade["side"]), int(trade["qty"]), Decimal(str(trade["entry_price"])), price)
        return {
            "symbol": symbol,
            "traded": True,
            "price": _text(price),
            "upper": _text(upper),
            "lower": _text(lower),
            "vwap": _text(vwap),
            "position": {
                "side": "flat" if qty == 0 else ("short" if qty < 0 else "long"),
                "qty": abs(qty),
                "entry_price": None if trade is None else trade["entry_price"],
            },
            "pnl": _text(realized + unreal, "0.01"),
        }

    def _write_status(self, now, session_date, equity, daytrade_count, pattern, qtys, matched, close_at, account, bars_by_symbol) -> None:
        start = _dec(self.store.day(session_date)["equity_start"])
        pnl = None if equity is None or start is None else equity - start
        left = day_trades_left(equity=equity, daytrade_count=daytrade_count, pattern_day_trader=pattern, guard=self.cfg.pdt_guard)
        cards = [self._symbol_card(symbol, qty, bars_by_symbol.get(symbol, []), session_date) for symbol, qty in qtys.items()]
        primary = cards[0]["position"] if cards else {"side": "flat", "qty": 0, "entry_price": None}
        shadows = []
        for symbol in self.cfg.shadow:
            row = self.store.open_trade("spy_shadow_trades", symbol)
            realized = Decimal(0)
            for item in self.store.shadow_trades(symbol, limit=40):
                if item["session_date"] == session_date and item["pnl"] not in {None, ""}:
                    realized += Decimal(str(item["pnl"]))
            unreal = Decimal(0)
            bars = bars_by_symbol.get(symbol) or []
            if row is not None and bars and row["entry_price"] not in {None, ""}:
                unreal = _pnl(str(row["side"]), int(row["qty"]), Decimal(str(row["entry_price"])), bars[-1].close)
            shadows.append(
                {
                    "symbol": symbol,
                    "side": None if row is None else row["side"],
                    "qty": 0 if row is None else int(row["qty"]),
                    "entry_price": None if row is None else row["entry_price"],
                    "day_pnl": _text(realized + unreal, "0.01"),
                    "hypothetical": True,
                    "orders": False,
                }
            )
        actual_last4 = str((account or {}).get("account_number") or "")[-4:]
        payload = {
            "ok": True,
            "enabled": True,
            "mode": "running",
            "paper_only": True,
            "symbol": cards[0]["symbol"] if cards else SYMBOL,
            "symbols": cards,
            "account_pinned": bool(self.cfg.account),
            "account_last4": self.cfg.account[-4:] if self.cfg.account else actual_last4,
            "account_match": matched,
            "allow_short": self.cfg.allow_short,
            "session_date": session_date,
            "now_ny": now.astimezone(NY).isoformat(),
            "flatten_at": close_at.astimezone(NY).isoformat(),
            "position": primary,
            "day_pnl": _text(pnl, "0.01"),
            "equity": _text(equity, "0.01"),
            "equity_start": _text(start, "0.01"),
            "day_trades_left": left,
            "daytrade_count": daytrade_count,
            "pattern_day_trader": pattern,
            "pdt_guard": self.cfg.pdt_guard,
            "brakes": {
                "daily_loss": bool(int(self.store.day(session_date)["loss_brake"] or 0)),
                "drawdown": (self.store.meta("halt_reason") or "") == "DRAWDOWN",
                "kill": (self.store.meta("halt_reason") or "") == "KILL",
                "halt_reason": self.store.meta("halt_reason") or "",
                "entries_today": self.store.entry_count(session_date),
                "entries_max": self.cfg.max_entries_day,
                "pdt_blocked": self.store.meta("pdt_block_date") == session_date,
            },
            "shadow": shadows,
            "rules": {
                "long_only": not self.cfg.allow_short,
                "leverage": "1x",
                "lookback": 14,
                "decisions": "HH:00 and HH:30 from 10:00 to 15:30 America/New_York",
                "flatten": "2 minutes before the close",
            },
        }
        path = self.store.path.with_name("spy_status.json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        self.store.path.with_name("spy_heartbeat.txt").write_text(now.astimezone(timezone.utc).isoformat(), encoding="utf-8")


def write_idle_status(cfg: SpyConfig, mode: str, now: datetime | None = None) -> None:
    moment = now or datetime.now(timezone.utc)
    payload = {
        "ok": True,
        "enabled": False,
        "mode": mode,
        "paper_only": True,
        "symbol": SYMBOL,
        "account_pinned": bool(cfg.account),
        "account_last4": cfg.account[-4:] if cfg.account else "",
        "allow_short": cfg.allow_short,
        "pdt_guard": cfg.pdt_guard,
        "position": {"side": "flat", "qty": 0, "entry_price": None},
        "day_pnl": None,
        "equity": None,
        "day_trades_left": None,
        "shadow": [{"symbol": symbol, "hypothetical": True, "orders": False, "day_pnl": None, "qty": 0, "side": None} for symbol in cfg.shadow],
        "brakes": {
            "kill": False,
            "daily_loss": False,
            "drawdown": False,
            "halt_reason": "LIVE_URL" if mode == "refused" else "",
            "entries_today": 0,
            "entries_max": cfg.max_entries_day,
        },
        "rules": {"long_only": not cfg.allow_short, "leverage": "1x", "lookback": 14},
    }
    path = cfg.db.with_name("spy_status.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    cfg.db.with_name("spy_heartbeat.txt").write_text(moment.isoformat(), encoding="utf-8")
