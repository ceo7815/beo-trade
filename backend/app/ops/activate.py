"""Provider activation checks. No orders are sent."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from app.config.settings import Settings, get_settings
from app.core.sessions import SCAN_PHASES, phase_at
from app.ai.telemetry import telemetry_from_response
from app.integrations.probes import probe_alpaca, probe_benzinga
from app.integrations.secrets import resolve_secret
from app.ops.safety import paper_errors
from app.providers.thetadata import _rows, _stamp, chain_response_ready, quote_response_ready

FRESH_SECONDS = 15 * 60


def classify_theta(
    *,
    reachable: bool,
    status_code: int,
    mdds: str,
    quote_rows: list[dict],
    chain_rows: list[dict],
    market_closed: bool,
    now: datetime,
) -> str:
    if not reachable:
        return "BLOCKED"
    if status_code in {401, 403} or "UNVERIFIED" in mdds.upper():
        return "AUTH ERROR"
    if "CONNECTED" not in mdds.upper():
        return "BLOCKED"
    if market_closed:
        return "MARKET CLOSED"
    if not quote_response_ready(quote_rows) or not chain_response_ready(chain_rows):
        return "NO DATA"
    if not _fresh(quote_rows, now) or not _fresh(chain_rows, now):
        return "NO DATA"
    return "READY"


def classify_benzinga(status: str) -> str:
    if status == "connected":
        return "READY"
    if status == "no_data":
        return "NO DATA"
    if status == "blocked_by_entitlement":
        return "AUTH ERROR"
    return "BLOCKED"


def classify_alpaca(status: str, detail: str = "") -> str:
    if status == "connected":
        return "READY"
    if status == "no_data":
        return "NO DATA"
    if status == "error" and "אימות" in detail:
        return "AUTH ERROR"
    return "BLOCKED"


def classify_openai(*, has_key: bool, status_code: int | None, parsed_ok: bool, telemetry_ok: bool) -> str:
    if not has_key:
        return "BLOCKED"
    if status_code in {401, 403, 404}:
        return "AUTH ERROR"
    if status_code == 200 and parsed_ok and telemetry_ok:
        return "READY"
    return "BLOCKED"


def _fresh(rows: list[dict], now: datetime) -> bool:
    for row in rows:
        stamp = _stamp(row.get("timestamp"))
        if stamp is None:
            continue
        if abs((now - stamp.astimezone(timezone.utc)).total_seconds()) <= FRESH_SECONDS:
            return True
    return False


def _theta_base(settings: Settings) -> str:
    base, _ = resolve_secret(settings, "thetadata_base_url")
    if base.strip():
        return base.strip().rstrip("/")
    host = settings.theta_terminal_host.strip()
    if host:
        return f"http://{host}:{settings.theta_terminal_port}"
    return ""


def check_theta(settings: Settings, now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    base = _theta_base(settings)
    if not base:
        return "BLOCKED"
    symbol = settings.trading().research_probe_symbol.strip().upper() or "NVDA"
    closed = phase_at(moment, settings.calendar()) not in SCAN_PHASES
    try:
        status = httpx.get(base + "/v3/terminal/mdds/status", timeout=8.0)
        quote = httpx.get(
            base + "/v3/stock/snapshot/quote",
            params={"symbol": symbol, "format": "json"},
            timeout=12.0,
        )
        chain = httpx.get(
            base + "/v3/option/snapshot/quote",
            params={"symbol": symbol, "expiration": "*", "max_dte": settings.trading().max_expiration_days, "format": "json"},
            timeout=20.0,
        )
    except httpx.HTTPError:
        return "BLOCKED"
    try:
        quote_rows = _rows(quote.json()) if quote.content else []
        chain_rows = _rows(chain.json()) if chain.content else []
    except ValueError:
        quote_rows, chain_rows = [], []
    return classify_theta(
        reachable=True,
        status_code=status.status_code,
        mdds=status.text,
        quote_rows=quote_rows,
        chain_rows=chain_rows,
        market_closed=closed,
        now=moment,
    )


def check_openai(settings: Settings) -> str:
    key, _ = resolve_secret(settings, "openai_api_key")
    if not key:
        return classify_openai(has_key=False, status_code=None, parsed_ok=False, telemetry_ok=False)
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["ok"]}},
        "required": ["status"],
        "additionalProperties": False,
    }
    body = {
        "model": settings.openai_model,
        "max_output_tokens": 40,
        "input": [{"role": "user", "content": "Return the required JSON object only."}],
        "text": {"format": {"type": "json_schema", "name": "provider_ping", "strict": True, "schema": schema}},
    }
    try:
        response = httpx.post(
            settings.openai_base_url.rstrip("/") + "/responses",
            headers={"Authorization": f"Bearer {key}"},
            json=body,
            timeout=45.0,
        )
    except httpx.HTTPError:
        return "BLOCKED"
    parsed_ok = False
    telemetry_ok = False
    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        measured = telemetry_from_response(payload if isinstance(payload, dict) else {}, 0)
        telemetry_ok = measured.get("input_tokens") is not None and measured.get("output_tokens") is not None
        text = ""
        for item in payload.get("output", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = str(content.get("text") or "")
        try:
            parsed_ok = json.loads(text).get("status") == "ok"
        except (json.JSONDecodeError, AttributeError):
            parsed_ok = False
    return classify_openai(
        has_key=True,
        status_code=response.status_code,
        parsed_ok=parsed_ok,
        telemetry_ok=telemetry_ok,
    )


def build_activation(settings: Settings, now: datetime | None = None) -> dict:
    benzinga_status, _, _ = probe_benzinga(settings)
    alpaca_status, _, alpaca_detail = probe_alpaca(settings)
    return {
        "paper_safety": not paper_errors(settings),
        "trading_mode": "PAPER",
        "live": "DISABLED",
        "providers": {
            "ThetaData": check_theta(settings, now),
            "Benzinga": classify_benzinga(benzinga_status),
            "OpenAI": check_openai(settings),
            "Alpaca Paper": classify_alpaca(alpaca_status, alpaca_detail),
        },
    }


def main() -> None:
    report = build_activation(get_settings())
    for name, status in report["providers"].items():
        print(f"{name}: {status}")
    print("Paper Safety: " + ("YES" if report["paper_safety"] else "NO"))
    print("Live: DISABLED")
    print("No order was submitted.")


if __name__ == "__main__":
    main()
