import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.broker import metrics, snapshot
from app.broker.adapter import AlpacaAdapter
from app.broker.snapshot import PARTS, SnapshotCache
from app.config.settings import Settings
from app.integrations.alpaca import READ_ATTEMPTS, AlpacaPaperClient
from app.main import create_app


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    snapshot.isolate(tmp_path)
    metrics.isolate(tmp_path)
    yield
    snapshot.isolate(None)
    metrics.isolate(None)
    metrics.reset()


def _client(handler) -> AlpacaPaperClient:
    return AlpacaPaperClient("PKPAPERKEY1234", "secret-value-9999", "PAPER", httpx.MockTransport(handler))


def _pauses(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("app.integrations.alpaca._pause", lambda seconds: slept.append(seconds))
    return slept


def test_ttl_serves_a_fresh_snapshot_then_refreshes():
    clock = {"t": 100.0}
    snapshot.cache._now = lambda: clock["t"]
    calls = []

    def fetch(parts):
        calls.append(set(parts))
        return {part: {"part": part, "n": len(calls)} for part in parts}, {}

    first = snapshot.cache.value("positions", ttl=5, fetch=fetch)
    second = snapshot.cache.value("positions", ttl=5, fetch=fetch)
    clock["t"] = 104.9
    third = snapshot.cache.value("positions", ttl=5, fetch=fetch, fresh=True)
    clock["t"] = 105.0
    fourth = snapshot.cache.value("positions", ttl=5, fetch=fetch, fresh=True)
    assert first == second == third
    assert first["n"] == 1
    assert fourth["n"] == 2
    assert len(calls) == 2


def test_concurrent_callers_coalesce_to_one_fetch():
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def fetch(parts):
        calls["n"] += 1
        started.set()
        assert release.wait(2)
        return {part: {"ok": True} for part in parts}, {}

    results = []
    errors = []

    def work():
        try:
            results.append(snapshot.cache.value("account", ttl=5, fetch=fetch))
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=work)
    second = threading.Thread(target=work)
    first.start()
    assert started.wait(2)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(2)
    second.join(2)
    assert errors == []
    assert calls["n"] == 1
    assert len(results) == 2
    assert results[0] == results[1]


def test_second_cache_reads_the_shared_file_without_another_fetch(tmp_path):
    primary = SnapshotCache()
    replica = SnapshotCache()
    primary.isolate(tmp_path)
    replica.isolate(tmp_path)
    primary._now = lambda: 50.0
    replica._now = lambda: 50.0
    calls = []

    def fetch(parts):
        calls.append(set(parts))
        return {part: {"part": part} for part in parts}, {}

    primary.value("clock", ttl=5, fetch=fetch)
    replica.value("clock", ttl=5, fetch=fetch)
    replica.value("positions", ttl=5, fetch=fetch)
    assert calls == [set(PARTS)]
    assert "clock" in calls[0]


def test_refresh_cycle_includes_one_clock_call(monkeypatch):
    calls = []

    def fetch(settings, parts):
        calls.append(set(parts))
        return {part: {"is_open": True} if part == "clock" else [] for part in parts}, {}

    monkeypatch.setattr(snapshot, "_fetch_parts", fetch)
    settings = Settings(broker_snapshot_ttl_seconds=5, alpaca_api_key="PKPAPERKEY1234", alpaca_api_secret="secret-value-9999")
    snapshot.refresh_due(settings)
    snapshot.refresh_due(settings)
    assert len(calls) == 1
    assert calls[0] == set(PARTS)


def test_force_refresh_failure_does_not_return_expired_positions():
    clock = {"t": 0.0}
    snapshot.cache._now = lambda: clock["t"]
    calls = {"n": 0}

    def fetch(parts):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("down")
        return {part: [{"symbol": "X"}] if part == "positions" else {} for part in parts}, {}

    snapshot.cache.value("positions", ttl=5, fetch=fetch, fresh=True)
    clock["t"] = 4.0
    assert snapshot.cache.value("positions", ttl=5, fetch=fetch, fresh=True) == [{"symbol": "X"}]
    assert calls["n"] == 1
    clock["t"] = 5.0
    with pytest.raises(RuntimeError, match="down"):
        snapshot.cache.value("positions", ttl=5, fetch=fetch, fresh=True)
    assert snapshot.cache.peek("positions") == [{"symbol": "X"}]


