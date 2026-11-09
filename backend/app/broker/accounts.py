"""Multi-account Alpaca access for period viewing.

The active trading account always uses ALPACA_API_KEY / ALPACA_API_SECRET.
The legacy 100K paper account may be viewed read-only via
ALPACA_100K_API_KEY / ALPACA_100K_API_SECRET. Trading / order submission
never uses the 100K keys. When 100K keys are absent, callers fall back to
DB history for that period.

PA3LU0NE5 (beo 10K ILS) was deleted. That period stays for stored history
and never opens a broker client, so a dead credential cannot affect the
options engine.
"""

from __future__ import annotations

import time

from app.analytics.periods import TradingPeriod, current_period_id, resolve_period
from app.broker.adapter import AlpacaAdapter
from app.integrations.alpaca import AlpacaNotConfigured, AlpacaPaperClient
from app.integrations.secrets import resolve_secret

RETIRED_ACCOUNT_NUMBERS = frozenset({"PA3LU0NE5", "PA3YUE43D"})
_VIEW_TTL_SECONDS = 600.0
_view_cache: dict[str, tuple[float, bool]] = {}


class ViewAccountUnavailable(AlpacaNotConfigured):
    """The selected period has no live broker. Callers fall back to stored history."""


def period_is_trading(period_id: str | None) -> bool:
    current = current_period_id()
    key = (period_id or "").strip() or current
    if key in {"", "current", current}:
        return True
    period = resolve_period(key)
    if period is None:
        return True
    return bool(period.trading) and not bool(period.read_only)


def is_retired(period: TradingPeriod | None) -> bool:
    if period is None:
        return False
    if period.retired:
        return True
    return period.alpaca_account_number in RETIRED_ACCOUNT_NUMBERS


def _period_for(period_id: str | None) -> TradingPeriod | None:
    key = (period_id or "").strip()
    if key in {"", "current"}:
        return resolve_period("current")
    return resolve_period(key)


def open_view_broker(settings, period_id: str | None = None) -> AlpacaAdapter:
    """Open a paper client for reads. A retired account never opens a client.

    View clients do not share the trading snapshot, so a failed credential
    cannot refresh or replace the options book.
    """
    from app.broker.snapshot import remember_settings

    remember_settings(settings)
    if is_retired(_period_for(period_id)):
        raise ViewAccountUnavailable("החשבון לא זמין")
    key_name, secret_name = _keys_for_period(period_id)
    if not key_name or not secret_name:
        raise AlpacaNotConfigured("נדרש API Key")
    key, _ = resolve_secret(settings, key_name)
    secret, _ = resolve_secret(settings, secret_name)
    if not key or not secret:
        raise AlpacaNotConfigured("נדרש API Key")
    client = AlpacaPaperClient(key, secret, settings.trading_mode, shared_cache=False)
    return AlpacaAdapter(client)


def open_trading_broker(settings) -> AlpacaAdapter:
    """Always the primary (active) account — the only client allowed to submit orders."""
    from app.broker.runtime import open_paper_broker

    return open_paper_broker(settings)


def has_view_keys(settings, period_id: str | None) -> bool:
    key_name, secret_name = _keys_for_period(period_id)
    if not key_name or not secret_name:
        return False
    key, _ = resolve_secret(settings, key_name)
    secret, _ = resolve_secret(settings, secret_name)
    return bool(key and secret)


def period_read_source(settings, period_id: str | None) -> str:
    """trading | view | none. none skips the broker instead of raising into the options loop."""
    if period_is_trading(period_id):
        return "trading"
    if is_retired(_period_for(period_id)):
        return "none"
    if has_view_keys(settings, period_id):
        return "view"
    return "none"


def view_broker_reachable(settings, period_id: str | None) -> bool:
    """True when a non-trading view account answers. Failures stay in this function."""
    key = (period_id or "").strip() or "current"
    if is_retired(_period_for(key)):
        return False
    if not has_view_keys(settings, key):
        return False
    now = time.time()
    cached = _view_cache.get(key)
    if cached is not None and now - cached[0] < _VIEW_TTL_SECONDS:
        return cached[1]
    ok = False
    try:
        adapter = open_view_broker(settings, key)
        try:
            account = adapter.get_account()
            number = str((account or {}).get("account_number") or "")
            ok = bool(account) and number not in RETIRED_ACCOUNT_NUMBERS
        finally:
            adapter.close()
    except Exception:
        ok = False
    _view_cache[key] = (now, ok)
    return ok


def _keys_for_period(period_id: str | None) -> tuple[str, str]:
    current = current_period_id()
    key = (period_id or "").strip() or current
    if key in {"", "current", current}:
        return "alpaca_api_key", "alpaca_api_secret"
    period = resolve_period(key)
    if is_retired(period):
        return "", ""
    if period is not None and period.id == current and period.trading and not period.read_only:
        return "alpaca_api_key", "alpaca_api_secret"
    if period is not None and (period.read_only or period.id == "paper_100k"):
        return "alpaca_100k_api_key", "alpaca_100k_api_secret"
    if period is not None and not period.trading:
        return "", ""
    return "alpaca_api_key", "alpaca_api_secret"
