from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import Settings
from app.providers.base import ProviderUnavailable

_HTTP_CLIENT = httpx.Client
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
EXCHANGE = ZoneInfo("America/New_York")
CACHE_OK = timedelta(hours=6)
MAX_PER_SECOND = 10
RETRY_CAP_SECONDS = 5
FILING_LIMIT = 5
FACT_UNITS = ("USD", "USD/shares", "shares", "pure")


class SecError(ProviderUnavailable):
    def __init__(self, detail: str, status: str = "error") -> None:
        super().__init__(detail)
        self.status = status


@dataclass
class _SecState:
    authenticated: bool | None = None
    last_success: str | None = None
    last_fetch: str | None = None
    filing_count: int = 0
    fact_count: int = 0
    submissions_status: int | None = None
    facts_status: int | None = None
    last_error: str | None = None
    snapshot: dict | None = None


_state = _SecState()
_tickers: dict[str, str] | None = None
_companies: dict[tuple[str, str], dict] = {}


class _Limiter:
    def __init__(self, per_second: int = MAX_PER_SECOND) -> None:
        self.interval = 1 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


_limiter = _Limiter()


def reset_sec_state() -> None:
    global _state, _tickers, _companies
    _state = _SecState()
    _tickers = None
    _companies = {}


def sec_status() -> dict:
    return {
        "provider": "sec",
        "authenticated": _state.authenticated,
        "last_success": _state.last_success,
        "last_fetch": _state.last_fetch,
        "filing_count": _state.filing_count,
        "fact_count": _state.fact_count,
        "submissions_status": _state.submissions_status,
        "facts_status": _state.facts_status,
        "last_error": _state.last_error,
    }


def classify_http(status_code: int, retry_after: str | None) -> tuple[str, str, float | None]:
    if status_code == 403:
        return "error", "SEC סירב לבקשה", None
    if status_code == 429:
        return "error", "SEC הגביל את הקצב", _retry_delay(retry_after)
    if status_code != 200:
        return "error", f"SEC החזיר {status_code}", None
    return "ok", "", None


def _retry_delay(header: str | None) -> float:
    try:
        seconds = float(header or "1")
    except ValueError:
        seconds = 1
    return min(max(seconds, 0), RETRY_CAP_SECONDS)


class SecFeed:
    name = "sec"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        agent = (settings.sec_user_agent or "").strip()
        if "@" not in agent:
            raise SecError("נדרש User-Agent עם אימייל קשר")
        self.settings = settings
        self.transport = transport
        self._agent = agent

    def status_detail(self) -> dict:
        return sec_status()

    def latest(self, as_of: datetime) -> dict | None:
        fetched = _parse_fetch(_state.last_fetch)
        if fetched is not None and datetime.now(EXCHANGE) - fetched < CACHE_OK and _state.snapshot is not None:
            return _state.snapshot
        if fetched is not None and datetime.now(EXCHANGE) - fetched < timedelta(seconds=60) and not _state.authenticated:
            return None
        try:
            return self.health(as_of)
        except SecError:
            return None

    def health(self, as_of: datetime | None = None) -> dict:
        moment = as_of or datetime.now(EXCHANGE)
        symbol = self.settings.trading().research_probe_symbol.strip().upper()
        if not symbol:
            self._fail("אין סמל בדיקה")
            raise SecError("אין סמל בדיקה")
        company = self._company(symbol, moment)
        snapshot = {"source": "sec", "companies": {company["symbol"]: company}}
        self._succeed(snapshot, company)
        return company

    def load(self, as_of: datetime, symbols: tuple[str, ...]) -> dict | None:
        companies = {}
        errors: list[str] = []
        for symbol in dict.fromkeys(item.strip().upper() for item in symbols if item.strip()):
            try:
                companies[symbol] = self._company(symbol, as_of)
            except SecError as exc:
                errors.append(str(exc))
        if not companies:
            self._fail(errors[0] if errors else "אין דיווחים או XBRL")
            return None
        snapshot = {"source": "sec", "companies": companies}
        first = next(iter(companies.values()))
        self._succeed(snapshot, first)
        return snapshot

    def _succeed(self, snapshot: dict, company: dict) -> None:
        now = datetime.now(EXCHANGE).isoformat()
        ready = bool(company["filings"]) and company["facts_available"]
        _state.last_fetch = now
        _state.filing_count = len(company["filings"])
        _state.fact_count = len(company["facts"])
        _state.submissions_status = company["submissions_status"]
        _state.facts_status = company["facts_status"]
        _state.authenticated = True if ready else False
        _state.last_success = now if ready else None
        _state.last_error = None if ready else "אין דיווחים או XBRL"
        _state.snapshot = snapshot if ready else None

    def _fail(self, message: str) -> None:
        _state.last_fetch = datetime.now(EXCHANGE).isoformat()
        _state.last_error = message
        _state.authenticated = False
        _state.last_success = None

    def _company(self, symbol: str, as_of: datetime) -> dict:
        session_day = as_of.astimezone(EXCHANGE).date().isoformat()
        cached = _companies.get((symbol, session_day))
        if cached is not None:
            return cached
        cik = self._cik(symbol)
        submissions_url = SUBMISSIONS_URL.format(cik=cik)
        facts_url = FACTS_URL.format(cik=cik)
        with _HTTP_CLIENT(transport=self.transport, timeout=30, follow_redirects=True) as client:
            submissions_response = self._get(client, submissions_url)
            submissions = _json_object(submissions_response)
            _state.submissions_status = submissions_response.status_code
            facts_response = self._get(client, facts_url)
            facts_payload = _json_object(facts_response)
            _state.facts_status = facts_response.status_code
        filings = _filings(submissions, session_day)
        extracted, available = _facts(facts_payload, self.settings.trading().sec_fact_tags, session_day)
        company = {
            "symbol": symbol,
            "cik": cik,
            "entity": str(submissions.get("name") or facts_payload.get("entityName") or ""),
            "submissions_status": submissions_response.status_code,
            "facts_status": facts_response.status_code,
            "submissions_endpoint": submissions_url,
            "facts_endpoint": facts_url,
            "filings": filings,
            "facts": extracted,
            "facts_available": available,
        }
        if filings and available:
            _companies[(symbol, session_day)] = company
        return company

    def _cik(self, symbol: str) -> str:
        global _tickers
        if _tickers is None:
            with _HTTP_CLIENT(transport=self.transport, timeout=30, follow_redirects=True) as client:
                payload = _json_object(self._get(client, TICKERS_URL))
            mapped: dict[str, str] = {}
            for row in payload.values():
                if not isinstance(row, dict):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                cik = row.get("cik_str")
                if ticker and cik is not None:
                    mapped[ticker] = str(cik).zfill(10)
            _tickers = mapped
        cik = _tickers.get(symbol)
        if not cik:
            raise SecError(f"אין CIK עבור {symbol}")
        return cik

    def _get(self, client: httpx.Client, url: str) -> httpx.Response:
        response = self._send(client, url)
        kind, detail, delay = classify_http(response.status_code, response.headers.get("Retry-After"))
        if response.status_code == 429 and delay is not None:
            time.sleep(delay)
            response = self._send(client, url)
            kind, detail, _delay = classify_http(response.status_code, response.headers.get("Retry-After"))
        if kind != "ok":
            self._fail(detail)
            raise SecError(detail, "disconnected" if kind == "disconnected" else "error")
        return response

    def _send(self, client: httpx.Client, url: str) -> httpx.Response:
        _limiter.wait()
        try:
            return client.get(url, headers={"User-Agent": self._agent, "Accept": "application/json"})
        except httpx.HTTPError as exc:
            self._fail("SEC לא זמין")
            raise SecError("SEC לא זמין", "disconnected") from exc


