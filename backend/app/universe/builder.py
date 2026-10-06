from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import Settings, TradingConfig

CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "universe_cache.json"
_MEMORY: dict[str, "UniverseBook"] = {}
_SYMBOL = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")
FIXED_WATCHLIST = ("NVDA", "TSLA", "AAPL", "AMD", "AMZN", "META", "MSFT", "GOOGL", "NFLX", "AVGO", "COIN", "MSTR", "PLTR", "BA", "JPM")


class UniverseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class UniverseBook:
    symbols: tuple[str, ...]
    source: str
    status: str
    discovered: int
    filtered: int
    refreshed_at: datetime
    cursor: int
    detail: str
    liquidity: dict[str, int] | None = None


def reset_universe_state() -> None:
    _MEMORY.clear()


def _asset_class(row: dict) -> str:
    return str(row.get("class") or row.get("asset_class") or "").strip().lower()


def _attributes(row: dict) -> set[str]:
    raw = row.get("attributes") or []
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    return {str(item).strip().lower() for item in raw}


def filter_optionable_equities(rows: list[dict], exclusions: tuple[str, ...] = ()) -> tuple[tuple[str, ...], int]:
    """Keep active, tradable US equities that list options. Nothing else is added."""
    blocked = {item.strip().upper() for item in exclusions if item.strip()}
    kept: list[str] = []
    discovered = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        discovered += 1
        symbol = str(row.get("symbol") or "").strip().upper()
        if str(row.get("status") or "").strip().lower() != "active":
            continue
        if row.get("tradable") is not True:
            continue
        if _asset_class(row) != "us_equity":
            continue
        if "has_options" not in _attributes(row):
            continue
        if not _SYMBOL.match(symbol) or symbol in blocked:
            continue
        kept.append(symbol)
    return tuple(dict.fromkeys(kept)), discovered


def rank_symbols(symbols: tuple[str, ...], liquidity: dict[str, int] | None, liquid_floor: int = 1_000_000) -> tuple[str, ...]:
    """Names already known to trade at least the volume floor come first. Unknown names stay next, in their original order. Known thinner names come last."""
    scores = liquidity or {}
    liquid: list[tuple[int, str]] = []
    unknown: list[tuple[int, str]] = []
    thin: list[tuple[int, str]] = []
    for index, symbol in enumerate(symbols):
        score = scores.get(symbol)
        if score is None:
            unknown.append((index, symbol))
        elif int(score) >= liquid_floor:
            liquid.append((-int(score), symbol))
        else:
            thin.append((-int(score), symbol))
    liquid.sort()
    thin.sort()
    return tuple(symbol for _score, symbol in liquid) + tuple(symbol for _index, symbol in unknown) + tuple(symbol for _score, symbol in thin)


def take_batch(symbols: tuple[str, ...], max_symbols: int, cursor: int) -> tuple[tuple[str, ...], int]:
    if max_symbols < 1 or not symbols:
        return (), 0
    if len(symbols) <= max_symbols:
        return symbols, 0
    start = cursor % len(symbols)
    batch = tuple(symbols[(start + offset) % len(symbols)] for offset in range(max_symbols))
    return batch, (start + max_symbols) % len(symbols)


def _fresh(book: UniverseBook, now: datetime, refresh_seconds: int) -> bool:
    age = (now - book.refreshed_at).total_seconds()
    return 0 <= age <= refresh_seconds


def _read_cache(path: Path) -> UniverseBook | None:
    key = str(path)
    if key in _MEMORY:
        return _MEMORY[key]
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        refreshed = datetime.fromisoformat(raw["refreshed_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    book = UniverseBook(
        symbols=tuple(str(item).upper() for item in raw.get("symbols") or []),
        source=str(raw.get("source") or "alpaca-assets"),
        status=str(raw.get("status") or "READY"),
        discovered=int(raw.get("discovered") or 0),
        filtered=int(raw.get("filtered") or 0),
        refreshed_at=refreshed,
        cursor=int(raw.get("cursor") or 0),
        detail=str(raw.get("detail") or ""),
        liquidity={str(key).upper(): int(value) for key, value in (raw.get("liquidity") or {}).items() if int(value) > 0},
    )
    _MEMORY[key] = book
    return book


def _write_cache(path: Path, book: UniverseBook) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "symbols": list(book.symbols),
                "source": book.source,
                "status": book.status,
                "discovered": book.discovered,
                "filtered": book.filtered,
                "refreshed_at": book.refreshed_at.isoformat(),
                "cursor": book.cursor,
                "detail": book.detail,
                "liquidity": book.liquidity or {},
            }
        ),
        encoding="utf-8",
    )
    _MEMORY[str(path)] = book


