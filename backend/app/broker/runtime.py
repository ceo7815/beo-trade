from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.broker.adapter import AlpacaAdapter
from app.integrations.alpaca import AlpacaNotConfigured, AlpacaPaperClient, PaperOnlyError
from app.integrations.secrets import resolve_secret, scrub
from app.models.db import session_scope
from app.models.tables import BrokerReconciliation


def broker_risk_snapshot(
    account: dict,
    equity_used: Decimal,
    quantity: int,
    ask: Decimal,
    multiplier: int,
    risk_pct: Decimal,
) -> dict | None:
    """The order may proceed only when sizing used this Alpaca account, not the internal ledger."""

    def money(key: str, fallback: str | None = None) -> Decimal | None:
        raw = account.get(key)
        if raw in {None, ""} and fallback is not None:
            raw = account.get(fallback)
        if raw in {None, ""}:
            return None
        return Decimal(str(raw))

    broker_equity = money("equity")
    broker_cash = money("cash")
    broker_buying_power = money("options_buying_power", "buying_power")
    if broker_equity is None or broker_cash is None or broker_buying_power is None:
        return None
    if broker_equity != equity_used:
        return None
    cost = ask * Decimal(quantity) * Decimal(multiplier)
    return {
        "broker_equity": str(broker_equity),
        "broker_cash": str(broker_cash),
        "broker_buying_power": str(broker_buying_power),
        "risk_equity_used": str(equity_used),
        "risk_budget": str(equity_used * risk_pct),
        "planned_position_cost": str(cost),
        "final_quantity": int(quantity),
    }


def paper_equity(settings) -> Decimal | None:
    """Account equity from the paper broker. None when the account cannot be read."""
    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError):
        return None
    try:
        account = adapter.get_account()
    except Exception:
        return None
    finally:
        adapter.close()
    raw = account.get("equity")
    if raw in {None, ""}:
        return None
    return Decimal(str(raw))


def open_option_symbols(settings) -> set[str]:
    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError):
        return set()
    try:
        rows = adapter.get_positions()
    except Exception:
        return set()
    finally:
        adapter.close()
    return {str(row.get("symbol") or "") for row in rows if row.get("symbol")}


def open_paper_broker(settings) -> AlpacaAdapter:
    """The only place that constructs the Alpaca paper client for business calls."""
    key, _ = resolve_secret(settings, "alpaca_api_key")
    secret, _ = resolve_secret(settings, "alpaca_api_secret")
    client = AlpacaPaperClient(key, secret, settings.trading_mode)
    return AlpacaAdapter(client)


def reconcile_if_configured(settings) -> None:
    key, _ = resolve_secret(settings, "alpaca_api_key")
    secret, _ = resolve_secret(settings, "alpaca_api_secret")
    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError):
        return
    try:
        adapter.reconcile()
    except Exception as exc:
        with session_scope() as session:
            session.add(
                BrokerReconciliation(
                    ok=False,
                    mismatches=[{"kind": "broker", "broker": scrub(str(exc), [key, secret])}],
                    observed_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
    finally:
        adapter.close()
