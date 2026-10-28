import httpx
import pytest
from structlog.testing import capture_logs

from app.config.settings import Settings
from app.integrations.alpaca import AlpacaPaperClient
from app.ops.failure import log_failure


def _http_error(status: int = 502) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://paper-api.alpaca.markets/v2/positions?api_key=SUPERSECRET")
    response = httpx.Response(status, request=request)
    try:
        raise httpx.HTTPStatusError(
            "Server error for url 'https://paper-api.alpaca.markets/v2/positions?api_key=SUPERSECRET'",
            request=request,
            response=response,
        )
    except httpx.HTTPStatusError as exc:
        return exc


@pytest.mark.parametrize("event", ["worker.scan_failed", "worker.monitor_failed"])
def test_loop_failure_logs_class_status_host_and_path_without_the_query(event):
    with capture_logs() as logs:
        log_failure(event, _http_error(), service="worker")
    row = logs[0]
    assert row["event"] == event
    assert row["error"] == "HTTPStatusError"
    assert row["status_code"] == 502
    assert row["upstream"] == "alpaca"
    assert row["host"] == "paper-api.alpaca.markets"
    assert row["method"] == "GET"
    assert row["path"] == "/v2/positions"
    assert row["traceback"]
    assert "SUPERSECRET" not in str(row)
    assert "?" not in row["path"]


def test_wrapped_http_error_keeps_the_upstream_request():
    try:
        raise _http_error(429)
    except httpx.HTTPStatusError as exc:
        wrapped = RuntimeError("scan")
        wrapped.__cause__ = exc
    with capture_logs() as logs:
        log_failure("worker.scan_failed", wrapped, service="worker")
    row = logs[0]
    assert row["error"] == "RuntimeError"
    assert row["status_code"] == 429
    assert row["path"] == "/v2/positions"
    assert row["upstream"] == "alpaca"


def test_monitor_loop_logs_the_caught_http_error(monkeypatch):
    import app.workers.runner as runner

    def boom(_settings):
        runner._stop = True
        raise _http_error()

    monkeypatch.setattr(runner, "monitor_once", boom)
    monkeypatch.setattr(runner, "beat", lambda *_args, **_kwargs: None)
    settings = Settings(auth_required=False, openai_price_input_per_million=0, openai_price_output_per_million=0)
    try:
        with capture_logs() as logs:
            runner._monitor_forever(settings, settings.calendar(), 0)
    finally:
        runner._stop = False
    row = next(item for item in logs if item["event"] == "worker.monitor_failed")
    assert row["error"] == "HTTPStatusError"
    assert row["status_code"] == 502
    assert row["host"] == "paper-api.alpaca.markets"
    assert row["path"] == "/v2/positions"
    assert row["service"] == "monitor"
    assert "SUPERSECRET" not in str(row)


def test_alpaca_client_logs_upstream_and_status_on_non_2xx(monkeypatch):
    monkeypatch.setattr("app.integrations.alpaca._pause", lambda _seconds: None)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "no"})

    client = AlpacaPaperClient("PKPAPERKEY1234", "secret-value-9999", "PAPER", httpx.MockTransport(handler))
    with capture_logs() as logs:
        with pytest.raises(httpx.HTTPStatusError):
            client.account()
    row = next(item for item in logs if item["event"] == "alpaca.http")
    assert row["upstream"] == "alpaca"
    assert row["status_code"] == 422
    assert row["method"] == "GET"
    assert row["path"] == "/v2/account"
    assert "secret" not in row["path"]
