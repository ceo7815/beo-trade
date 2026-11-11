"""Paper guard for Options 2. Refuses an order that is not this paper account.

The refusal is a short code. The account number and the keys are not included.
"""

from __future__ import annotations

from app.options2.desk import PAPER_HOST, host_refused


def _pin_ok(pin: str) -> bool:
    value = (pin or "").strip().upper()
    return 4 <= len(value) <= 8 and value.isalnum()


def account_matches(pin: str, account_number: str) -> bool:
    """The broker account number must end with the configured suffix."""
    suffix = (pin or "").strip().upper()
    number = (account_number or "").strip().upper()
    if not _pin_ok(suffix):
        return False
    return len(number) > len(suffix) and number.endswith(suffix)


def trading_allowed(settings) -> tuple[bool, str]:
    """Whether this process may send orders. The main desk is always allowed here."""
    if str(getattr(settings, "desk_id", "") or "").strip() != "options2":
        return True, ""
    if host_refused():
        return False, "OPTIONS2_NOT_PAPER"
    base = str(getattr(settings, "alpaca_base_url", "") or "").strip().rstrip("/")
    if base != PAPER_HOST:
        return False, "OPTIONS2_NOT_PAPER"
    if not bool(getattr(settings, "options2_enabled", False)):
        return False, "OPTIONS2_DISABLED"
    if not str(getattr(settings, "alpaca_api_key", "") or "").strip():
        return False, "OPTIONS2_NOT_CONFIGURED"
    if not str(getattr(settings, "alpaca_api_secret", "") or "").strip():
        return False, "OPTIONS2_NOT_CONFIGURED"
    if not _pin_ok(str(getattr(settings, "options2_account_last4", "") or "")):
        return False, "OPTIONS2_NOT_CONFIGURED"
    return True, ""


def order_refusal(settings, account: dict | None) -> str | None:
    """Block every Options 2 order unless the live account suffix matches."""
    allowed, reason = trading_allowed(settings)
    if str(getattr(settings, "desk_id", "") or "").strip() != "options2":
        return None
    if not allowed:
        return reason
    number = ""
    if isinstance(account, dict):
        number = str(account.get("account_number") or "")
    if not account_matches(str(getattr(settings, "options2_account_last4", "") or ""), number):
        return "OPTIONS2_ACCOUNT_MISMATCH"
    return None


def cached_account_block(settings) -> str:
    """Mismatch against the last snapshot on disk. Empty when no snapshot exists yet."""
    if str(getattr(settings, "desk_id", "") or "").strip() != "options2":
        return ""
    allowed, reason = trading_allowed(settings)
    if not allowed:
        return reason
    try:
        from app.broker.snapshot import peek

        account = peek("account")
    except Exception:
        return ""
    if not isinstance(account, dict) or not str(account.get("account_number") or "").strip():
        return ""
    if order_refusal(settings, account):
        return "OPTIONS2_ACCOUNT_MISMATCH"
    return ""


def public_status(settings) -> dict:
    """Safe to return from the API and to log. No key material and no full account number."""
    block = cached_account_block(settings) or None
    last4 = str(getattr(settings, "options2_account_last4", "") or "").strip().upper()
    if not _pin_ok(last4):
        last4 = ""
    return {
        "desk": "options2",
        "options2_enabled": bool(getattr(settings, "options2_enabled", False)),
        "options2_profile": "v2610_40k",
        "options2_scale": "0.40",
        "options2_capital_usd": "40000",
        "options2_source_commit": "b7f62cf",
        "options2_account_last4": last4,
        "options2_block": block,
    }