def test_invalidate_refreshes_the_next_exit_read_and_keeps_the_screen_value():
    clock = {"t": 20.0}
    snapshot.cache._now = lambda: clock["t"]
    calls = {"n": 0}

    def fetch(parts):
        calls["n"] += 1
        payload = {"n": calls["n"]}
        return {part: [{"symbol": "X", "n": calls["n"]}] if part == "positions" else payload for part in parts}, {}

    snapshot.cache.value("positions", ttl=5, fetch=fetch)
    snapshot.invalidate("positions", "open_orders", "account")
    assert snapshot.peek("positions")[0]["symbol"] == "X"
    refreshed = snapshot.cache.value("positions", ttl=5, fetch=fetch, fresh=True)
    assert calls["n"] == 2
    assert refreshed[0]["n"] == 2


def test_exit_sync_and_submit_request_a_fresh_position_snapshot(monkeypatch):
    seen = []

    def fake(name, fresh=False):
        seen.append((name, fresh))
        return [{"symbol": "NVDA", "qty": "1", "asset_class": "us_option"}]

    monkeypatch.setattr("app.broker.snapshot.read_shared", fake)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            return httpx.Response(200, json={"id": "ord", "symbol": "NVDA", "qty": "1", "status": "new", "position_intent": "sell_to_close"})
        return httpx.Response(200, json=[])

    client = AlpacaPaperClient("PKPAPERKEY1234", "secret-value-9999", "PAPER", httpx.MockTransport(handler), shared_cache=True)
    adapter = AlpacaAdapter(client)
    adapter.sync_positions()
    adapter.submit_exit_order({"symbol": "NVDA", "qty": "1", "position_intent": "sell_to_close", "limit_price": "1"})
    assert ("positions", True) in seen
    assert posts["n"] == 1


def test_ttl_is_clamped_to_five_through_ten_seconds():
    assert Settings(broker_snapshot_ttl_seconds=1).broker_snapshot_ttl() == 5
    assert Settings(broker_snapshot_ttl_seconds=7.5).broker_snapshot_ttl() == 7.5
    assert Settings(broker_snapshot_ttl_seconds=30).broker_snapshot_ttl() == 10


def test_stream_builder_reads_the_snapshot_without_opening_the_broker(monkeypatch):
    from app.live import stream as live

    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("alpaca")))
    snapshot.prime({"positions": [{"symbol": "QQQ", "underlying": "QQQ", "current_price": "2"}], "account": {"equity": "10", "cash": "1", "last_equity": "9"}})
    frame = live.build_snapshot(Settings(auth_required=False, openai_price_input_per_million=0, openai_price_output_per_million=0))
    assert frame["positions"]["items"][0]["underlying"] == "QQQ"
    assert frame["account"]["equity"] == "10"


def test_retry_after_is_honored_and_capped(monkeypatch):
    slept = _pauses(monkeypatch)
    state = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < READ_ATTEMPTS:
            return httpx.Response(429, headers={"Retry-After": "0.35"}, json={})
        return httpx.Response(200, json={"equity": "1"})

    account = _client(handler).account()
    assert account["equity"] == "1"
    assert state["n"] == READ_ATTEMPTS
    assert slept == [0.35, 0.35]

    slept.clear()
    state["n"] = 0

    def capped(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "30"}, json={})

    with pytest.raises(httpx.HTTPStatusError):
        _client(capped).account()
    assert state["n"] == READ_ATTEMPTS
    assert slept == [8.0, 8.0]


