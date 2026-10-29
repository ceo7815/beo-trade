from __future__ import annotations

from app.broker.normalize import (
    fill_from_activity,
    normalize_account,
    normalize_activity,
    normalize_asset,
    normalize_clock,
    normalize_configuration,
    normalize_order,
    normalize_position,
)
from app.integrations.alpaca import ALLOWED_INTENTS, OPTION_ORDER_TYPES, AlpacaPaperClient, PaperOnlyError, option_order


class AlpacaAdapter:
    name = "alpaca"

    def __init__(self, client: AlpacaPaperClient) -> None:
        self.client = client
        self._positions: list[dict] = []

    def get_account(self, *, fresh: bool = False) -> dict:
        if self.client.shared_cache:
            from app.broker.snapshot import read_shared

            account = read_shared("account", fresh=fresh)
            return account if isinstance(account, dict) else {}
        return self._read_account()

    def _read_account(self) -> dict:
        account = normalize_account(self.client.account())
        try:
            account["configuration"] = normalize_configuration(self.client.configurations())
        except Exception as exc:
            if exc.__class__.__name__ not in {"HTTPStatusError", "HTTPError", "ConnectError", "TimeoutException"}:
                raise
            account["configuration"] = {}
        return account

    def get_positions(self, *, fresh: bool = False) -> list[dict]:
        """`fresh=True` is the exit and order path: refresh when the snapshot has reached the TTL."""
        if self.client.shared_cache:
            from app.broker.snapshot import read_shared

            rows = read_shared("positions", fresh=fresh)
            self._positions = list(rows) if isinstance(rows, list) else []
            return list(self._positions)
        self._positions = [normalize_position(row) for row in self.client.positions()]
        return list(self._positions)

    def sync_positions(self) -> list[dict]:
        # Monitor exits (stop, trail, session close, kill, floor) start here.
        return self.get_positions(fresh=True)

    def get_orders(self, status: str = "all", symbol: str = "", asset_class: str = "", after: str = "", until: str = "", *, fresh: bool = False) -> list[dict]:
        cacheable = self.client.shared_cache and status == "open" and not symbol and not after and not until
        if cacheable:
            from app.broker.snapshot import read_shared

            cached = read_shared("open_orders", fresh=fresh)
            rows = list(cached) if isinstance(cached, list) else []
        else:
            rows = [normalize_order(row) for row in self.client.orders(status=status, symbol=symbol, after=after, until=until)]
        if asset_class:
            rows = [row for row in rows if row.get("asset_class") == asset_class]
        return rows

    def get_order(self, order_id: str) -> dict:
        return normalize_order(self.client.order(order_id))

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        raw = self.client.order_by_client_id(client_order_id)
        return None if raw is None else normalize_order(raw)

    def get_activities(self, activity_types: str = "", after: str = "", until: str = "") -> list[dict]:
        return [normalize_activity(row) for row in self.client.activities(activity_types, after, until)]

    def get_session_activities(self, session_date, *, max_pages: int = 4) -> list[dict]:
        """Funding activities around one New York session. Does not walk the whole account.

        Alpaca returns at most 100 rows per page. A full page asks for the next id,
        and the page cap stops a token that never ends.
        """
        from app.analytics.finance import session_funding_query

        types, after, until = session_funding_query(session_date)
        found: list[dict] = []
        token = ""
        seen: set[str] = set()
        pages = max(1, int(max_pages))
        for index in range(pages):
            page = self.client.activities(types, after=after, until=until, page_token=token)
            if not isinstance(page, list) or not page:
                break
            chunk = page[:100]
            found.extend(normalize_activity(row) for row in chunk)
            if len(page) < 100:
                break
            token = str(chunk[-1].get("id") or "")
            if not token or token in seen:
                break
            # A full last page means the window did not fit. Callers treat that as a failed read.
            if index == pages - 1:
                raise RuntimeError("Alpaca activity page cap reached")
            seen.add(token)
        return found

    def get_fills(self) -> list[dict]:
        fills = []
        for row in self.client.activities("FILL"):
            item = fill_from_activity(row)
            if item is not None:
                fills.append(item)
        return fills

    def get_all_fills(self, max_pages: int = 20, after: str = "") -> list[dict]:
        """Page through FILL activities. `after` is YYYY-MM-DD so a short window does not download the whole account."""
        fills = []
        token = ""
        for _ in range(max_pages):
            page = self.client.activities("FILL", page_token=token, after=after)
            for row in page:
                item = fill_from_activity(row)
                if item is not None:
                    fills.append(item)
            if len(page) < 100:
                break
            token = str(page[-1].get("id") or "")
            if not token:
                break
        return fills

    def get_market_clock(self, *, fresh: bool = False) -> dict:
        if self.client.shared_cache:
            from app.broker.snapshot import read_shared

            clock = read_shared("clock", fresh=fresh)
            return clock if isinstance(clock, dict) else {}
        return normalize_clock(self.client.clock())

    def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        raw = self.client.portfolio_history(period, timeframe)
        timestamps = raw.get("timestamp") if isinstance(raw.get("timestamp"), list) else []
        equity = raw.get("equity") if isinstance(raw.get("equity"), list) else []
        profit = raw.get("profit_loss") if isinstance(raw.get("profit_loss"), list) else []
        points = []
        for index, stamp in enumerate(timestamps):
            point = {
                "timestamp": stamp,
                "equity": equity[index] if index < len(equity) else None,
                "profit_loss": profit[index] if index < len(profit) else None,
            }
            if point["equity"] is None and point["profit_loss"] is None:
                continue
            points.append(point)
        base = raw.get("base_value")
        return {"source": "alpaca-paper", "period": period, "timeframe": timeframe, "base_value": base, "points": points}

    def list_optionable_equities(self) -> list[dict]:
        return self.client.assets()

    def get_asset(self, symbol: str) -> dict:
        return normalize_asset(self.client.asset(symbol))

    def submit_entry_order(self, body: dict) -> dict:
        if body.get("position_intent") != "buy_to_open":
            raise PaperOnlyError("פקודת כניסה מותרת רק כ-buy_to_open.")
        return normalize_order(self.client.submit(body))

    def submit_exit_order(self, body: dict) -> dict:
        if body.get("position_intent") != "sell_to_close":
            raise PaperOnlyError("פקודת יציאה מותרת רק כ-sell_to_close.")
        symbol = str(body.get("symbol") or "")
        qty = _qty(body.get("qty"))
        held = _held_qty(self._positions_for_exit(), symbol)
        if held <= 0:
            raise PaperOnlyError("אין פוזיציה פתוחה אצל הברוקר לסגירה.")
        if qty > held:
            raise PaperOnlyError("הכמות גדולה מהכמות הפתוחה אצל הברוקר.")
        return normalize_order(self.client.submit(body))

    def _positions_for_exit(self) -> list[dict]:
        if self.client.shared_cache:
            return self.get_positions(fresh=True)
        return self._positions or self.get_positions()

    def close(self) -> None:
        self.client.close()

    def cancel_order(self, order_id: str) -> None:
        self.client.cancel(order_id)

    def replace_order(self, order_id: str, body: dict) -> dict:
        return normalize_order(self.client.replace(order_id, body))

    def reconcile(self) -> dict:
        from app.broker.store import reconcile_broker
        from app.models.db import session_scope

        with session_scope() as session:
            report = reconcile_broker(session, self)
            session.commit()
            return report

    def capabilities(self) -> dict:
        return {
            "environment": "PAPER",
            "order_types": list(OPTION_ORDER_TYPES),
            "documented_position_intents": ["buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"],
            "strategy_position_intents": list(ALLOWED_INTENTS),
            "protective_orders": "strategy_exits_stay_in_beo_trade",
            "order_class": "simple",
        }


def option_entry(symbol: str, qty: int, limit_price: str, client_order_id: str) -> dict:
    return option_order(symbol, qty, limit_price, "buy_to_open", client_order_id)


def option_exit(symbol: str, qty: int, limit_price: str, client_order_id: str) -> dict:
    return option_order(symbol, qty, limit_price, "sell_to_close", client_order_id)


def _qty(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _held_qty(positions: list[dict], symbol: str) -> int:
    for row in positions:
        if str(row.get("symbol") or "").replace(" ", "") == symbol.replace(" ", ""):
            return abs(_qty(row.get("qty")))
    return 0
