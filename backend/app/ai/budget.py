from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from app.config.settings import Settings


class BudgetMode(str, Enum):
    NORMAL = "normal"
    SOFT = "soft"
    CRITICAL = "critical"
    STOPPED = "stopped"
    UNPRICED = "unpriced"


@dataclass
class UsageEntry:
    process: str
    created_at: datetime
    cost: Decimal
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    model: str


@dataclass
class BudgetLedger:
    hard_limit: Decimal
    soft_budget: Decimal
    warning_budget: Decimal
    critical_budget: Decimal
    max_per_day: Decimal
    price_input: Decimal
    price_cached: Decimal
    price_output: Decimal
    max_input_tokens: int
    max_output_tokens: int
    entries: list[UsageEntry] = field(default_factory=list)
    buy_count: int = 0

    @property
    def prices_configured(self) -> bool:
        return self.price_input > 0 and self.price_output > 0

    def max_call_cost(self) -> Decimal:
        if not self.prices_configured:
            return Decimal("0")
        incoming = Decimal(self.max_input_tokens) * self.price_input / Decimal("1000000")
        outgoing = Decimal(self.max_output_tokens) * self.price_output / Decimal("1000000")
        return incoming + outgoing

    def _start(self, moment: datetime, day_only: bool) -> datetime:
        local = moment.astimezone(ZoneInfo("America/New_York"))
        if day_only:
            start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start

    def total_since(self, moment: datetime, day_only: bool) -> Decimal:
        start = self._start(moment, day_only)
        return sum((entry.cost for entry in self.entries if entry.created_at >= start), Decimal("0"))

    def month_total(self, moment: datetime) -> Decimal:
        return self.total_since(moment, day_only=False)

    def day_total(self, moment: datetime) -> Decimal:
        return self.total_since(moment, day_only=True)

    def hour_total(self, moment: datetime) -> Decimal:
        threshold = moment.timestamp() - 3600
        return sum(
            (entry.cost for entry in self.entries if entry.created_at.timestamp() >= threshold),
            Decimal("0"),
        )

    def mode(self, moment: datetime) -> BudgetMode:
        if not self.prices_configured:
            return BudgetMode.UNPRICED
        month = self.month_total(moment)
        day = self.day_total(moment)
        reserve = self.max_call_cost()
        if month >= self.hard_limit or month + reserve > self.hard_limit:
            return BudgetMode.STOPPED
        if day + reserve > self.max_per_day:
            return BudgetMode.STOPPED
        if month >= self.critical_budget:
            return BudgetMode.CRITICAL
        if month >= self.soft_budget or month >= self.warning_budget:
            return BudgetMode.SOFT
        return BudgetMode.NORMAL

    def allow(self, process: str, moment: datetime) -> bool:
        current = self.mode(moment)
        if current in {BudgetMode.STOPPED, BudgetMode.UNPRICED}:
            return False
        if process != "decision" and current in {BudgetMode.SOFT, BudgetMode.CRITICAL}:
            return False
        return True

    def record(self, entry: UsageEntry) -> None:
        self.entries.append(entry)

    def cost_of(self, input_tokens: int, cached_tokens: int, output_tokens: int) -> Decimal:
        cached = min(cached_tokens, input_tokens)
        fresh = input_tokens - cached
        cached_price = self.price_cached if self.price_cached > 0 else self.price_input
        total = (
            Decimal(fresh) * self.price_input
            + Decimal(cached) * cached_price
            + Decimal(output_tokens) * self.price_output
        ) / Decimal("1000000")
        return total.quantize(Decimal("0.000001"))

    def average_per_buy(self, moment: datetime) -> Decimal | None:
        if self.buy_count <= 0:
            return None
        return (self.month_total(moment) / Decimal(self.buy_count)).quantize(Decimal("0.000001"))


def make_ledger(settings: Settings, entries: list[UsageEntry] | None = None) -> BudgetLedger:
    trading = settings.trading()
    return BudgetLedger(
        hard_limit=settings.ai_hard_limit,
        soft_budget=settings.ai_soft_budget,
        warning_budget=settings.ai_warning_budget,
        critical_budget=settings.ai_critical_budget,
        max_per_day=settings.max_ai_cost_per_day,
        price_input=settings.openai_price_input_per_million,
        price_cached=settings.openai_price_cached_input_per_million,
        price_output=settings.openai_price_output_per_million,
        max_input_tokens=trading.max_input_tokens,
        max_output_tokens=trading.max_output_tokens,
        entries=list(entries or []),
    )
