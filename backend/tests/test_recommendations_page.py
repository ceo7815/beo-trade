from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
from app.models.db import session_scope
from app.models.tables import RecommendationRow


def _row(row_id: str, moment: datetime, **overrides) -> RecommendationRow:
    payload = dict(
        id=row_id,
        decision="BUY",
        underlying="NVDA",
        underlying_price=100,
        option_symbol="NVDA251003C00100000",
        call_put="CALL",
        strike=100,
        expiration="2026-10-30",
        option_price=1.2,
        bid=1.1,
        ask=1.2,
        delta=0.4,
        gamma=0.01,
        theta=-0.02,
        vega=0.1,
        iv=0.5,
        volume=100,
        open_interest=80,
        max_entry_price=1.3,
        quantity=1,
        holding_window_min=5,
        holding_window_max=30,
        thesis="T" * 4000,
        catalyst="C" * 2000,
        risk="R" * 2000,
        invalidation="I" * 2000,
        reason_codes=["MOMENTUM"],
        gate_results={"DATA": True},
        scenarios=[{"move_percent": 1, "option_price": 1.4, "change_percent": 10}],
        quote_observed_at=moment,
        created_at=moment,
    )
    payload.update(overrides)
    return RecommendationRow(**payload)


def test_recommendations_are_paged_and_lean(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    database = tmp_path / "recs.db"
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + database.as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    base = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    with TestClient(create_app(settings, allow_open_api=True)) as client:
        with session_scope() as session:
            session.add(_row("rec-old", base - timedelta(minutes=2), underlying="OLD"))
            session.add(_row("rec-mid", base - timedelta(minutes=1), underlying="MID"))
            session.add(_row("rec-new", base, underlying="NEW", decision="SUPPRESS"))
            session.add(_row("rec-buy", base, underlying="BUY"))
            session.commit()
        first = client.get("/api/v1/recommendations", params={"limit": 2})
        assert first.status_code == 200
        body = first.json()
        assert [item["underlying"] for item in body["items"]] == ["BUY", "MID"]
        assert body["next_cursor"]
        assert body["limit"] == 2
        for item in body["items"]:
            assert "thesis" not in item
            assert "catalyst" not in item
            assert "gate_results" not in item
            assert "scenarios" not in item
            assert item["timestamp"].endswith("+00:00")
        assert len(first.text) < 20_000
        second = client.get("/api/v1/recommendations", params={"limit": 2, "cursor": body["next_cursor"]})
        assert second.status_code == 200
        rest = second.json()
        assert [item["underlying"] for item in rest["items"]] == ["OLD"]
        assert rest["next_cursor"] is None
        detail = client.get("/api/v1/recommendations/rec-buy")
        assert detail.status_code == 200
        assert detail.json()["thesis"].startswith("TTTT")
        assert client.get("/api/v1/recommendations", params={"cursor": "not-a-cursor"}).status_code == 422
