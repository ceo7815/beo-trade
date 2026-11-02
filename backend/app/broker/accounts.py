"""Multi-account Alpaca access for period viewing.

The active trading account always uses ALPACA_API_KEY / ALPACA_API_SECRET.
The legacy 100K paper account may be viewed read-only via
ALPACA_100K_API_KEY / ALPACA_100K_API_SECRET. Trading / order submission
never uses the 100K keys. When 100K keys are absent, callers fall back to
DB history for that period.
"""

from __future__ import annotations

from app.analytics.periods import current_period_id, resolve_period
from app.broker.adapter import AlpacaAdapter
from app.integrations.alpaca import AlpacaNotConfigured, AlpacaPaperClient, PaperOnlyError
from app.integrations.secrets import resolve_secret


def period_is_trading(period_id: str | None) -> bool:
    current = current_period_id()
    key = (period_id or "").strip() or current
    if key in {"", "current", current}:
        return True
    period = resolve_period(key)
    if period is None:
        return True
    return bool(period.trading) and not bool(period.read_only)


def open_view_broker(settings, period_id: str | None = None) -> AlpacaAdapter:
    """Open a paper client for reads. Non-trading periods use 100K keys when set."""
    from app.broker.snapshot import remember_settings

    remember_settings(settings)
    key_name, secret_name = _keys_for_period(period_id)
    key, _ = resolve_secret(settings, key_name)
    secret, _ = resolve_secret(settings, secret_name)
    if not key or not secret:
        raise AlpacaNotConfigured("נדרש API Key")
    client = AlpacaPaperClient(key, secret, settings.trading_mode)
    return AlpacaAdapter(client)


def open_trading_broker(settings) -> AlpacaAdapter:
    """Always the primary (active) account — the only client allowed to submit orders."""
    from app.broker.runtime import open_paper_broker

    return open_paper_broker(settings)


def has_view_keys(settings, period_id: str | None) -> bool:
    key_name, secret_name = _keys_for_period(period_id)
    key, _ = resolve_secret(settings, key_name)
    secret, _ = resolve_secret(settings, secret_name)
    return bool(key and secret)


def _keys_for_period(period_id: str | None) -> tuple[str, str]:
    current = current_period_id()
    key = (period_id or "").strip() or current
    if key in {"", "current", current, "paper_10k_ils"}:
        return "alpaca_api_key", "alpaca_api_secret"
    period = resolve_period(key)
    if period is not None and (period.read_only or period.id == "paper_100k"):
        return "alpaca_100k_api_key", "alpaca_100k_api_secret"
    return "alpaca_api_key", "alpaca_api_secret"
