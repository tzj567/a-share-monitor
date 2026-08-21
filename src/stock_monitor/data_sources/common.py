"""Shared provider contracts and conversion helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from stock_monitor.models import Bar, NewsItem, ReferenceClose


SHANGHAI = ZoneInfo("Asia/Shanghai")
ProgressCallback = Callable[[str], None]


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class MarketDataBatch:
    bars: list[Bar] = field(default_factory=list)
    reference_closes: list[ReferenceClose] = field(default_factory=list)


class HistoricalMarketProvider(Protocol):
    name: str

    def test_connection(self) -> str: ...

    def fetch(self, symbol: str, start: date, end: date, interval: str = "1d") -> MarketDataBatch: ...


class NewsProvider(Protocol):
    name: str

    def test_connection(self) -> str: ...

    def fetch_news(self, limit: int = 100, cursor: str | None = None) -> Iterable[NewsItem]: ...


def daily_timestamp(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, 15, 0, tzinfo=SHANGHAI)


def is_daily_bar_closed(value: date, now: datetime | None = None) -> bool:
    """Treat today's daily candle as open until the A-share close at 15:00."""
    current = now or datetime.now(SHANGHAI)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(SHANGHAI)
    if value != current.date():
        return value < current.date()
    return current.time() >= time(15, 0)


def normalize_symbol(symbol: str) -> str:
    result = symbol.strip().upper()
    if result.endswith((".SZ", ".SH", ".BJ")):
        return result
    code = result.split(".")[0]
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def numeric(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ProviderError(f"字段 {field} 不是有效数字：{value!r}") from error
    return result


def volume_in_shares(value: object, field: str, unit: Literal["shares", "lots"]) -> float:
    """Normalize vendor volume to the repository's canonical unit: shares."""
    result = numeric(value, field)
    if result < 0:
        raise ProviderError(f"字段 {field} 不能为负数：{value!r}")
    return result * 100 if unit == "lots" else result