def test_transient_5xx_and_timeouts_retry_with_capped_backoff(monkeypatch):
    slept = _pauses(monkeypatch)
    monkeypatch.setattr("app.integrations.alpaca.random.uniform", lambda _start, _end: 0)
    state = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(502, json={})
        return httpx.Response(200, json={"is_open": True})

    clock = _client(handler).clock()
    assert clock["is_open"] is True
    assert state["n"] == 2
    assert slept == [0.2]

    slept.clear()
    state["n"] = 0

    def slow(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.TimeoutException("slow")
        return httpx.Response(200, json=[])

    assert _client(slow).positions() == []
    assert state["n"] == 2
    assert slept == [0.2]


def test_order_post_is_not_retried_without_a_client_order_id(monkeypatch):
    _pauses(monkeypatch)
    posts = {"n": 0}
    gets = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            return httpx.Response(503, json={"message": "down"})
        if request.method == "DELETE":
            posts["n"] += 1
            return httpx.Response(503, json={"message": "down"})
        gets["n"] += 1
        return httpx.Response(404, json={})

    client = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.submit({"symbol": "A", "qty": "1", "side": "buy", "type": "limit", "time_in_force": "day", "limit_price": "1"})
    with pytest.raises(httpx.HTTPStatusError):
        client.cancel("ord-1")
    assert posts["n"] == 2
    assert gets["n"] == 0


def test_order_post_retries_only_after_the_client_order_is_confirmed_missing(monkeypatch):
    _pauses(monkeypatch)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            if posts["n"] == 1:
                return httpx.Response(502, json={"message": "down"})
            return httpx.Response(200, json={"id": "ord-2", "client_order_id": "cid-1", "status": "new"})
        assert "by_client_order_id" in str(request.url)
        return httpx.Response(404, json={"message": "missing"})

    body = {"symbol": "A", "qty": "1", "side": "buy", "type": "limit", "client_order_id": "cid-1"}
    order = _client(handler).submit(body)
    assert posts["n"] == 2
    assert order["id"] == "ord-2"


def test_order_post_does_not_post_again_when_the_client_order_exists(monkeypatch):
    _pauses(monkeypatch)
    posts = {"n": 0}
    lookups = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            return httpx.Response(504, json={"message": "gateway"})
        lookups["n"] += 1
        return httpx.Response(200, json={"id": "already", "client_order_id": "cid-9", "status": "accepted"})

    order = _client(handler).submit({"symbol": "A", "qty": "1", "client_order_id": "cid-9"})
    assert posts["n"] == 1
    assert lookups["n"] == 1
    assert order["id"] == "already"


def test_order_post_does_not_retry_when_the_existence_check_fails(monkeypatch):
    _pauses(monkeypatch)
    posts = {"n": 0}
    lookups = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts["n"] += 1
            return httpx.Response(503, json={"message": "down"})
        lookups["n"] += 1
        return httpx.Response(503, json={"message": "down"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).submit({"symbol": "A", "qty": "1", "client_order_id": "cid-x"})
    assert posts["n"] == 1
    assert lookups["n"] == READ_ATTEMPTS


def test_client_error_is_not_retried_for_a_read_or_an_order(monkeypatch):
    _pauses(monkeypatch)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(422, json={"message": "no"})

    client = _client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.account()
    with pytest.raises(httpx.HTTPStatusError):
        client.submit({"symbol": "A", "qty": "1", "client_order_id": "cid"})
    assert calls["n"] == 2


def test_counters_record_calls_429_5xx_and_hit_ratio(monkeypatch):
    _pauses(monkeypatch)
    metrics.reset()
    state = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"equity": "5"})

    _client(handler).account()
    metrics.note_hit()
    metrics.note_hit()
    body = snapshot.public_broker_status()
    assert body["calls_per_minute"] == 2
    assert body["http_429"] == 1
    assert body["http_5xx"] == 0
    assert body["cache_hits"] >= 2
    assert body["cache_misses"] == 0
    assert body["cache_hit_ratio"] == 1.0
    assert body["snapshot_ttl_seconds"] == 5
    assert body["shared_cache"] == "file"
    metrics.note_miss()
    ratio = snapshot.public_broker_status()["cache_hit_ratio"]
    assert ratio == round(body["cache_hits"] / (body["cache_hits"] + 1), 4)


def test_system_endpoint_exposes_alpaca_counters(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    metrics.note_hit()
    metrics.note_miss()
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "status.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=0,
        openai_price_output_per_million=0,
    )
    with TestClient(create_app(settings, allow_open_api=True)) as client:
        body = client.get("/api/v1/system").json()
        health = client.get("/api/v1/health").json()
        text = client.get("/metrics").text
    assert body["alpaca"]["shared_cache"] == "file"
    assert body["alpaca"]["cache_hits"] >= 1
    assert body["alpaca"]["cache_misses"] >= 1
    assert "cache_hit_ratio" in body["alpaca"]
    assert health["alpaca"]["calls_per_minute"] == body["alpaca"]["calls_per_minute"]
    assert "beo_alpaca_calls_per_minute" in text
    assert "beo_alpaca_http_429" in text
    assert "beo_alpaca_http_5xx" in text
    assert "beo_alpaca_cache_hit_ratio" in text
