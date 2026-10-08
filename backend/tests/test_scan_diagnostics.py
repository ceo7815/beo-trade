import httpx

from app.recommendations.live_scan import _StageClock
from app.recommendations.pipeline import ai_failure_label


def _error(status: int, body) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status, json=body, request=request) if body is not None else httpx.Response(status, text="busy", request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


def test_quota_and_rate_limit_are_told_apart():
    assert ai_failure_label(_error(429, {"error": {"code": "insufficient_quota"}})) == "HTTPStatusError:429:insufficient_quota"
    assert ai_failure_label(_error(429, {"error": {"code": "rate_limit_exceeded"}})) == "HTTPStatusError:429:rate_limit_exceeded"


def test_an_unreadable_body_keeps_the_status():
    assert ai_failure_label(_error(500, None)) == "HTTPStatusError:500"
    assert ai_failure_label(TimeoutError("slow")) == "TimeoutError"


def test_stage_clock_records_each_stage():
    clock = _StageClock()
    clock.mark("universe")
    clock.mark("options")
    assert set(clock.seconds) == {"universe", "options"}
    assert all(value >= 0 for value in clock.seconds.values())
