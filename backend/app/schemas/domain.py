from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class OptionRight(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class DecisionKind(str, Enum):
    BUY = "BUY"
    SUPPRESS = "SUPPRESS"


REASON_CODES = (
    "NEWS_CATALYST",
    "MOMENTUM",
    "UNUSUAL_VOLUME",
    "LIQUIDITY",
    "IV_SETUP",
    "GAMMA_SETUP",
    "DELTA_SETUP",
    "SHORT_TERM_STRUCTURE",
    "EVENT_DRIVEN",
    "TECHNICAL_CONFIRMATION",
)

REASON_HEBREW = {
    "NEWS_CATALYST": "אירוע חדשותי",
    "MOMENTUM": "תנועה חזקה",
    "UNUSUAL_VOLUME": "נפח חריג",
    "LIQUIDITY": "נזילות טובה",
    "IV_SETUP": "תנאי תנודתיות מתאימים",
    "GAMMA_SETUP": "רגישות מחיר מתאימה למסחר קצר",
    "DELTA_SETUP": "דלתא בטווח שהוגדר",
    "SHORT_TERM_STRUCTURE": "מבנה פקיעה קצר",
    "EVENT_DRIVEN": "אירוע מהותי",
    "TECHNICAL_CONFIRMATION": "אישור טכני",
}

QUALITY_GATES = (
    "DATA_OK",
    "NEWS_OK",
    "LIQUIDITY_OK",
    "OPTIONS_OK",
    "GREEKS_OK",
    "IV_OK",
    "RISK_OK",
    "AI_OK",
    "SCENARIO_OK",
)

BANNED_LANGUAGE = (
    "בטוח",
    "מובטח",
    "סיכון אפס",
    "ודאי",
    "guaranteed",
    "risk-free",
    "risk free",
    "sure thing",
)


def ensure_aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"{label} timestamp must include a timezone")


@dataclass(frozen=True)
class Bar:
    observed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def __post_init__(self) -> None:
        ensure_aware(self.observed_at, "bar")


@dataclass(frozen=True)
class UnderlyingSnapshot:
    symbol: str
    price: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    relative_volume: Decimal
    change_percent: Decimal
    change_dollars: Decimal
    prior_close: Decimal
    observed_at: datetime
    session_ok: bool
    bars: tuple[Bar, ...] = ()

    def __post_init__(self) -> None:
        ensure_aware(self.observed_at, "underlying")


@dataclass(frozen=True)
class OptionSnapshot:
    underlying: str
    option_symbol: str
    right: OptionRight
    strike: Decimal
    expiration: date
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    open_interest: int
    observed_at: datetime
    implied_volatility: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None

    def __post_init__(self) -> None:
        ensure_aware(self.observed_at, "option")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


@dataclass(frozen=True)
class NewsItem:
    source: str
    url: str
    headline: str
    published_at: datetime
    symbols: tuple[str, ...]
    category: str
    event_type: str
    importance: int
    content: str
    source_credibility: Decimal
    observed_at: datetime
    article_id: str = ""
    author: str = ""
    source_host: str = ""
    channels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ensure_aware(self.published_at, "news published_at")
        ensure_aware(self.observed_at, "news observed_at")


@dataclass(frozen=True)
class ScenarioResult:
    move_percent: float
    underlying_price: Decimal
    option_price: Decimal
    change_dollars: Decimal
    change_percent: Decimal


@dataclass(frozen=True)
class RiskPlan:
    quantity: int
    max_entry_price: Decimal
    max_loss: Decimal
    capital_at_risk: Decimal
    spread_cost: Decimal
    slippage_estimate: Decimal
    holding_window_min: int
    holding_window_max: int


@dataclass
class ModelOutput:
    decision: str
    option_symbol: str
    call_put: str
    thesis: str
    catalyst: str
    risk: str
    invalidation: str
    reason_codes: list[str]
    raw: dict
    model: str
    prompt_version: str
    input_hash: str
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost: Decimal = Decimal("0")
    telemetry: dict = field(default_factory=dict)


@dataclass
class Recommendation:
    recommendation_id: str
    decision: DecisionKind
    timestamp: datetime
    underlying: str
    underlying_price: Decimal
    option_symbol: str
    call_put: OptionRight
    strike: Decimal
    expiration: date
    option_price: Decimal
    bid: Decimal
    ask: Decimal
    delta: Decimal
    gamma: Decimal
    theta: Decimal
    vega: Decimal
    iv: Decimal
    volume: int
    open_interest: int
    max_entry_price: Decimal
    quantity: int
    holding_window_min: int
    holding_window_max: int
    thesis: str
    catalyst: str
    risk: str
    invalidation: str
    reason_codes: list[str]
    gate_results: dict[str, bool]
    suppress_reason: str = ""
    scenarios: list[ScenarioResult] = field(default_factory=list)
    audit_hash: str = ""
    prompt_version: str = ""
    model: str = ""
    model_decision: str = ""
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_cost: Decimal = Decimal("0")
    policy_trace: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    exit_price: Decimal
    observed_at: datetime


@dataclass
class PaperFill:
    trade_id: str
    recommendation_id: str
    option_symbol: str
    quantity: int
    entry_price: Decimal
    entry_at: datetime
    exit_price: Decimal | None = None
    exit_at: datetime | None = None
    exit_reason: str = ""
    max_favorable: Decimal = Decimal("0")
    max_adverse: Decimal = Decimal("0")
    bid_at_entry: Decimal = Decimal("0")
    ask_at_entry: Decimal = Decimal("0")
    iv_at_entry: Decimal = Decimal("0")
    delta_at_entry: Decimal = Decimal("0")
    gamma_at_entry: Decimal = Decimal("0")
    theta_at_entry: Decimal = Decimal("0")
    vega_at_entry: Decimal = Decimal("0")
    underlying_at_entry: Decimal = Decimal("0")
