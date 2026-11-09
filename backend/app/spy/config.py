"""Environment for the SPY engine. Defaults match spec v5 and the owner's decisions.

Strategy numbers (14-day lookback, 2% vol target, decision clock) are not
read from the environment. Only infrastructure and the two explicit switches
(short, PDT guard, shadow list) are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


PAPER_HTTP = "https://paper-api.alpaca.markets"
DATA_HTTP = "https://data.alpaca.markets"
LIVE_HTTP = "https://api.alpaca.markets"
SYMBOL = "SPY"


class PaperRefused(RuntimeError):
    """Trading base is not the Alpaca paper host. No order is sent."""


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def assert_paper_url(url: str) -> str:
    """Accept only the paper trading host. Empty means the paper host."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return PAPER_HTTP
    host = _host(base)
    if host != "paper-api.alpaca.markets" or base.lower() != PAPER_HTTP:
        raise PaperRefused(host or "invalid")
    if LIVE_HTTP in base.lower() and "paper-api" not in base.lower():
        raise PaperRefused(host or "live")
    return PAPER_HTTP


def assert_data_url(url: str) -> str:
    """Market data host. This is not a trading host and cannot place orders."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return DATA_HTTP
    if _host(base) != "data.alpaca.markets" or base.lower() != DATA_HTTP:
        raise PaperRefused(_host(base) or "invalid-data")
    return DATA_HTTP


def _symbols(raw: str | None, default: str) -> tuple[str, ...]:
    text = default if raw is None else raw
    found: list[str] = []
    for part in text.split(","):
        symbol = part.strip().upper()
        if not symbol or not symbol.isalpha() or len(symbol) > 5 or symbol in found:
            continue
        found.append(symbol)
    return tuple(found)


def trade_symbols(raw: str | None) -> tuple[str, ...]:
    parsed = _symbols(raw, "SPY,QQQ,IWM")
    return parsed or ("SPY",)


def shadow_symbols(raw: str | None, traded: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Symbols that are logged and never ordered. Default is empty. Traded names are skipped."""
    blocked = set(traded)
    found: list[str] = []
    for symbol in _symbols(raw, ""):
        if symbol in blocked or symbol in found:
            continue
        found.append(symbol)
    return tuple(found)


def _count(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(hi, max(lo, value))


def db_path() -> Path:
    override = os.environ.get("SPY_DB_PATH", "").strip()
    if override:
        return Path(override)
    docker = Path("/app/data")
    if docker.is_dir():
        return docker / "spy.sqlite"
    local = Path(__file__).resolve().parents[2] / "data"
    return local / "spy.sqlite"


def kill_flag_path() -> Path:
    override = os.environ.get("SPY_KILL_FLAG", "").strip()
    if override:
        return Path(override)
    return db_path().with_name("spy_kill.flag")


def resume_flag_path() -> Path:
    return db_path().with_name("spy_resume.flag")


def heartbeat_path() -> Path:
    return db_path().with_name("spy_heartbeat.txt")


def status_path() -> Path:
    return db_path().with_name("spy_status.json")


@dataclass(frozen=True)
class SpyConfig:
    enabled: bool
    key_id: str
    secret: str
    account: str
    trading_url: str
    data_url: str
    pdt_guard: bool
    allow_short: bool
    shadow: tuple[str, ...]
    db: Path
    symbols: tuple[str, ...] = ("SPY",)
    max_positions: int = 1
    max_entries_day: int = 4
    per_symbol_entries: int = 4

    def public(self) -> dict:
        """Safe to log and to return from the API. No key material."""
        return {
            "enabled": self.enabled,
            "account_last4": self.account[-4:] if self.account else "",
            "account_pinned": bool(self.account),
            "key_set": bool(self.key_id),
            "secret_set": bool(self.secret),
            "pdt_guard": self.pdt_guard,
            "allow_short": self.allow_short,
            "symbols": list(self.symbols),
            "max_positions": self.max_positions,
            "max_entries_day": self.max_entries_day,
            "shadow": list(self.shadow),
            "paper_only": True,
        }

    def __repr__(self) -> str:
        shown = self.public()
        return f"SpyConfig({shown})"


def load_config() -> SpyConfig:
    return SpyConfig(
        enabled=_flag("SPY_ENGINE_ENABLED", False),
        key_id=os.environ.get("SPY_ALPACA_KEY_ID", "").strip(),
        secret=os.environ.get("SPY_ALPACA_SECRET_KEY", "").strip(),
        account=os.environ.get("SPY_ALPACA_ACCOUNT", "").strip(),
        trading_url=os.environ.get("SPY_ALPACA_BASE_URL", "").strip(),
        data_url=os.environ.get("SPY_DATA_URL", "").strip(),
        pdt_guard=_flag("SPY_PDT_GUARD", True),
        allow_short=_flag("SPY_ALLOW_SHORT", False),
        symbols=trade_symbols(os.environ.get("SPY_TRADE_SYMBOLS")),
        max_positions=_count("SPY_MAX_POSITIONS", 3, 1, 3),
        max_entries_day=_count("SPY_MAX_ENTRIES_DAY", 6, 1, 12),
        per_symbol_entries=2,
        shadow=shadow_symbols(os.environ.get("SPY_SHADOW_SYMBOLS"), trade_symbols(os.environ.get("SPY_TRADE_SYMBOLS"))),
        db=db_path(),
    )


def startup_mode(cfg: SpyConfig) -> str:
    """disabled | refused | not_configured | running. refused never places an order."""
    if not cfg.enabled:
        return "disabled"
    try:
        assert_paper_url(cfg.trading_url)
        assert_data_url(cfg.data_url)
    except PaperRefused:
        return "refused"
    if not cfg.key_id or not cfg.secret:
        return "not_configured"
    return "running"


def redact(text: str, cfg: SpyConfig | None) -> str:
    if not text:
        return ""
    cleaned = text
    if cfg is not None:
        for secret in (cfg.secret, cfg.key_id):
            if secret:
                cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned[:300]
