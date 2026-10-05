from __future__ import annotations

import time

import httpx

from app.config.settings import Settings
from app.integrations.alpaca import PAPER_HTTP, AlpacaNotConfigured, AlpacaPaperClient, paper_base
from app.integrations.secrets import resolve_secret, scrub


def _timed(call) -> tuple[int, object]:
    started = time.perf_counter()
    value = call()
    return int((time.perf_counter() - started) * 1000), value


def _get(url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 8.0, **kwargs) -> httpx.Response:
    with httpx.Client(transport=transport, timeout=timeout) as client:
        return client.get(url, **kwargs)


def _post(url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 8.0, **kwargs) -> httpx.Response:
    with httpx.Client(transport=transport, timeout=timeout) as client:
        return client.post(url, **kwargs)


THETADATA_AUTH_URL = "https://nexus-api.thetadata.us/identity/terminal/auth_user"
THETADATA_CLIENT_KEY = "cf58ada4-4175-11f0-860f-1e2e95c79e64"


def probe_openai(settings: Settings, transport: httpx.BaseTransport | None = None) -> tuple[str, int | None, str]:
    key, _ = resolve_secret(settings, "openai_api_key")
    if not key:
        return "missing_key", None, "נדרש API Key"
    try:
        latency, response = _timed(
            lambda: _get(
                f"{settings.openai_base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=8.0,
                transport=transport,
            )
        )
    except httpx.HTTPError as exc:
        return "error", None, scrub(str(exc), [key])
    if response.status_code == 200:
        return "connected", latency, f"האימות עבר. מודל מוגדר: {settings.openai_model}"
    if response.status_code in {401, 403}:
        return "error", latency, "האימות נכשל"
    return "error", latency, f"תשובת שרת {response.status_code}"


def probe_alpaca(settings: Settings, transport: httpx.BaseTransport | None = None) -> tuple[str, int | None, str]:
    key, _ = resolve_secret(settings, "alpaca_api_key")
    secret, _ = resolve_secret(settings, "alpaca_api_secret")
    if not key or not secret:
        return "missing_key", None, "נדרש API Key"
    try:
        paper_base(settings.trading_mode)
    except Exception as exc:
        return "error", None, str(exc)
    client = AlpacaPaperClient(key, secret, settings.trading_mode, transport)
    try:
        latency, account = _timed(client.account)
    except AlpacaNotConfigured as exc:
        return "missing_key", None, str(exc)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in {401, 403}:
            return "error", None, "האימות מול Paper נכשל"
        return "error", None, f"Alpaca החזיר {code}"
    except httpx.HTTPError as exc:
        return "error", None, scrub(str(exc), [key, secret])
    finally:
        client.close()
    if not account:
        return "no_data", latency, "מחובר — אין נתונים"
    return "connected", latency, "חשבון Paper נקרא"


def probe_thetadata(settings: Settings, transport: httpx.BaseTransport | None = None) -> tuple[str, int | None, str]:
    key, _ = resolve_secret(settings, "thetadata_api_key")
    base, _ = resolve_secret(settings, "thetadata_base_url")
    if not key and not base:
        return "missing_key", None, "נדרש API Key"
    subscription = ""
    auth_result: tuple[str, int | None, str] | None = None
    if key:
        auth_result = _probe_thetadata_key(key)
        if auth_result[0] == "authenticated":
            subscription = auth_result[2]
    terminal = _probe_terminal_data(settings, transport, subscription)
    if terminal[0] != "blocked_by_entitlement":
        return terminal
    if auth_result is not None and auth_result[0] != "authenticated":
        return auth_result
    return terminal


def _tier(value: object) -> str:
    if isinstance(value, bool):
        return "אין נתונים"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for name in ("name", "tier", "plan", "level", "subscriptionType", "type"):
            found = value.get(name)
            if isinstance(found, str) and found.strip():
                return found.strip()
    return "אין נתונים"


def _probe_thetadata_key(key: str) -> tuple[str, int | None, str]:
    try:
        latency, response = _timed(
            lambda: _post(
                THETADATA_AUTH_URL,
                json={"apiKey": key, "authEnv": {"envType": "PROD"}},
                headers={"TD-TERMINAL-KEY": THETADATA_CLIENT_KEY},
                timeout=15.0,
            )
        )
    except httpx.HTTPError as exc:
        return "disconnected", None, scrub(str(exc), [key])
    if response.status_code in {401, 403}:
        return "error", latency, "האימות נכשל"
    if response.status_code != 200:
        return "error", latency, f"ThetaData החזיר {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return "error", latency, "ThetaData החזיר תשובה לא קריאה"
    user = body.get("user") if isinstance(body, dict) else None
    if not isinstance(body, dict) or not body.get("sessionId") or not isinstance(user, dict):
        return "error", latency, "האימות לא החזיר סשן"
    detail = (
        "האימות עבר. "
        f"מניות {_tier(user.get('stockSubscription'))}. "
        f"אופציות {_tier(user.get('optionsSubscription'))}. "
        f"מדדים {_tier(user.get('indicesSubscription'))}."
    )
    return "authenticated", latency, detail


