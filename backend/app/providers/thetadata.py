from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
import structlog

from app.config.settings import Settings
from app.core.sessions import SCAN_PHASES, phase_at
from app.integrations.secrets import resolve_secret
from app.providers.base import ProviderUnavailable
from app.quant.relative_volume import LAST_BAR_OPEN, SESSION_OPEN, time_of_day_relative_volume
from app.schemas.domain import Bar, OptionRight, OptionSnapshot, UnderlyingSnapshot

log = structlog.get_logger()

DEFAULT_BASE = "http://127.0.0.1:25503"
EXCHANGE = ZoneInfo("America/New_York")
LATE_QUOTE_SECONDS = 90
REQUEST_TIMEOUT_SECONDS = 10
# A terminal that stops answering would otherwise hold one scan for every optional request's timeout.
MAX_TRANSPORT_FAILURES = 3
# Option paths this desk requests. Theta Terminal, not this process, decides which of them
# need a professional subscription. A STANDARD login answers PERMISSION_DENIED
# ("Requesting an option endpoint requiring a professional subscription, but you only have a STANDARD subscription")
# for a path above that tier. Greeks (`/v3/option/snapshot/greeks/all`) are the professional surface.
# A denial on greeks, open interest, history, and other enrichment is remembered for that path
# until the next New York trading day, and later calls return [] without another request.
# A denial on a live quote or OHLC — the paths a stop, a trail, and a thesis exit read — is
# logged once and remembered for at most five minutes, then tried again. It must not go dark
# for the rest of the session.
# greeks denied → the contract is still built from the quote, and IV/greeks are solved from the mid;
# open interest denied → open_interest stays 0, as it does for an empty body.
QUOTE_DENIAL_SECONDS = 300
_PERMISSION_DENIED: dict[str, datetime] = {}


class ThetaDataError(ProviderUnavailable):
    pass


@dataclass
class _State:
    authenticated: bool | None = None
    last_success: str | None = None
    last_fetch: str | None = None
    quote_count: int = 0
    option_count: int = 0
    newest_quote: str | None = None
    last_error: str | None = None
    context: dict = field(default_factory=dict)


_state = _State()
_bundle: tuple[datetime, dict] | None = None
# Prior-session bars do not change during a session. Keyed by (kind, symbol, session day).
_prior: dict[tuple[str, str, date], list[dict]] = {}
# A 400 or 5xx is logged once per path and symbol list. A single-symbol failure is remembered so history is not requested again this session.
_LOGGED_SKIP: set[tuple[str, tuple[str, ...]]] = set()
_SKIPPED: dict[tuple[str, str], int] = {}
_REQUEST_KEYS = ("start_date", "end_date", "interval", "start_time", "end_time", "expiration", "strike", "right", "max_dte", "strike_range")
# Corrupt per-symbol history comes back as 400 or 5xx. These are not a dead terminal.
_SYMBOL_FAILURES = frozenset({400, 500, 502, 503, 504})
_CORE_SNAPSHOTS = frozenset({
    "/v3/stock/snapshot/ohlc",
    "/v3/stock/snapshot/quote",
    "/v3/stock/snapshot/trade",
})
# A month of minute bars per symbol is heavy; rotating symbols beyond this many are refetched.
MAX_ROTATING_PRIOR = 40
# Minute bars not fetched within this budget are fetched in the background for the next scan.
INTRADAY_BUDGET_SECONDS = 45
_warm_lock = threading.Lock()
_warm_thread: threading.Thread | None = None


def reset_theta_state() -> None:
    global _state, _bundle
    _state = _State()
    _bundle = None
    _prior.clear()
    _PERMISSION_DENIED.clear()
    _LOGGED_SKIP.clear()
    _SKIPPED.clear()


def feed_status() -> dict:
    return {
        "provider": "thetadata",
        "authenticated": _state.authenticated,
        "last_success": _state.last_success,
        "last_fetch": _state.last_fetch,
        "quote_count": _state.quote_count,
        "option_count": _state.option_count,
        "newest_quote": _state.newest_quote,
        "last_error": _state.last_error,
    }


def _iso(moment: datetime) -> str:
    return moment.astimezone(EXCHANGE).isoformat()


def _flatten(rows: list[dict]) -> list[dict]:
    """v3 JSON nests option rows as {"contract": {...}, "data": [{...}]}."""
    flat: list[dict] = []
    for row in rows:
        contract = row.get("contract")
        data = row.get("data")
        if isinstance(contract, dict) and isinstance(data, list):
            for tick in data:
                if isinstance(tick, dict):
                    flat.append({**contract, **tick})
            continue
        flat.append(row)
    return flat