def fetch_activity_scores(settings: Settings) -> dict[str, int]:
    """Read-only Alpaca most-actives list. A failure leaves the saved liquidity order unchanged."""
    from app.integrations.secrets import resolve_secret

    key, _origin = resolve_secret(settings, "alpaca_api_key")
    secret, _secret_origin = resolve_secret(settings, "alpaca_api_secret")
    if not key or not secret:
        return {}
    try:
        import httpx

        response = httpx.get(
            "https://data.alpaca.markets/v1beta1/screener/stocks/most-actives",
            params={"by": "volume", "top": 50},
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=8.0,
        )
        response.raise_for_status()
        body = response.json()
    except Exception:
        return {}
    rows = body.get("most_actives") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return {}
    scores = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        try:
            volume = int(row.get("volume") or 0)
        except (TypeError, ValueError):
            continue
        if symbol and volume > 0:
            scores[symbol] = volume
    return scores


def remember_liquidity(scores: dict[str, int], cache_path: Path | None = None) -> None:
    path = cache_path or CACHE_PATH
    book = _read_cache(path)
    if book is None:
        return
    merged = dict(book.liquidity or {})
    changed = False
    for symbol, volume in scores.items():
        name = str(symbol).strip().upper()
        try:
            amount = int(volume)
        except (TypeError, ValueError):
            continue
        if not name or amount <= 0 or merged.get(name) == amount:
            continue
        merged[name] = amount
        changed = True
    if changed:
        _write_cache(path, replace(book, liquidity=merged))


def fetch_assets(settings: Settings) -> list[dict]:
    from app.broker.runtime import open_paper_broker
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError

    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError) as exc:
        raise UniverseUnavailable("ספק היקום אינו זמין") from exc
    try:
        return adapter.list_optionable_equities()
    except Exception as exc:
        raise UniverseUnavailable("ספק היקום אינו זמין") from exc
    finally:
        adapter.close()


def load_universe(settings: Settings, config: TradingConfig, now: datetime, cache_path: Path | None = None) -> UniverseBook:
    path = cache_path or CACHE_PATH
    cached = _read_cache(path)
    if cached is not None and _fresh(cached, now, config.universe_refresh_seconds) and cached.symbols:
        visible = tuple(symbol for symbol in cached.symbols if symbol not in set(config.universe_exclusions))
        return replace(cached, symbols=visible, filtered=len(visible))
    try:
        rows = fetch_assets(settings)
    except UniverseUnavailable:
        if cached is not None and cached.symbols:
            stale = replace(
                cached,
                status="DEGRADED",
                detail=f"DEGRADED. יקום שמור מ-{cached.refreshed_at.isoformat()}. הרענון נכשל. זו אינה סריקת שוק מלאה.",
            )
            _MEMORY[str(path)] = stale
            return stale
        raise UniverseUnavailable("ספק היקום אינו זמין. אין יקום שמור, ואין רשימת גיבוי.") from None
    symbols, discovered = filter_optionable_equities(rows, config.universe_exclusions)
    if not symbols:
        raise UniverseUnavailable("ספק היקום החזיר אפס מניות עם אופציות.")
    book = UniverseBook(
        symbols=symbols,
        source="alpaca-assets",
        status="READY",
        discovered=discovered,
        filtered=len(symbols),
        refreshed_at=now,
        cursor=0 if cached is None else cached.cursor,
        detail="יקום דינמי מ-Alpaca Assets. המחיר והאופציות מגיעים מ-ThetaData.",
        liquidity={**({} if cached is None else dict(cached.liquidity or {})), **fetch_activity_scores(settings)},
    )
    _write_cache(path, book)
    return book


def next_scan_symbols(settings: Settings, config: TradingConfig, now: datetime, cache_path: Path | None = None) -> tuple[UniverseBook, tuple[str, ...]]:
    path = cache_path or CACHE_PATH
    book = load_universe(settings, config, now, path)
    if book.symbols == FIXED_WATCHLIST and book.source != "alpaca-assets":
        raise UniverseUnavailable("יקום קבוע אינו מותר.")
    blocked = {item.strip().upper() for item in config.universe_exclusions if item.strip()}
    core = tuple(dict.fromkeys(item.strip().upper() for item in config.core_symbols if item.strip() and item.strip().upper() not in blocked))
    pinned = set(core)
    ranked = rank_symbols(tuple(symbol for symbol in book.symbols if symbol not in pinned), book.liquidity, config.min_underlying_volume)
    room = max(0, config.universe_max_symbols_per_scan - len(core))
    batch, cursor = take_batch(ranked, room, book.cursor)
    stored = _read_cache(path)
    if stored is not None and room:
        _write_cache(path, replace(stored, cursor=cursor))
    return book, core + batch