def _probe_terminal_data(
    settings: Settings,
    transport: httpx.BaseTransport | None,
    subscription: str,
) -> tuple[str, int | None, str]:
    from app.providers.thetadata import DEFAULT_BASE, chain_response_ready, quote_response_ready

    symbol = settings.trading().research_probe_symbol.strip().upper()
    if not symbol:
        return "error", None, "אין סמל בדיקה"
    base, _ = resolve_secret(settings, "thetadata_base_url")
    root = (base or DEFAULT_BASE).rstrip("/")
    trading = settings.trading()
    try:
        latency, quote = _timed(
            lambda: _get(
                root + "/v3/stock/snapshot/quote",
                params={"symbol": symbol, "format": "json"},
                timeout=8.0,
                transport=transport,
            )
        )
        chain = _get(
            root + "/v3/option/snapshot/quote",
            params={"symbol": symbol, "expiration": "*", "max_dte": trading.max_expiration_days, "format": "json"},
            timeout=8.0,
            transport=transport,
        )
    except httpx.HTTPError:
        return "blocked_by_entitlement", None, "Theta Terminal לא רץ, או שאין entitlement לציטוט ולשרשרת."
    if quote.status_code in {401, 403} or chain.status_code in {401, 403}:
        return "blocked_by_entitlement", latency, "ThetaData סירב לנתונים. נדרשת הפעלת entitlement."
    if quote.status_code != 200 or chain.status_code != 200:
        code = quote.status_code if quote.status_code != 200 else chain.status_code
        return "error", latency, f"ThetaData החזיר {code}"
    try:
        quote_rows = quote.json()
        chain_rows = chain.json()
    except ValueError:
        return "error", latency, "ThetaData החזיר תשובה לא תקינה"
    quote_ready = quote_response_ready(quote_rows if isinstance(quote_rows, list) else [])
    chain_ready = chain_response_ready(chain_rows if isinstance(chain_rows, list) else [])
    if isinstance(quote_rows, dict):
        quote_ready = quote_response_ready(quote_rows.get("response") or [])
    if isinstance(chain_rows, dict):
        chain_ready = chain_response_ready(chain_rows.get("response") or [])
    if quote_ready and chain_ready:
        detail = "התקבל ציטוט ושרשרת אופציות."
        if subscription:
            detail = f"{detail} {subscription}"
        return "connected", latency, detail
    return "no_data", latency, "הטרמינל ענה בלי ציטוט או בלי שרשרת אופציות."


def probe_benzinga(settings: Settings, transport: httpx.BaseTransport | None = None) -> tuple[str, int | None, str]:
    key, _ = resolve_secret(settings, "benzinga_api_key")
    if not key:
        return "missing_key", None, "נדרש API Key"
    try:
        latency, response = _timed(
            lambda: _get(
                "https://api.benzinga.com/api/v2/news",
                params={"pageSize": 1, "token": key},
                headers={"Accept": "application/json"},
                timeout=8.0,
                transport=transport,
            )
        )
    except httpx.HTTPError as exc:
        return "error", None, scrub(str(exc), [key])
    if response.status_code == 200:
        body = response.json() if response.content else []
        if body:
            return "connected", latency, "התקבלה ידיעה"
        return "no_data", latency, "מחובר — אין נתונים"
    if response.status_code in {401, 403}:
        return "blocked_by_entitlement", latency, "Benzinga סירב. נדרשת הפעלת entitlement."
    return "error", latency, f"Benzinga החזיר {response.status_code}"


def probe_fred(settings: Settings, transport: httpx.BaseTransport | None = None) -> tuple[str, int | None, str]:
    from app.providers.fred import observation_ready

    key, _ = resolve_secret(settings, "fred_api_key")
    if not key:
        return "missing_key", None, "נדרש API Key"
    series = next((item for item in settings.trading().fred_series if item), "FEDFUNDS")
    try:
        latency, response = _timed(
            lambda: _get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series,
                    "api_key": key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 5,
                },
                timeout=8.0,
                transport=transport,
            )
        )
    except httpx.HTTPError as exc:
        return "error", None, scrub(str(exc), [key])
    if response.status_code in {400, 401, 403}:
        return "error", latency, "האימות נכשל"
    if response.status_code != 200:
        return "error", latency, f"FRED החזיר {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return "error", latency, "FRED החזיר תשובה לא תקינה"
    if observation_ready(payload):
        return "connected", latency, f"התקבלה תצפית {series}."
    return "no_data", latency, "אין תצפית מספרית"


