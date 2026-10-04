from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.schemas.domain import NewsItem, OptionSnapshot, UnderlyingSnapshot


def _latest_underlying(rows: list[UnderlyingSnapshot], as_of: datetime) -> list[UnderlyingSnapshot]:
    chosen: dict[str, UnderlyingSnapshot] = {}
    for row in rows:
        if row.observed_at > as_of:
            continue
        current = chosen.get(row.symbol)
        if current is None or row.observed_at > current.observed_at:
            bars = tuple(bar for bar in row.bars if bar.observed_at <= as_of)
            chosen[row.symbol] = row if bars == row.bars else replace(row, bars=bars)
    return list(chosen.values())


def _latest_option(rows: list[OptionSnapshot], as_of: datetime) -> list[OptionSnapshot]:
    chosen: dict[str, OptionSnapshot] = {}
    for row in rows:
        if row.observed_at > as_of:
            continue
        current = chosen.get(row.option_symbol)
        if current is None or row.observed_at > current.observed_at:
            chosen[row.option_symbol] = row
    return list(chosen.values())


class ReplayMarket:
    name = "replay"

    def __init__(self, rows: list[UnderlyingSnapshot]) -> None:
        self.rows = rows

    def load_underlyings(self, as_of: datetime) -> list[UnderlyingSnapshot]:
        return _latest_underlying(self.rows, as_of)


class ReplayOptions:
    name = "replay"

    def __init__(self, rows: list[OptionSnapshot]) -> None:
        self.rows = rows

    def load_options(self, as_of: datetime) -> list[OptionSnapshot]:
        return _latest_option(self.rows, as_of)


class ReplayNews:
    name = "replay"

    def __init__(self, rows: list[NewsItem]) -> None:
        self.rows = rows

    def load_news(self, as_of: datetime, symbols: tuple[str, ...] | None = None) -> list[NewsItem]:
        visible = []
        for row in self.rows:
            if row.published_at > as_of or row.observed_at > as_of:
                continue
            visible.append(row)
        return visible