def _rows(payload: object) -> list[dict]:
    return _flatten(_raw_rows(payload))


def _raw_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    columns: list[str] = []
    header = payload.get("header")
    if isinstance(header, dict) and isinstance(header.get("format"), list):
        columns = [str(item) for item in header["format"]]
    for key in ("response", "data"):
        raw = payload.get(key)
        if not isinstance(raw, list) or not raw:
            continue
        if isinstance(raw[0], dict):
            return [row for row in raw if isinstance(row, dict)]
        if columns and isinstance(raw[0], list):
            return [
                dict(zip(columns, row))
                for row in raw
                if isinstance(row, list)
            ]
    if "symbol" in payload or "bid" in payload:
        return [payload]
    return []


def _parse_day(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EXCHANGE)
    return parsed


def _at_scan(stamp: datetime | None, as_of: datetime) -> datetime | None:
    """Snapshots are read after the scan clock starts, so a liquid name's last quote lands just after it."""
    if stamp is None or stamp <= as_of:
        return stamp
    if (stamp - as_of).total_seconds() <= LATE_QUOTE_SECONDS:
        return as_of.astimezone(EXCHANGE)
    return None


def _dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _right(value: object) -> OptionRight | None:
    text = str(value or "").strip().upper()
    if text in {"C", "CALL"}:
        return OptionRight.CALL
    if text in {"P", "PUT"}:
        return OptionRight.PUT
    return None


def _contract_key(row: dict) -> tuple[str, str, str] | None:
    expiration = _parse_day(row.get("expiration"))
    strike = _dec(row.get("strike"))
    right = _right(row.get("right"))
    if expiration is None or strike is None or right is None:
        return None
    return (expiration.isoformat(), format(strike, "f"), right.value)


def _occ(symbol: str, expiration: date, right: OptionRight, strike: Decimal) -> str:
    root = symbol.upper().ljust(6)
    mills = int((strike * Decimal("1000")).quantize(Decimal("1")))
    side = "C" if right is OptionRight.CALL else "P"
    return f"{root}{expiration.strftime('%y%m%d')}{side}{mills:08d}"


def _by_symbol(rows: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            found[symbol] = row
    return found


def _index_contracts(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    found: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = _contract_key(row)
        if key is not None:
            found[key] = row
    return found


def _exchange_now() -> datetime:
    return datetime.now(EXCHANGE)


def _exit_quote_or_ohlc(path: str) -> bool:
    """Live quote and OHLC. History quotes and history bars are enrichment, not exits."""
    if "/history/" in path:
        return False
    return path.rstrip("/").rsplit("/", 1)[-1] in {"quote", "ohlc"}


def _next_trading_day(day: date) -> date:
    from app.config.settings import load_calendar

    holidays = load_calendar(None).holidays
    cursor = day + timedelta(days=1)
    while cursor.weekday() >= 5 or cursor in holidays:
        cursor += timedelta(days=1)
    return cursor


def _denial_until(path: str, now: datetime) -> datetime:
    local = now.astimezone(EXCHANGE)
    if _exit_quote_or_ohlc(path):
        return local + timedelta(seconds=QUOTE_DENIAL_SECONDS)
    nxt = _next_trading_day(local.date())
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=EXCHANGE)


def _denial_active(path: str, now: datetime) -> bool:
    until = _PERMISSION_DENIED.get(path)
    return until is not None and now.astimezone(EXCHANGE) < until


def _permission_denied(response: httpx.Response) -> bool:
    """Theta Terminal's STANDARD-tier refusal for a professional option path."""
    try:
        text = response.text[:800]
    except Exception:
        text = ""
    lowered = text.lower()
    if "permission_denied" in lowered or "professional subscription" in lowered:
        return True
    try:
        payload = response.json()
    except Exception:
        return False
    header = payload.get("header") if isinstance(payload, dict) else None
    if not isinstance(header, dict):
        return False
    kind = str(header.get("error_type") or "").upper()
    message = str(header.get("error_msg") or header.get("message") or "")
    return kind == "PERMISSION_DENIED" or "professional subscription" in message.lower()


def _response_excerpt(response: httpx.Response, limit: int = 300) -> str:
    try:
        text = response.text
    except Exception:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit]


def _log_symbol_failure_once(path: str, symbols: list[str], params: dict, body: str, status_code: int) -> None:
    """One line per path and symbol list. The next cycle must not repeat it."""
    key = (path, tuple(symbols))
    if key in _LOGGED_SKIP:
        return
    _LOGGED_SKIP.add(key)
    logged = {name: params[name] for name in _REQUEST_KEYS if params.get(name) not in (None, "")}
    log.warning(
        "thetadata.bad_request",
        upstream="thetadata",
        path=path,
        symbols=symbols,
        params=logged,
        status_code=status_code,
        body=body,
    )


