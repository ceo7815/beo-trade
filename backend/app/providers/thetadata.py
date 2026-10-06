from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import Settings
from app.core.sessions import SCAN_PHASES, phase_at
from app.integrations.secrets import resolve_secret
from app.providers.base import ProviderUnavailable
from app.quant.relative_volume import LAST_BAR_OPEN, SESSION_OPEN, time_of_day_relative_volume
from app.schemas.domain import Bar, OptionRight, OptionSnapshot, UnderlyingSnapshot

DEFAULT_BASE = "http://127.0.0.1:25503"
EXCHANGE = ZoneInfo("America/New_York")


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


def reset_theta_state() -> None:
    global _state, _bundle
    _state = _State()
    _bundle = None


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


def _rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("response", "data"):
            raw = payload.get(key)
            if isinstance(raw, list):
                return [row for row in raw if isinstance(row, dict)]
        if "symbol" in payload:
            return [payload]
    return []


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
    expiration = str(row.get("expiration") or "")[:10]
    strike = _dec(row.get("strike"))
    right = _right(row.get("right"))
    if not expiration or strike is None or right is None:
        return None
    return (expiration, format(strike, "f"), right.value)


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


class ThetaFeed:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport
        self._symbols: tuple[str, ...] = ()
        self._option_symbols: tuple[str, ...] = ()
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

    def _get(self, path: str, params: dict, required: bool = True, _attempt: int = 0) -> list[dict]:
        try:
            with httpx.Client(transport=self.transport, timeout=20) as client:
                response = client.get(self.base + path, params={"format": "json", **params})
        except httpx.HTTPError as exc:
            if not required:
                return []
            self._fail("ThetaData לא זמין")
            raise ThetaDataError("ThetaData לא זמין") from exc
        code = response.status_code
        symbols = [part.strip() for part in str(params.get("symbol") or "").split(",") if part.strip()]
        if code in {403, 429} and _attempt < 1:
            return self._get(path, params, required=required, _attempt=_attempt + 1)
        if code in {403, 429, 472} and len(symbols) > 1:
            rows: list[dict] = []
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
            if not required:
                return []
            self._fail("ThetaData החזיר תשובה לא תקינה")
            raise ThetaDataError("ThetaData החזיר תשובה לא תקינה") from exc
        return _rows(payload)

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
        ohlc = _by_symbol(self._get("/v3/stock/snapshot/ohlc", {"symbol": listed}))
        quotes = _by_symbol(self._get("/v3/stock/snapshot/quote", {"symbol": listed}))
        trades = _by_symbol(self._get("/v3/stock/snapshot/trade", {"symbol": listed}))
        history: dict[str, list[dict]] = {}
        for symbol in stocks:
            if symbol not in ohlc and symbol not in trades:
                continue
            start = (session_day - timedelta(days=30)).strftime("%Y%m%d")
            end = (session_day - timedelta(days=1)).strftime("%Y%m%d")
            history[symbol] = self._get(
                "/v3/stock/history/eod",
                {"symbol": symbol, "start_date": start, "end_date": end},
            )
        intraday: dict[str, list[dict]] = {}
        cutoff = _completed_minute(as_of)
        if cutoff is not None:
            window_start = (session_day - timedelta(days=30)).strftime("%Y%m%d")
            window_end = session_day.strftime("%Y%m%d")
            end_clock = cutoff.strftime("%H:%M:%S")
            for symbol in stocks:
                if symbol not in ohlc and symbol not in trades:
                    continue
                intraday[symbol] = self._get(
                    "/v3/stock/history/ohlc",
                    {
                        "symbol": symbol,
                        "start_date": window_start,
                        "end_date": window_end,
                        "interval": "1m",
                        "start_time": "09:30:00",
                        "end_time": end_clock,
                    },
                    required=False,
                )
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

    def _options(self, symbols: tuple[str, ...], as_of: datetime, max_dte: int, strike_range: int) -> list[OptionSnapshot]:
        contracts: list[OptionSnapshot] = []
        for symbol in symbols:
            params = {"symbol": symbol, "expiration": "*", "max_dte": max_dte, "strike_range": strike_range}
            quotes = _index_contracts(self._get("/v3/option/snapshot/quote", params))
            ohlc = _index_contracts(self._get("/v3/option/snapshot/ohlc", params))
            interest = _index_contracts(self._get("/v3/option/snapshot/open_interest", params))
            greeks = _index_contracts(self._get("/v3/option/snapshot/greeks/all", params))
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
    observed = _stamp((quote or {}).get("timestamp")) or _stamp((trade or {}).get("timestamp")) or _stamp((ohlc or {}).get("timestamp"))
    price = _dec((trade or {}).get("price")) or _dec((ohlc or {}).get("close"))
    if observed is None or price is None or observed > as_of:
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
    observed = _stamp((quote or {}).get("timestamp")) or _stamp((trade or {}).get("timestamp")) or _stamp((ohlc or {}).get("timestamp"))
    price = _dec((trade or {}).get("price")) or _dec((ohlc or {}).get("close"))
    if observed is None or price is None or observed > as_of or ohlc is None:
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
        stamp = _stamp(row.get("timestamp"))
        price = _dec(row.get("price"))
        if stamp is None or price is None or stamp > as_of:
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
    observed = _stamp(quote.get("timestamp"))
    bid = _dec(quote.get("bid"))
    ask = _dec(quote.get("ask"))
    last = _dec((ohlc or {}).get("close"))
    volume = _int((ohlc or {}).get("volume"))
    open_interest = _int((interest or {}).get("open_interest"))
    expiration_text = str(quote.get("expiration") or "")[:10]
    strike = _dec(quote.get("strike"))
    right = _right(quote.get("right"))
    if None in (observed, bid, ask, strike, right) or not expiration_text:
        return None
    if observed > as_of or bid <= 0 or ask < bid:
        return None
    if last is None:
        last = (bid + ask) / Decimal("2")
    if volume is None:
        volume = 0
    if open_interest is None:
        open_interest = 0
    expiration = date.fromisoformat(expiration_text)
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
