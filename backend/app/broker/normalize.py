from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

ACCOUNT_FIELDS = (
    "id",
    "account_number",
    "status",
    "currency",
    "cash",
    "equity",
    "portfolio_value",
    "buying_power",
    "regt_buying_power",
    "daytrading_buying_power",
    "options_buying_power",
    "effective_buying_power",
    "non_marginable_buying_power",
    "initial_margin",
    "maintenance_margin",
    "last_maintenance_margin",
    "long_market_value",
    "short_market_value",
    "position_market_value",
    "cash_withdrawable",
    "cash_transferable",
    "pending_transfer_in",
    "pending_transfer_out",
    "accrued_fees",
    "pending_reg_taf_fees",
    "trading_blocked",
    "transfers_blocked",
    "account_blocked",
    "trade_suspended_by_user",
    "shorting_enabled",
    "pattern_day_trader",
    "daytrade_count",
    "multiplier",
    "last_equity",
    "balance_asof",
    "created_at",
    "options_approved_level",
    "options_trading_level",
    "crypto_status",
)

CONFIGURATION_FIELDS = (
    "dtbp_check",
    "trade_confirm_email",
    "suspend_trade",
    "no_shorting",
    "fractional_trading",
    "max_margin_multiplier",
    "pdt_check",
    "ptp_no_exception_entry",
    "max_options_trading_level",
)

POSITION_FIELDS = (
    "asset_id",
    "symbol",
    "exchange",
    "asset_class",
    "asset_marginable",
    "qty",
    "qty_available",
    "avg_entry_price",
    "side",
    "market_value",
    "cost_basis",
    "unrealized_pl",
    "unrealized_plpc",
    "unrealized_intraday_pl",
    "unrealized_intraday_plpc",
    "current_price",
    "lastday_price",
    "change_today",
)

ORDER_FIELDS = (
    "id",
    "client_order_id",
    "created_at",
    "updated_at",
    "submitted_at",
    "filled_at",
    "expired_at",
    "canceled_at",
    "failed_at",
    "replaced_at",
    "replaced_by",
    "replaces",
    "asset_id",
    "symbol",
    "asset_class",
    "notional",
    "qty",
    "filled_qty",
    "filled_avg_price",
    "order_class",
    "order_type",
    "type",
    "side",
    "time_in_force",
    "limit_price",
    "stop_price",
    "status",
    "extended_hours",
    "legs",
    "trail_percent",
    "trail_price",
    "hwm",
    "position_intent",
    "source",
    "ratio_qty",
)

ASSET_FIELDS = (
    "id",
    "class",
    "exchange",
    "symbol",
    "name",
    "status",
    "tradable",
    "marginable",
    "shortable",
    "easy_to_borrow",
    "fractionable",
    "attributes",
)

CLOCK_FIELDS = ("timestamp", "is_open", "next_open", "next_close")

ACTIVITY_FIELDS = (
    "id",
    "activity_type",
    "transaction_time",
    "type",
    "price",
    "qty",
    "side",
    "symbol",
    "leaves_qty",
    "order_id",
    "cum_qty",
    "order_status",
    "net_amount",
    "date",
    "description",
)

STATUS_TO_INTERNAL = {
    "new": "SUBMITTED",
    "pending_new": "SUBMITTED",
    "accepted": "ACCEPTED",
    "accepted_for_bidding": "ACCEPTED",
    "pending_review": "ACCEPTED",
    "calculated": "ACCEPTED",
    "partially_filled": "PARTIALLY_FILLED",
    "filled": "FILLED",
    "pending_cancel": "CANCEL_REQUESTED",
    "canceled": "CANCELLED",
    "cancelled": "CANCELLED",
    "rejected": "REJECTED",
    "stopped": "REJECTED",
    "suspended": "REJECTED",
    "expired": "EXPIRED",
    "done_for_day": "DONE_FOR_DAY",
    "replaced": "REPLACED",
    "pending_replace": "REPLACED",
}

EVENT_TO_INTERNAL = {
    "new": "SUBMITTED",
    "pending_new": "SUBMITTED",
    "accepted": "ACCEPTED",
    "partial_fill": "PARTIALLY_FILLED",
    "fill": "FILLED",
    "canceled": "CANCELLED",
    "rejected": "REJECTED",
    "expired": "EXPIRED",
    "done_for_day": "DONE_FOR_DAY",
    "replaced": "REPLACED",
}