def probe_sec(settings: Settings, transport: httpx.BaseTransport | None = None) -> tuple[str, int | None, str]:
    from app.providers.sec import SecError, SecFeed

    started = time.perf_counter()
    try:
        report = SecFeed(settings, transport=transport).health()
    except SecError as exc:
        return exc.status, None, str(exc)
    latency = int((time.perf_counter() - started) * 1000)
    if report["submissions_status"] == 200 and report["facts_status"] == 200 and report["filings"] and report["facts_available"]:
        detail = (
            f"submissions {report['submissions_status']}, company facts {report['facts_status']}, {report['symbol']}."
        )
        return "connected", latency, detail
    return "no_data", latency, "SEC ענה בלי דיווחים או בלי XBRL"


def probe_supabase(settings: Settings) -> tuple[str, int | None, str]:
    url, _ = resolve_secret(settings, "database_url")
    if not url.startswith("postgresql"):
        return "disconnected", None, "Supabase לא הוגדר. המסד המקומי אינו Supabase."
    if "supabase" not in url.lower() and settings.app_env != "production":
        return "disconnected", None, "כתובת Postgres אינה מזוהה כ-Supabase"
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    secret = make_url(url).password or ""
    try:
        started = time.perf_counter()
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        engine.dispose()
        latency = int((time.perf_counter() - started) * 1000)
    except Exception as exc:
        return "error", None, scrub(str(exc), [secret, url])
    return "connected", latency, "המסד ענה"


def probe_redis(settings: Settings) -> tuple[str, int | None, str]:
    from app.infra.redis_client import DEFAULT_URL, RedisClient, RedisUnavailable

    url, _ = resolve_secret(settings, "redis_url")
    client = RedisClient(url or DEFAULT_URL)
    started = time.perf_counter()
    try:
        if not client.ping():
            return "disconnected", None, "Redis לא מחובר"
        token = "beo-ping"
        client.set("beo:health", token, 30)
        if client.get("beo:health") != token:
            return "error", None, "Redis ענה ל-PING אבל GET/SET נכשל"
    except RedisUnavailable:
        return "disconnected", None, "Redis לא מחובר"
    finally:
        client.close()
    return "connected", int((time.perf_counter() - started) * 1000), "PING"


def probe_xcloud() -> tuple[str, int | None, str]:
    from app.models.db import database_ready

    started = time.perf_counter()
    if not database_ready():
        return "disconnected", None, "המסד על השרת לא ענה"
    latency = int((time.perf_counter() - started) * 1000)
    return "connected", latency, "התהליך רץ על השרת והמסד עונה"


def probe_internal(integration_id: str) -> tuple[str, int | None, str]:
    try:
        if integration_id == "quant":
            from app.quant.black_scholes import price

            assert callable(price)
            return "internal", None, "מנוע החישוב זמין בתהליך"
        if integration_id == "candidate":
            from app.recommendations.pipeline import run_scan

            assert callable(run_scan)
            return "internal", None, "מנוע הסינון זמין. אין עדיין מונה סריקה שמור."
        if integration_id == "risk":
            from app.recommendations.decision import build_recommendation

            assert callable(build_recommendation)
            return "internal", None, "שערי הסיכון זמינים"
        if integration_id == "audit":
            from app.models.store import save_recommendation

            assert callable(save_recommendation)
            return "internal", None, "שמירת ההחלטה זמינה"
        if integration_id == "backtest":
            from app.backtesting.engine import run_backtest

            assert callable(run_backtest)
            return "internal", None, "המנוע זמין. אין צילומי שוק, ולכן אין ריצה."
        if integration_id == "regime":
            from app.market.regime import build_regime

            assert callable(build_regime)
            from app.market.regime import last_regime

            latest = last_regime()
            if latest.get("status") == "CALCULATED":
                return "internal", None, "חושב מנתוני הקשר"
            return "internal", None, "ממומש — ממתין לנתוני שוק"
    except Exception as exc:
        return "error", None, scrub(str(exc))
    return "not_built", None, "לא מיושם"


PROBES = {
    "openai": probe_openai,
    "alpaca": probe_alpaca,
    "thetadata": probe_thetadata,
    "benzinga": probe_benzinga,
    "fred": probe_fred,
    "sec": probe_sec,
    "supabase": probe_supabase,
    "redis": probe_redis,
    "xcloud": lambda settings: probe_xcloud(),
}
