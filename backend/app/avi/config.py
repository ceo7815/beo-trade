"""Environment for the Avi options engine. Prefix AVI_ only.

The options keys and the retired 100K keys are never read. A live trading
host is refused. The process does not trade until the paper keys and the
account pin are both set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.spy.config import PaperRefused, assert_data_url, assert_paper_url

SYMBOL = "QQQ"


class NotConfigured(RuntimeError):
    """Enabled, but the paper keys or the account pin are missing."""


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _pct(name: str, default: str) -> Decimal:
    """Fraction. 0.04 and 4 both mean 4 percent."""
    raw = os.environ.get(name, "").strip() or default
    try:
        value = Decimal(raw)
    except Exception:
        value = Decimal(default)
    if value > 1:
        value = value / Decimal(100)
    if value <= 0 or value > 1:
        value = Decimal(default)
    return value


def _count(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(hi, max(lo, value))


def _money(name: str) -> Decimal | None:
    """Unset means the engine uses session-start equity. A positive override wins."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except Exception:
        return None
    if value <= 0:
        return None
    return value


def _last4() -> str:
    """Suffix pin from AVI_ACCOUNT_LAST4. Four to eight letters or digits. Never hardcoded."""
    raw = os.environ.get("AVI_ACCOUNT_LAST4", "").strip().upper()
    if 4 <= len(raw) <= 8 and raw.isalnum():
        return raw
    return ""


def db_path() -> Path:
    override = os.environ.get("AVI_DB_PATH", "").strip()
    if override:
        return Path(override)
    docker = Path("/app/data")
    if docker.is_dir():
        return docker / "avi.sqlite"
    local = Path(__file__).resolve().parents[2] / "data"
    return local / "avi.sqlite"


def kill_flag_path() -> Path:
    override = os.environ.get("AVI_KILL_FLAG", "").strip()
    if override:
        return Path(override)
    return db_path().with_name("avi_kill.flag")


def heartbeat_path() -> Path:
    return db_path().with_name("avi_heartbeat.txt")


def status_path() -> Path:
    return db_path().with_name("avi_status.json")


@dataclass(frozen=True)
class AviConfig:
    enabled: bool
    key_id: str
    secret: str
    account_last4: str
    trading_url: str
    data_url: str
    risk_pct: Decimal
    bankroll: Decimal | None
    max_contracts: int
    max_debit_pct: Decimal
    db: Path

    def public(self) -> dict:
        """Safe to log and to return from the API. No key material."""
        return {
            "enabled": self.enabled,
            "account_last4": self.account_last4,
            "account_pinned": bool(self.account_last4),
            "key_set": bool(self.key_id),
            "secret_set": bool(self.secret),
            "bankroll_override": self.bankroll is not None,
            "risk_pct": str(self.risk_pct),
            "max_contracts": self.max_contracts,
            "max_debit_pct": str(self.max_debit_pct),
            "paper_only": True,
            "symbol": SYMBOL,
        }

    def __repr__(self) -> str:
        return f"AviConfig({self.public()})"


def load_config() -> AviConfig:
    return AviConfig(
        enabled=_flag("AVI_ENGINE_ENABLED", False),
        key_id=os.environ.get("AVI_ALPACA_API_KEY", "").strip(),
        secret=os.environ.get("AVI_ALPACA_SECRET_KEY", "").strip(),
        account_last4=_last4(),
        trading_url=os.environ.get("AVI_ALPACA_BASE_URL", "").strip(),
        data_url=os.environ.get("AVI_DATA_URL", "").strip(),
        risk_pct=_pct("AVI_RISK_PCT", "0.04"),
        bankroll=_money("AVI_BANKROLL"),
        max_contracts=_count("AVI_MAX_CONTRACTS", 10, 1, 50),
        max_debit_pct=_pct("AVI_MAX_DEBIT_PCT", "0.08"),
        db=db_path(),
    )


def startup_mode(cfg: AviConfig) -> str:
    """disabled | refused | not_configured | running.

    refused and not_configured never place an order. Missing keys or a missing
    account pin are not_configured. A non-paper trading host is refused.
    """
    if not cfg.enabled:
        return "disabled"
    try:
        assert_paper_url(cfg.trading_url)
        assert_data_url(cfg.data_url)
    except PaperRefused:
        return "refused"
    if not cfg.key_id or not cfg.secret or not cfg.account_last4:
        return "not_configured"
    return "running"


def redact(text: str, cfg: AviConfig | None) -> str:
    if not text:
        return ""
    cleaned = text
    if cfg is not None:
        for secret in (cfg.secret, cfg.key_id):
            if secret:
                cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned[:300]


def account_matches(last4: str, account_number: str) -> bool:
    """The account number must end with the configured suffix. The value comes from env."""
    pin = (last4 or "").strip().upper()
    number = (account_number or "").strip().upper()
    if not (4 <= len(pin) <= 8) or not pin.isalnum():
        return False
    return len(number) > len(pin) and number.endswith(pin)