OPEN_ORDER_STATES = {"CREATED", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_REQUESTED"}
OCC = re.compile(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def present(raw: dict, fields: tuple[str, ...]) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {name: raw[name] for name in fields if name in raw and raw[name] is not None}


def internal_state(status: str) -> str:
    return STATUS_TO_INTERNAL.get(str(status or "").lower(), "ERROR")


def normalize_account(raw: dict) -> dict:
    item = present(raw, ACCOUNT_FIELDS)
    item["source"] = "alpaca-paper"
    return item


def normalize_configuration(raw: dict) -> dict:
    return present(raw, CONFIGURATION_FIELDS)


def parse_option_symbol(symbol: str) -> dict | None:
    match = OCC.match(str(symbol or "").replace(" ", "").upper())
    if match is None:
        return None
    root, year, month, day, right, strike_raw = match.groups()
    strike = (Decimal(strike_raw) / Decimal("1000")).quantize(Decimal("0.001"))
    return {
        "underlying": root,
        "expiration": f"20{year}-{month}-{day}",
        "right": "CALL" if right == "C" else "PUT",
        "strike": format(strike.normalize(), "f"),
    }


def normalize_position(raw: dict) -> dict:
    item = present(raw, POSITION_FIELDS)
    item["source"] = "alpaca-paper"
    item["source_provider"] = "alpaca"
    contract = parse_option_symbol(str(item.get("symbol") or ""))
    if contract and str(raw.get("asset_class") or "") in {"", "us_option"}:
        item.update(contract)
    mark = item.get("current_price")
    if mark in {None, ""}:
        item.pop("unrealized_pl", None)
        item.pop("unrealized_plpc", None)
    return item


def normalize_order(raw: dict, trade_id: str = "") -> dict:
    item = present(raw, ORDER_FIELDS)
    item["broker_order_id"] = str(item.get("id") or "")
    item["internal_state"] = internal_state(str(item.get("status") or ""))
    item["source"] = "alpaca-paper"
    if trade_id:
        item["trade_id"] = trade_id
    contract = parse_option_symbol(str(item.get("symbol") or ""))
    if contract:
        item["contract"] = contract
    legs = raw.get("legs") if isinstance(raw.get("legs"), list) else None
    if legs is not None:
        item["legs"] = [normalize_order(leg) for leg in legs if isinstance(leg, dict)]
    return item


def normalize_clock(raw: dict) -> dict:
    item = present(raw, CLOCK_FIELDS)
    item["source"] = "alpaca-paper"
    return item


def normalize_asset(raw: dict) -> dict:
    item = present(raw, ASSET_FIELDS)
    attributes = raw.get("attributes")
    if isinstance(attributes, list) and "has_options" in attributes:
        item["has_options"] = True
    elif isinstance(attributes, list):
        item["has_options"] = False
    item["source"] = "alpaca-paper"
    return item


def normalize_activity(raw: dict) -> dict:
    item = present(raw, ACTIVITY_FIELDS)
    item["source"] = "alpaca-paper"
    return item


def fill_from_activity(raw: dict, trade_id: str = "") -> dict | None:
    if str(raw.get("activity_type") or "").upper() != "FILL":
        return None
    execution_id = str(raw.get("id") or "").strip()
    if not execution_id:
        return None
    item = {
        "execution_id": execution_id,
        "broker_order_id": str(raw.get("order_id") or ""),
        "symbol": str(raw.get("symbol") or ""),
        "side": str(raw.get("side") or ""),
        "qty": raw.get("qty"),
        "price": raw.get("price"),
        "timestamp": raw.get("transaction_time"),
        "cumulative_qty": raw.get("cum_qty"),
        "source": "alpaca-paper",
    }
    if trade_id:
        item["trade_id"] = trade_id
    return {key: value for key, value in item.items() if value is not None}


def execution_pnl(entry_price: str, exit_price: str, qty: str, fees: str | None = None, multiplier: int = 100) -> dict | None:
    """Gross is the fill difference. Net exists only when the broker supplied a fee."""
    try:
        entry = Decimal(str(entry_price))
        exit_value = Decimal(str(exit_price))
        quantity = Decimal(str(qty))
    except Exception:
        return None
    if entry <= 0 or quantity <= 0:
        return None
    fee = None
    if fees not in {None, ""}:
        try:
            fee = Decimal(str(fees))
        except Exception:
            fee = None
    gross = (exit_value - entry) * quantity * Decimal(multiplier)
    basis = entry * quantity * Decimal(multiplier)
    net = None if fee is None else gross - fee
    return {
        "gross_pnl": format(gross.quantize(Decimal("0.01")), "f"),
        "fees": None if fee is None else format(fee.quantize(Decimal("0.01")), "f"),
        "net_pnl": None if net is None else format(net.quantize(Decimal("0.01")), "f"),
        "return_pct": format((gross / basis).quantize(Decimal("0.0001")), "f"),
        "fees_note": None if fee is not None else "עמלות טרם זמינות",
        "source": "alpaca-paper",
    }


def clock_day(raw: dict) -> str:
    stamp = str(raw.get("timestamp") or "")
    if len(stamp) >= 10 and stamp[4] == "-" and stamp[7] == "-":
        return stamp[:10]
    return ""


def parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
