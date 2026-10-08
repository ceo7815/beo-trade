from __future__ import annotations

import json
import time
from datetime import datetime
from decimal import Decimal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.budget import BudgetLedger, UsageEntry
from app.ai.prompts import PROMPT_VERSION, SYSTEM_PROMPT, decision_json_schema
from app.ai.telemetry import telemetry_from_response
from app.config.settings import Settings
from app.integrations.secrets import resolve_secret
from app.schemas.domain import REASON_CODES, ModelOutput


class DecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    underlying: str
    option_symbol: str
    call_put: str
    strike: float
    expiration: str
    option_price: float
    max_entry_price: float
    holding_window_minutes: int
    thesis: str
    catalyst: str
    risk: str
    invalidation: str
    reason_codes: list[str] = Field(default_factory=list)
    confidence: float
    risk_factors: list[str]
    holding_window: str
    required_conditions: list[str]
    data_quality: str


class AIClientError(RuntimeError):
    pass


def extract_output_text(payload: dict) -> str:
    if payload.get("status") not in (None, "completed"):
        raise AIClientError(f"response status {payload.get('status')}")
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                raise AIClientError("model refusal")
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    raise AIClientError("response did not include structured text")


def parse_decision(payload: dict) -> DecisionPayload:
    text = extract_output_text(payload)
    try:
        data = json.loads(text)
        parsed = DecisionPayload.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AIClientError("structured output failed validation") from exc
    if parsed.decision not in {"BUY", "SUPPRESS"}:
        raise AIClientError("decision is outside the schema")
    if parsed.call_put not in {"CALL", "PUT"}:
        raise AIClientError("call_put is outside the schema")
    if any(code not in REASON_CODES for code in parsed.reason_codes):
        raise AIClientError("reason code is outside the schema")
    if parsed.data_quality not in {"PASS", "FAIL"}:
        raise AIClientError("data_quality is outside the schema")
    return parsed


class OpenAIResponsesClient:
    """Calls the OpenAI Responses API. Prices and the model id come from configuration."""

    def __init__(self, settings: Settings, ledger: BudgetLedger) -> None:
        self.settings = settings
        self.ledger = ledger

    def analyze(self, packet: dict, as_of: datetime) -> ModelOutput:
        api_key, _ = resolve_secret(self.settings, "openai_api_key")
        if not api_key:
            raise AIClientError("OPENAI_API_KEY is not configured")
        if not self.ledger.allow("decision", as_of):
            raise AIClientError("AI budget blocked the call")
        user_text = json.dumps(packet, ensure_ascii=False)
        limit = self.settings.trading().max_input_tokens * 4
        body = {
            "model": self.settings.openai_model,
            "max_output_tokens": self.settings.trading().max_output_tokens,
            "prompt_cache_key": PROMPT_VERSION,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text[:limit]},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "buy_decision",
                    "strict": True,
                    "schema": decision_json_schema(),
                }
            },
        }
        started = time.perf_counter()
        response = httpx.post(
            self.settings.openai_base_url.rstrip("/") + "/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=45.0,
        )
        response.raise_for_status()
        payload = response.json()
        latency_ms = int((time.perf_counter() - started) * 1000)
        measured = telemetry_from_response(payload, latency_ms)
        input_tokens = measured["input_tokens"]
        cached_tokens = measured["cached_tokens"]
        output_tokens = measured["output_tokens"]
        cost = None
        if None not in (input_tokens, output_tokens):
            cost = self.ledger.cost_of(input_tokens, cached_tokens or 0, output_tokens)
        self.ledger.record(
            UsageEntry(
                process="decision",
                created_at=as_of,
                cost=cost or Decimal("0"),
                input_tokens=input_tokens or 0,
                cached_tokens=cached_tokens or 0,
                output_tokens=output_tokens or 0,
                model=self.settings.openai_model,
            )
        )
        parsed = parse_decision(payload)
        return ModelOutput(
            decision=parsed.decision,
            option_symbol=parsed.option_symbol,
            call_put=parsed.call_put,
            thesis=parsed.thesis,
            catalyst=parsed.catalyst,
            risk=parsed.risk,
            invalidation=parsed.invalidation,
            reason_codes=list(parsed.reason_codes),
            raw=parsed.model_dump(),
            model=self.settings.openai_model,
            prompt_version=PROMPT_VERSION,
            input_hash="",
            input_tokens=input_tokens or 0,
            cached_tokens=cached_tokens or 0,
            output_tokens=output_tokens or 0,
            reasoning_tokens=measured["reasoning_tokens"] or 0,
            cost=cost or Decimal("0"),
            telemetry={**measured, "estimated_cost": None if cost is None else str(cost), "model": self.settings.openai_model, "decision": parsed.decision},
        )