def _fallback_prior(key: tuple[str, str, date]) -> list[dict]:
    """Rows already stored for this symbol, from an earlier session day."""
    kind, symbol, day = key
    found = [item for item in _prior if item[0] == kind and item[1] == symbol and item[2] != day and _prior[item]]
    if not found:
        return []
    return list(_prior[max(found, key=lambda item: item[2])])


def _remember_permission_denial(path: str, status_code: int, now: datetime | None = None) -> None:
    moment = now or _exchange_now()
    if _denial_active(path, moment):
        return
    _PERMISSION_DENIED[path] = _denial_until(path, moment)
    log.warning(
        "thetadata.permission_denied",
        upstream="thetadata",
        path=path,
        status_code=status_code,
        error="PERMISSION_DENIED",
    )


class ThetaFeed:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self._symbols: tuple[str, ...] = ()
        self._option_symbols: tuple[str, ...] = ()
        self.last_option_status: str | None = None
        self._listed_expirations: dict[str, list[str]] = {}
        self._transport_failures = 0
        self.request_stats: dict[str, dict] = {}
        self.on_request = None
        self._reported = 0.0
        base, _origin = resolve_secret(settings, "thetadata_base_url")
        self.base = (base or DEFAULT_BASE).rstrip("/")

    def bind_symbols(self, symbols: tuple[str, ...]) -> None:
        global _bundle
        self._symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()))
        self._option_symbols = self._symbols
        _bundle = None

    def bind_option_symbols(self, symbols: tuple[str, ...]) -> None:
        self._option_symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()))

    def context_quotes(self) -> dict:
        return dict(_state.context)

    def load(self, as_of: datetime) -> dict:
        global _bundle
        if _bundle is not None and _bundle[0] == as_of:
            return _bundle[1]
        bundle = self._fetch(as_of)
        _bundle = (as_of, bundle)
        return bundle

    def _warm_in_background(self, pending: list[tuple[tuple[str, str, date], dict]]) -> None:
        """One worker at a time, on its own feed, so the scan's request stats and budget are untouched."""
        global _warm_thread
        with _warm_lock:
            if _warm_thread is not None and _warm_thread.is_alive():
                return
            warmer = type(self)(self.settings, self.transport)

            def run() -> None:
                for key, params in pending:
                    if key in _prior:
                        continue
                    try:
                        warmer._prior_rows(key, "/v3/stock/history/ohlc", params, required=False)
                    except ProviderUnavailable:
                        return

            _warm_thread = threading.Thread(target=run, name="theta-warm", daemon=True)
            _warm_thread.start()

    def _remember_prior(self, key: tuple[str, str, date], rows: list[dict]) -> list[dict]:
        """Keep this session's rows. Another symbol's refresh does not drop this symbol's older bars."""
        stored = list(rows)
        if stored and key[0] == "1m":
            stored = [
                {"timestamp": row.get("timestamp") or row.get("created") or row.get("last_trade"), "volume": row.get("volume")}
                for row in stored
            ]
        for stale in [item for item in list(_prior) if item[0] == key[0] and item[1] == key[1] and item[2] != key[2]]:
            _prior.pop(stale, None)
        if stored:
            pinned = {item.strip().upper() for item in self.settings.trading().core_symbols}
            if key[1] not in pinned:
                rotating = [item for item in _prior if item[0] == key[0] and item[1] not in pinned]
                for oldest in rotating[: max(0, len(rotating) - MAX_ROTATING_PRIOR + 1)]:
                    _prior.pop(oldest, None)
        _prior[key] = stored
        return stored

    def _prior_rows(self, key: tuple[str, str, date], path: str, params: dict, required: bool) -> list[dict]:
        cached = _prior.get(key)
        if cached is not None:
            return cached
        rows = self._get(path, params, required=required)
        if not rows and (path, key[1]) in _SKIPPED:
            # A skipped symbol was never stored, so the next cycle asked for the same corrupt history again.
            return self._remember_prior(key, _fallback_prior(key))
        if rows:
            return self._remember_prior(key, rows)
        return rows

    def _fail_core_snapshot(self, code: int) -> None:
        """Every name failed a live stock snapshot. The terminal is down, not one corrupt file."""
        self._fail(f"ThetaData החזיר {code}")
        raise ThetaDataError(f"ThetaData החזיר {code}")

    def _get(self, path: str, params: dict, required: bool = True, _attempt: int = 0, _split: bool = False) -> list[dict]:
        self._last_skip_code = None
        now = _exchange_now()
        if _denial_active(path, now):
            return []
        started = time.monotonic()
        stats = self.request_stats.setdefault(path, {"count": 0, "seconds": 0.0, "slowest": 0.0})
        try:
            with httpx.Client(transport=self.transport, timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = client.get(self.base + path, params={"format": "json", **params})
        except httpx.HTTPError as exc:
            self._count(stats, started)
            self._transport_failures += 1
            if self._transport_failures >= MAX_TRANSPORT_FAILURES:
                self._fail("ThetaData לא מגיב")
                raise ThetaDataError("ThetaData לא מגיב") from exc
            if not required:
                return []
            self._fail("ThetaData לא זמין")
            raise ThetaDataError("ThetaData לא זמין") from exc
        self._count(stats, started)
        code = response.status_code
        if _permission_denied(response):
            _remember_permission_denial(path, code, now)
            return []
        symbols = [part.strip() for part in str(params.get("symbol") or "").split(",") if part.strip()]
        if code in _SYMBOL_FAILURES:
            # One bad name (corrupt history, or an index on a stock path) must not abort the scan.
            # A multi-symbol 400 or 5xx is split the same way as 472: keep the names that answer, skip the one that does not.
            _log_symbol_failure_once(path, symbols, params, _response_excerpt(response), code)
            if len(symbols) > 1:
                rows: list[dict] = []
                server_failures = 0
                for symbol in symbols:
                    one = dict(params)
                    one["symbol"] = symbol
                    got = self._get(path, one, required=required, _split=True)
                    rows.extend(got)
                    if not got and self._last_skip_code in _SYMBOL_FAILURES and self._last_skip_code != 400:
                        server_failures += 1
                if server_failures == len(symbols) and not rows and required and path in _CORE_SNAPSHOTS:
                    self._fail_core_snapshot(code)
                return rows
            if symbols:
                self._last_skip_code = code
                _SKIPPED[(path, symbols[0].upper())] = code
                if not _split and required and code != 400 and path in _CORE_SNAPSHOTS:
                    self._fail_core_snapshot(code)
                return []
            if not required:
                return []
        if code != 200 and code not in _SYMBOL_FAILURES:
            log.warning(
                "thetadata.http",
                upstream="thetadata",
                status_code=code,
                method="GET",
                path=path,
                host=urlsplit(self.base).hostname,
            )
        if code in {403, 429} and _attempt < 1:
            return self._get(path, params, required=required, _attempt=_attempt + 1)
        if code in {403, 429, 472} and len(symbols) > 1:
            rows = []
            for symbol in symbols:
                one = dict(params)
                one["symbol"] = symbol
                rows.extend(self._get(path, one, required=required))
            return rows
        if code in {403, 429, 472}:
            return []
        if code != 200:
            if not required:
                return []
            self._fail(f"ThetaData החזיר {response.status_code}")
            raise ThetaDataError(f"ThetaData החזיר {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            if path.startswith("/v3/option/snapshot/quote"):
                self.last_option_status = f"{code}:bad-json"
            if not required:
                return []
            self._fail("ThetaData החזיר תשובה לא תקינה")
            raise ThetaDataError("ThetaData החזיר תשובה לא תקינה") from exc
        rows = _rows(payload)
        if path.startswith("/v3/option/snapshot/quote"):
            self.last_option_status = f"{code}:{len(rows)}"
        return rows

    def _count(self, stats: dict, started: float) -> None:
        spent = time.monotonic() - started
        stats["count"] += 1
        stats["seconds"] = round(stats["seconds"] + spent, 2)
        stats["slowest"] = round(max(stats["slowest"], spent), 2)
        stats["last_at"] = datetime.now(EXCHANGE).strftime("%H:%M:%S")
        if self.on_request is not None and time.monotonic() - self._reported >= 5:
            self._reported = time.monotonic()
            try:
                self.on_request(self.request_stats)
            except Exception:
                pass

    def _fail(self, message: str) -> None:
        _state.last_fetch = datetime.now(EXCHANGE).isoformat()
        _state.last_error = message
        _state.authenticated = False

    def _fetch(self, as_of: datetime) -> dict:
        config = self.settings.trading()
        universe = self._symbols
        if not universe:
            self._fail("אין יקום סריקה")
            raise ProviderUnavailable("אין יקום סריקה")
        context_symbols = tuple(dict.fromkeys(symbol.strip().upper() for symbol in config.context_symbols if symbol.strip()))
        stocks = tuple(dict.fromkeys((*universe, *context_symbols)))
        listed = ",".join(stocks)
        session_day = as_of.astimezone(EXCHANGE).date()
        self.request_stats = {}
        ohlc = _by_symbol(self._get("/v3/stock/snapshot/ohlc", {"symbol": listed}))
        quotes = _by_symbol(self._get("/v3/stock/snapshot/quote", {"symbol": listed}))
        trades = _by_symbol(self._get("/v3/stock/snapshot/trade", {"symbol": listed}))
        history: dict[str, list[dict]] = {}
        start = (session_day - timedelta(days=30)).strftime("%Y%m%d")
        end = (session_day - timedelta(days=1)).strftime("%Y%m%d")
        for symbol in stocks:
            if symbol not in ohlc and symbol not in trades:
                continue
            history[symbol] = self._prior_rows(
                ("eod", symbol, session_day),
                "/v3/stock/history/eod",
                {"symbol": symbol, "start_date": start, "end_date": end},
                required=True,
            )
        intraday: dict[str, list[dict]] = {}
        cutoff = _completed_minute(as_of)
        if cutoff is not None:
            today = session_day.strftime("%Y%m%d")
            end_clock = cutoff.strftime("%H:%M:%S")
            deadline = time.monotonic() + INTRADAY_BUDGET_SECONDS
            skipped: list[tuple[tuple[str, str, date], dict]] = []
            for symbol in stocks:
                if symbol not in ohlc and symbol not in trades:
                    continue
                key = ("1m", symbol, session_day)
                params = {"symbol": symbol, "start_date": start, "end_date": end, "interval": "1m", "start_time": "09:30:00", "end_time": "16:00:00"}
                if time.monotonic() > deadline and key not in _prior:
                    skipped.append((key, params))
                    continue
                earlier = self._prior_rows(key, "/v3/stock/history/ohlc", params, required=False)
                start_clock = "09:30:00"
                if end_clock > start_clock:
                    current = self._get(
                        "/v3/stock/history/ohlc",
                        {
                            "symbol": symbol,
                            "start_date": today,
                            "end_date": today,
                            "interval": "1m",
                            "start_time": start_clock,
                            "end_time": end_clock,
                        },
                        required=False,
                    )
                else:
                    current = []
                intraday[symbol] = [*_on_days(earlier, session_day, before=True), *_on_days(current, session_day, before=False)]
            self.request_stats["intraday_skipped"] = {"count": len(skipped)}
            if skipped:
                self._warm_in_background(skipped)
        underlyings = []
        context: dict[str, dict] = {}
        newest: datetime | None = None
        session_ok = phase_at(as_of, self.settings.calendar()) in SCAN_PHASES
        for symbol in stocks:
            level = _context_level(symbol, ohlc.get(symbol), quotes.get(symbol), trades.get(symbol), history.get(symbol, []), as_of, session_day)
            if level is not None:
                context[symbol] = level
                newest = level["observed_at"] if newest is None else max(newest, level["observed_at"])
            if symbol not in universe:
                continue
            built = _underlying(
                symbol,
                ohlc.get(symbol),
                quotes.get(symbol),
                trades.get(symbol),
                history.get(symbol, []),
                intraday.get(symbol, []),
                as_of,
                session_day,
                session_ok,
                self.settings.calendar().holidays,
            )
            if built is not None:
                underlyings.append(built)
        for symbol in context_symbols:
            if symbol in context:
                continue
            level = _index_level(self._get("/v3/index/snapshot/price", {"symbol": symbol}, required=False), as_of, symbol)
            if level is None:
                continue
            context[symbol] = level
            newest = level["observed_at"] if newest is None else max(newest, level["observed_at"])
        now = datetime.now(EXCHANGE).isoformat()
        ready = bool(underlyings)
        _state.last_fetch = now
        _state.quote_count = len(underlyings)
        _state.option_count = 0
        _state.newest_quote = None if newest is None else _iso(newest)
        _state.context = context
        _state.authenticated = False
        _state.last_success = None
        _state.last_error = None if ready else "אין ציטוט"
        return {"underlyings": underlyings, "options": [], "context": context}

    def load_options(self, as_of: datetime) -> list:
        symbols = self._option_symbols
        if not symbols:
            _state.option_count = 0
            return []
        config = self.settings.trading()
        options = self._options(symbols, as_of, config.max_expiration_days, config.scan_strike_range)
        now = datetime.now(EXCHANGE).isoformat()
        _state.option_count = len(options)
        _state.last_fetch = now
        if options and _state.quote_count:
            _state.authenticated = True
            _state.last_success = now
            _state.last_error = None
        elif not options:
            _state.last_error = _state.last_error or "אין שרשרת אופציות"
        return options

    def load_contract_options(self, as_of: datetime, contracts: list[tuple]) -> list[OptionSnapshot]:
        """Live snapshots for named contracts only. Submit revalidation uses this instead of full chains."""
        found: list[OptionSnapshot] = []
        for underlying, expiration, strike, right in contracts:
            symbol = str(underlying or "").strip().upper()
            if not symbol or expiration is None or strike is None or right is None:
                continue
            params = {
                "symbol": symbol,
                "expiration": expiration.isoformat(),
                "strike": format(strike, "f"),
                "right": str(getattr(right, "value", right)).lower(),
            }
            quote_rows = self._get("/v3/option/snapshot/quote", params, required=False)
            if not quote_rows:
                continue
            quote = quote_rows[0]
            ohlc_rows = self._get("/v3/option/snapshot/ohlc", params, required=False)
            interest_rows = self._get("/v3/option/snapshot/open_interest", params, required=False)
            greek_rows = self._get("/v3/option/snapshot/greeks/all", params, required=False)
            item = _option(
                symbol,
                quote,
                ohlc_rows[0] if ohlc_rows else None,
                interest_rows[0] if interest_rows else None,
                greek_rows[0] if greek_rows else None,
                as_of,
            )
            if item is not None:
                found.append(item)
        return found

    def _near_expirations(self, symbol: str, max_dte: int, as_of: datetime) -> list[str]:
        cached = self._listed_expirations.get(symbol)
        if cached is not None:
            return cached
        session_day = as_of.astimezone(EXCHANGE).date()
        listed = self._get("/v3/option/list/expirations", {"symbol": symbol}, required=False)
        chosen: list[str] = []
        for row in listed:
            day = _parse_day(row.get("expiration"))
            if day is None:
                continue
            ahead = (day - session_day).days
            if 0 <= ahead <= max_dte:
                chosen.append(day.isoformat())
        if not chosen:
            future = []
            for row in listed:
                day = _parse_day(row.get("expiration"))
                if day is not None and day >= session_day:
                    future.append(day.isoformat())
            chosen = future[:2]
        self._listed_expirations[symbol] = chosen[:3]
        return self._listed_expirations[symbol]

    def _option_rows(self, path: str, symbol: str, max_dte: int, strike_range: int, as_of: datetime) -> list[dict]:
        narrow = {
            "symbol": symbol,
            "expiration": "*",
            "strike": "*",
            "right": "both",
            "max_dte": max_dte,
            "strike_range": strike_range,
        }
        rows = self._get(path, narrow, required=False)
        if not rows:
            rows = self._get(
                path,
                {"symbol": symbol, "expiration": "*", "strike": "*", "right": "both"},
                required=False,
            )
        if rows:
            return rows
        found: list[dict] = []
        for expiration in self._near_expirations(symbol, max_dte, as_of):
            dated = self._get(
                path,
                {"symbol": symbol, "expiration": expiration, "strike": "*"},
                required=False,
            )
            for row in dated:
                if not row.get("expiration"):
                    row["expiration"] = expiration
            found.extend(dated)
        return found

    def _options(self, symbols: tuple[str, ...], as_of: datetime, max_dte: int, strike_range: int) -> list[OptionSnapshot]:
        contracts: list[OptionSnapshot] = []
        for symbol in symbols:
            quotes = _index_contracts(self._option_rows("/v3/option/snapshot/quote", symbol, max_dte, strike_range, as_of))
            ohlc = _index_contracts(self._option_rows("/v3/option/snapshot/ohlc", symbol, max_dte, strike_range, as_of))
            interest = _index_contracts(self._option_rows("/v3/option/snapshot/open_interest", symbol, max_dte, strike_range, as_of))
            greeks = _index_contracts(self._option_rows("/v3/option/snapshot/greeks/all", symbol, max_dte, strike_range, as_of))
            for key, quote in quotes.items():
                item = _option(symbol, quote, ohlc.get(key), interest.get(key), greeks.get(key), as_of)
                if item is not None:
                    contracts.append(item)
        return contracts


def _history_before(rows: list[dict], session_day: date) -> list[dict]:
    kept = []
    for row in rows:
        stamp = _stamp(row.get("last_trade") or row.get("created") or row.get("timestamp"))
        if stamp is None or stamp.astimezone(EXCHANGE).date() >= session_day:
            continue
        if _dec(row.get("close")) is None:
            continue
        kept.append(row)
    kept.sort(key=lambda row: _stamp(row.get("last_trade") or row.get("created") or row.get("timestamp")) or datetime.min.replace(tzinfo=EXCHANGE))
    return kept


def _context_level(symbol: str, ohlc: dict | None, quote: dict | None, trade: dict | None, history: list[dict], as_of: datetime, session_day: date) -> dict | None:
    observed = _at_scan(_stamp((quote or {}).get("timestamp")) or _stamp((trade or {}).get("timestamp")) or _stamp((ohlc or {}).get("timestamp")), as_of)
    price = _dec((trade or {}).get("price")) or _dec((ohlc or {}).get("close"))
    if observed is None or price is None:
        return None
    change = None
    prior_rows = _history_before(history, session_day)
    if prior_rows:
        prior_close = _dec(prior_rows[-1].get("close"))
        if prior_close is not None and prior_close > 0:
            change = ((price - prior_close) / prior_close) * Decimal("100")
    return {"symbol": symbol, "price": price, "change_percent": change, "observed_at": observed}


def _completed_minute(as_of: datetime) -> datetime | None:
    local = as_of.astimezone(EXCHANGE)
    cutoff = local.replace(second=0, microsecond=0) - timedelta(minutes=1)
    if cutoff.date() != local.date() or cutoff.time() < SESSION_OPEN:
        return None
    if cutoff.time() > LAST_BAR_OPEN:
        return cutoff.replace(hour=LAST_BAR_OPEN.hour, minute=LAST_BAR_OPEN.minute, second=0, microsecond=0)
    return cutoff


def _on_days(rows: list[dict], session_day: date, before: bool) -> list[dict]:
    kept = []
    for row in rows:
        stamp = _stamp(row.get("timestamp") or row.get("created") or row.get("last_trade"))
        if stamp is None:
            continue
        day = stamp.astimezone(EXCHANGE).date()
        if (day < session_day) if before else (day == session_day):
            kept.append(row)
    return kept


def _minute_bars(rows: list[dict]) -> list[tuple[datetime, int]]:
    bars = []
    for row in rows:
        stamp = _stamp(row.get("timestamp") or row.get("created") or row.get("last_trade"))
        volume = _int(row.get("volume"))
        if stamp is None or volume is None:
            continue
        bars.append((stamp, volume))
    return bars


def _underlying(
    symbol: str,
    ohlc: dict | None,
    quote: dict | None,
    trade: dict | None,
    history: list[dict],
    intraday: list[dict],
    as_of: datetime,
    session_day: date,
    session_ok: bool,
    holidays: frozenset[date] | set[date],
) -> UnderlyingSnapshot | None:
    observed = _at_scan(_stamp((quote or {}).get("timestamp")) or _stamp((trade or {}).get("timestamp")) or _stamp((ohlc or {}).get("timestamp")), as_of)
    price = _dec((trade or {}).get("price")) or _dec((ohlc or {}).get("close"))
    if observed is None or price is None or ohlc is None:
        return None
    prior_rows = _history_before(history, session_day)
    if not prior_rows:
        return None
    prior = prior_rows[-1]
    prior_close = _dec(prior.get("close"))
    open_price = _dec(ohlc.get("open"))
    high = _dec(ohlc.get("high"))
    low = _dec(ohlc.get("low"))
    close = _dec(ohlc.get("close"))
    volumes = [Decimal(value) for value in (_int(row.get("volume")) or 0 for row in prior_rows[-20:]) if value and value > 0]
    today_volume = _int(ohlc.get("volume"))
    if None in (prior_close, open_price, high, low, close) or prior_close <= 0 or today_volume is None or not volumes:
        return None
    average = sum(volumes, Decimal("0")) / Decimal(len(volumes))
    if average <= 0:
        return None
    audit = time_of_day_relative_volume(_minute_bars(intraday), as_of, session_day, holidays)
    if audit is None:
        relative = Decimal("0")
        rv_numerator = 0
        rv_denominator = Decimal("0")
        rv_day_count = 0
        rv_cutoff = None
        rv_prior_days: tuple[str, ...] = ()
        rv_prior_totals: tuple[int, ...] = ()
        rv_method = "unavailable"
    else:
        relative = audit["numerator"] / audit["denominator"]
        rv_numerator = int(audit["numerator"])
        rv_denominator = audit["denominator"]
        rv_day_count = int(audit["day_count"])
        rv_cutoff = audit["cutoff"]
        rv_prior_days = tuple(audit["prior_days"])
        rv_prior_totals = tuple(int(item) for item in audit["prior_totals"])
        rv_method = str(audit["method"])
    bars: list[Bar] = []
    for row in prior_rows:
        stamp = _stamp(row.get("last_trade") or row.get("created") or row.get("timestamp"))
        bar_open = _dec(row.get("open"))
        bar_high = _dec(row.get("high"))
        bar_low = _dec(row.get("low"))
        bar_close = _dec(row.get("close"))
        bar_volume = _int(row.get("volume"))
        if stamp is None or None in (bar_open, bar_high, bar_low, bar_close) or bar_volume is None:
            continue
        bars.append(Bar(stamp, bar_open, bar_high, bar_low, bar_close, bar_volume))
    bars.append(Bar(observed, open_price, high, low, close, today_volume))
    return UnderlyingSnapshot(
        symbol=symbol,
        price=price,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=today_volume,
        relative_volume=relative,
        prior_day_volume=_int(prior.get("volume")) or 0,
        rv_numerator=rv_numerator,
        rv_denominator=rv_denominator,
        rv_day_count=rv_day_count,
        rv_cutoff=rv_cutoff,
        rv_prior_days=rv_prior_days,
        rv_prior_totals=rv_prior_totals,
        rv_method=rv_method,
        change_percent=((price - prior_close) / prior_close) * Decimal("100"),
        change_dollars=price - prior_close,
        prior_close=prior_close,
        observed_at=observed,
        session_ok=session_ok,
        bars=tuple(bars),
    )


def _index_level(rows: list[dict], as_of: datetime, symbol: str) -> dict | None:
    for row in rows:
        if str(row.get("symbol") or "").strip().upper() != symbol:
            continue
        stamp = _at_scan(_stamp(row.get("timestamp")), as_of)
        price = _dec(row.get("price"))
        if stamp is None or price is None:
            continue
        return {"symbol": symbol, "price": price, "change_percent": None, "observed_at": stamp}
    return None


def quote_response_ready(rows: list[dict]) -> bool:
    for row in rows:
        if not isinstance(row, dict) or _stamp(row.get("timestamp")) is None:
            continue
        if _dec(row.get("bid")) is not None or _dec(row.get("price")) is not None or _dec(row.get("close")) is not None:
            return True
    return False


def chain_response_ready(rows: list[dict]) -> bool:
    for row in rows:
        if not isinstance(row, dict) or _contract_key(row) is None or _stamp(row.get("timestamp")) is None:
            continue
        if _dec(row.get("bid")) is not None and _dec(row.get("ask")) is not None:
            return True
    return False


def _option(
    symbol: str,
    quote: dict,
    ohlc: dict | None,
    interest: dict | None,
    greeks: dict | None,
    as_of: datetime,
) -> OptionSnapshot | None:
    quoted = _stamp(quote.get("timestamp"))
    # A snapshot returns the standing NBBO; its timestamp is the last change, not when it was read.
    # Chains are read after the scan clock starts, so a quote from this session is current as of the scan.
    observed = quoted
    if quoted is not None and quoted.astimezone(EXCHANGE).date() == as_of.astimezone(EXCHANGE).date():
        observed = as_of.astimezone(EXCHANGE)
    bid = _dec(quote.get("bid"))
    ask = _dec(quote.get("ask"))
    last = _dec((ohlc or {}).get("close"))
    volume = _int((ohlc or {}).get("volume"))
    open_interest = _int((interest or {}).get("open_interest"))
    expiration = _parse_day(quote.get("expiration"))
    strike = _dec(quote.get("strike"))
    right = _right(quote.get("right"))
    if None in (observed, bid, ask, strike, right) or expiration is None:
        return None
    if observed > as_of or bid <= 0 or ask < bid:
        return None
    if last is None:
        last = (bid + ask) / Decimal("2")
    if volume is None:
        volume = 0
    if open_interest is None:
        open_interest = 0
    greek = greeks or {}
    return OptionSnapshot(
        underlying=symbol,
        option_symbol=_occ(symbol, expiration, right, strike),
        right=right,
        strike=strike,
        expiration=expiration,
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
        open_interest=open_interest,
        observed_at=observed,
        implied_volatility=_dec(greek.get("implied_vol")),
        delta=_dec(greek.get("delta")),
        gamma=_dec(greek.get("gamma")),
        theta=_dec(greek.get("theta")),
        vega=_dec(greek.get("vega")),
    )


class ThetaMarketProvider:
    name = "thetadata"

    def __init__(self, feed: ThetaFeed) -> None:
        self.feed = feed

    def load_underlyings(self, as_of: datetime) -> list[UnderlyingSnapshot]:
        return list(self.feed.load(as_of)["underlyings"])

    def context_quotes(self) -> dict:
        return self.feed.context_quotes()

    def status_detail(self) -> dict:
        return feed_status()


class ThetaOptionsProvider:
    name = "thetadata"

    def __init__(self, feed: ThetaFeed) -> None:
        self.feed = feed

    def load_options(self, as_of: datetime) -> list[OptionSnapshot]:
        return list(self.feed.load_options(as_of))

    def bind_option_symbols(self, symbols: tuple[str, ...]) -> None:
        self.feed.bind_option_symbols(symbols)

    def status_detail(self) -> dict:
        return feed_status()
