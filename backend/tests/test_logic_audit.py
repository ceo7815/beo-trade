from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.ai.fingerprint import decision_fingerprint
from app.config.settings import TradingConfig
from app.options.selector import risk_plan
from app.recommendations.pipeline import run_scan
from app.schemas.domain import DecisionKind, ModelOutput, OptionRight, OptionSnapshot
from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market


def _option() -> OptionSnapshot:
    return OptionSnapshot(
        underlying="TEST",
        option_symbol="TEST261002C00100000",
        right=OptionRight.CALL,
        strike=Decimal("100"),
        expiration=AS_OF.date(),
        bid=Decimal("1.00"),
        ask=Decimal("1.10"),
        last=Decimal("1.05"),
        volume=800,
        open_interest=1500,
        observed_at=AS_OF,
        implied_volatility=Decimal("0.8"),
        delta=Decimal("0.45"),
        gamma=Decimal("0.02"),
        theta=Decimal("-0.05"),
        vega=Decimal("0.04"),
    )


def test_paper_default_uses_equity_percent_not_a_fixed_account():
    option = replace(_option(), bid=Decimal("1.08"), last=Decimal("1.09"))
    policy = TradingConfig()
    small = risk_plan(option, Decimal("1000"), policy)
    assert small.quantity == 0
    plan = risk_plan(option, Decimal("100000"), policy)
    premium = option.ask * 100
    by_risk = int((Decimal("100000") * Decimal("0.02")) // (premium * Decimal("0.40")))
    by_capital = int((Decimal("100000") * Decimal("0.07")) // premium)
    assert plan.quantity == min(by_risk, by_capital)
    assert plan.quantity * premium <= Decimal("7000")
    blocked = risk_plan(option, Decimal("100000"), policy, open_positions=10)
    assert blocked.quantity == 0


def test_one_scan_calls_ai_once_for_the_same_candidate():
    underlying, option, news = sample_market()
    StubAI.calls = 0
    run_scan(
        [underlying, underlying],
        [option],
        news,
        loose_config(),
        StubAI(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("5000"),
        set(),
    )
    assert StubAI.calls == 1


def test_unchanged_fingerprint_is_reused():
    underlying, option, news = sample_market()
    saved: dict[str, ModelOutput] = {}

    class Counting(StubAI):
        calls = 0

        def analyze(self, packet, as_of):
            Counting.calls += 1
            return StubAI().analyze(packet, as_of)

    def reuse(digest: str, moment: datetime):
        return saved.get(digest)

    config = loose_config()
    rows = run_scan(
        [underlying],
        [option],
        news,
        config,
        Counting(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("5000"),
        set(),
        reuse=reuse,
    )
    assert Counting.calls == 1
    assert rows[0].audit_hash
    saved[rows[0].audit_hash] = ModelOutput(
        decision="BUY",
        option_symbol=rows[0].option_symbol,
        call_put="CALL",
        thesis=rows[0].thesis,
        catalyst=rows[0].catalyst,
        risk=rows[0].risk,
        invalidation=rows[0].invalidation,
        reason_codes=list(rows[0].reason_codes),
        raw={"data_quality": "PASS"},
        model="cached",
        prompt_version=rows[0].prompt_version,
        input_hash=rows[0].audit_hash,
    )
    run_scan(
        [underlying],
        [option],
        news,
        config,
        Counting(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("5000"),
        set(),
        reuse=reuse,
    )
    assert Counting.calls == 1


def test_scan_call_cap_does_not_call_ai():
    underlying, option, news = sample_market()

    class Raising:
        def analyze(self, packet, as_of):
            raise AssertionError("AI must not be called")

    config = replace(loose_config(), ai_max_calls_per_scan=0)
    rows = run_scan(
        [underlying],
        [option],
        news,
        config,
        Raising(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("5000"),
        set(),
    )
    assert rows
    assert rows[0].decision is DecisionKind.SUPPRESS
    assert rows[0].suppress_reason == "AI_BUDGET_EXCEEDED"


def test_fingerprint_changes_when_the_quote_changes():
    first = decision_fingerprint({"underlying": {"symbol": "TEST", "price": 100}, "shortlist": [{"option_symbol": "X", "bid": 1, "ask": 1.1}]})
    second = decision_fingerprint({"underlying": {"symbol": "TEST", "price": 100}, "shortlist": [{"option_symbol": "X", "bid": 1, "ask": 1.4}]})
    assert first != second
