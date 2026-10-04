from datetime import datetime, timedelta, timezone

from app.ops.activate import classify_alpaca, classify_benzinga, classify_openai, classify_theta

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
STAMP = (NOW - timedelta(seconds=20)).isoformat()


def _quote():
    return [{"timestamp": STAMP, "bid": "1.25", "symbol": "NVDA"}]


def _chain():
    return [{"timestamp": STAMP, "bid": "1.00", "ask": "1.10", "expiration": "2026-10-10", "strike": "100", "right": "CALL"}]


def test_theta_market_closed_is_not_an_error():
    assert classify_theta(
        reachable=True,
        status_code=200,
        mdds="CONNECTED",
        quote_rows=[],
        chain_rows=[],
        market_closed=True,
        now=NOW,
    ) == "MARKET CLOSED"


def test_theta_ready_requires_quote_chain_and_fresh_timestamp():
    assert classify_theta(
        reachable=True,
        status_code=200,
        mdds="CONNECTED",
        quote_rows=_quote(),
        chain_rows=_chain(),
        market_closed=False,
        now=NOW,
    ) == "READY"


def test_theta_unreachable_is_blocked_even_when_the_market_is_closed():
    assert classify_theta(
        reachable=False,
        status_code=0,
        mdds="",
        quote_rows=[],
        chain_rows=[],
        market_closed=True,
        now=NOW,
    ) == "BLOCKED"


def test_theta_unverified_is_an_auth_error():
    assert classify_theta(
        reachable=True,
        status_code=200,
        mdds="UNVERIFIED",
        quote_rows=_quote(),
        chain_rows=_chain(),
        market_closed=False,
        now=NOW,
    ) == "AUTH ERROR"


def test_theta_open_without_chain_is_no_data():
    assert classify_theta(
        reachable=True,
        status_code=200,
        mdds="CONNECTED",
        quote_rows=_quote(),
        chain_rows=[],
        market_closed=False,
        now=NOW,
    ) == "NO DATA"


def test_provider_words():
    assert classify_benzinga("connected") == "READY"
    assert classify_benzinga("no_data") == "NO DATA"
    assert classify_benzinga("blocked_by_entitlement") == "AUTH ERROR"
    assert classify_benzinga("missing_key") == "BLOCKED"
    assert classify_alpaca("connected") == "READY"
    assert classify_alpaca("error", "האימות מול Paper נכשל") == "AUTH ERROR"
    assert classify_alpaca("missing_key") == "BLOCKED"
    assert classify_openai(has_key=False, status_code=None, parsed_ok=False, telemetry_ok=False) == "BLOCKED"
    assert classify_openai(has_key=True, status_code=401, parsed_ok=False, telemetry_ok=False) == "AUTH ERROR"
    assert classify_openai(has_key=True, status_code=200, parsed_ok=True, telemetry_ok=True) == "READY"