def _json_object(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise SecError("SEC החזיר תשובה לא תקינה") from exc
    if not isinstance(payload, dict):
        raise SecError("SEC החזיר תשובה לא תקינה")
    return payload


def _filings(payload: dict, session_day: str) -> list[dict]:
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    count = min(len(forms), len(dates), len(accessions))
    rows = []
    for index in range(count):
        filed = str(dates[index])
        if not filed or filed > session_day:
            continue
        rows.append(
            {
                "form": str(forms[index]),
                "filed": filed,
                "accession": str(accessions[index]),
                "document": str(documents[index]) if index < len(documents) else "",
            }
        )
        if len(rows) == FILING_LIMIT:
            break
    return rows


def _facts(payload: dict, tags: tuple[str, ...], session_day: str) -> tuple[list[dict], bool]:
    taxonomies = payload.get("facts")
    available = isinstance(taxonomies, dict) and bool(taxonomies)
    concepts = (taxonomies or {}).get("us-gaap") if isinstance(taxonomies, dict) else None
    if not isinstance(concepts, dict):
        return [], available
    extracted = []
    for tag in tags:
        concept = concepts.get(tag)
        if not isinstance(concept, dict):
            continue
        units = concept.get("units") if isinstance(concept.get("units"), dict) else {}
        series = next((units.get(unit) for unit in FACT_UNITS if isinstance(units.get(unit), list)), None)
        chosen = _latest_fact(series or [], session_day)
        if chosen is None:
            continue
        extracted.append(
            {
                "tag": tag,
                "label": str(concept.get("label") or tag),
                "unit": next(unit for unit in FACT_UNITS if isinstance(units.get(unit), list)),
                "filed": str(chosen.get("filed") or ""),
                "end": str(chosen.get("end") or ""),
                "form": str(chosen.get("form") or ""),
                "value": chosen.get("val"),
            }
        )
    return extracted, available


def _latest_fact(series: list, session_day: str) -> dict | None:
    chosen = None
    for item in series:
        if not isinstance(item, dict) or item.get("val") is None:
            continue
        filed = str(item.get("filed") or "")
        if not filed or filed > session_day:
            continue
        if chosen is None or filed > str(chosen.get("filed") or ""):
            chosen = item
    return chosen


def _parse_fetch(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EXCHANGE)
    return parsed.astimezone(EXCHANGE)
