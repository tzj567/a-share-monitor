"""Validated domain models shared by the API and storage layers."""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SYMBOL_PATTERN = re.compile(r"^[0-9A-Z][0-9A-Z._-]{0,31}$")
PriceBasis = Literal["raw", "qfq"]
NewsSentiment = Literal["利好", "利空", "中性"]
FundFlowEntity = Literal["stock", "sector"]


def _finite(value: float | None) -> bool:
    return value is None or math.isfinite(value)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Bar(StrictModel):
    symbol: str = Field(min_length=1, max_length=32)
    interval: str = Field(default="1m", min_length=1, max_length=16)
    timestamp: datetime
    trading_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    qfq_open: float | None = Field(default=None, gt=0)
    qfq_high: float | None = Field(default=None, gt=0)
    qfq_low: float | None = Field(default=None, gt=0)
    qfq_close: float | None = Field(default=None, gt=0)
    volume: float = Field(ge=0)
    is_closed: bool = False
    source: str = Field(default="manual", min_length=1, max_length=64)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        result = value.upper()
        if not _SYMBOL_PATTERN.fullmatch(result):
            raise ValueError("symbol contains unsupported characters")
        return result

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        return value.astimezone(timezone.utc)

    @field_validator("open", "high", "low", "close", "qfq_open", "qfq_high", "qfq_low", "qfq_close", "volume")
    @classmethod
    def require_finite(cls, value: float | None) -> float | None:
        if not _finite(value):
            raise ValueError("numeric values must be finite")
        return value

    @model_validator(mode="after")
    def validate_ranges_and_basis(self) -> "Bar":
        if self.timestamp.astimezone(ZoneInfo("Asia/Shanghai")).date() != self.trading_date:
            raise ValueError("trading_date must match timestamp in Asia/Shanghai")
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        if not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("raw open and close must be inside low/high")

        qfq = (self.qfq_open, self.qfq_high, self.qfq_low, self.qfq_close)
        if any(value is not None for value in qfq) and not all(value is not None for value in qfq):
            raise ValueError("qfq OHLC must be supplied as a complete set")
        if all(value is not None for value in qfq):
            assert self.qfq_low is not None and self.qfq_high is not None
            assert self.qfq_open is not None and self.qfq_close is not None
            if self.qfq_low > self.qfq_high:
                raise ValueError("qfq_low must not exceed qfq_high")
            if not self.qfq_low <= self.qfq_open <= self.qfq_high or not self.qfq_low <= self.qfq_close <= self.qfq_high:
                raise ValueError("qfq open and close must be inside qfq low/high")
        return self


class ReferenceClose(StrictModel):
    symbol: str = Field(min_length=1, max_length=32)
    trading_date: date
    previous_close: float = Field(gt=0)
    source: str = Field(default="manual", min_length=1, max_length=64)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        result = value.upper()
        if not _SYMBOL_PATTERN.fullmatch(result):
            raise ValueError("symbol contains unsupported characters")
        return result

    @field_validator("previous_close")
    @classmethod
    def require_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("previous_close must be finite")
        return value


class WatchItem(StrictModel):
    symbol: str = Field(min_length=1, max_length=32)
    name: str | None = Field(default=None, max_length=100)
    enabled: bool = True

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        result = value.upper()
        if not _SYMBOL_PATTERN.fullmatch(result):
            raise ValueError("symbol contains unsupported characters")
        return result


class NewsItem(StrictModel):
    external_id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=64)
    published_at: datetime
    title: str = Field(min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=5000)
    url: str | None = Field(default=None, max_length=2000)
    symbols: list[str] = Field(default_factory=list)
    sentiment: NewsSentiment = "中性"
    sentiment_score: float = Field(default=0.0, ge=-1, le=1)
    evidence: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=0.0, ge=0, le=1)

    @field_validator("published_at")
    @classmethod
    def normalize_published_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone offset")
        return value.astimezone(timezone.utc)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            symbol = value.strip().upper()
            if symbol and _SYMBOL_PATTERN.fullmatch(symbol) and symbol not in result:
                result.append(symbol)
        return result


class FundFlowSnapshot(StrictModel):
    """Canonical stock/sector capital-flow snapshot.

    Vendor percentages are normalized to fractions (8.5% -> 0.085) before
    entering this model. ``observed_at`` records when the vendor snapshot was
    observed, which is separate from its A-share trading date.
    """

    entity_type: FundFlowEntity
    entity_code: str = Field(min_length=1, max_length=64)
    entity_name: str = Field(default="", max_length=100)
    trading_date: date
    observed_at: datetime
    latest_price: float | None = Field(default=None, gt=0)
    change_pct: float | None = None
    main_net_inflow: float
    main_net_ratio: float | None = None
    super_large_net: float | None = None
    large_net: float | None = None
    medium_net: float | None = None
    small_net: float | None = None
    source: str = Field(min_length=1, max_length=64)
    is_degraded: bool = False

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone offset")
        return value.astimezone(timezone.utc)

    @field_validator(
        "latest_price", "change_pct", "main_net_inflow", "main_net_ratio",
        "super_large_net", "large_net", "medium_net", "small_net",
    )
    @classmethod
    def require_finite_flow_value(cls, value: float | None) -> float | None:
        if not _finite(value):
            raise ValueError("fund-flow numeric values must be finite")
        return value


class RuleConfig(StrictModel):
    indicator_basis: PriceBasis = "qfq"
    ma_fast: int = Field(default=5, ge=2, le=500)
    ma_slow: int = Field(default=20, ge=3, le=1000)
    rsi_period: int = Field(default=14, ge=2, le=500)
    macd_fast: int = Field(default=12, ge=2, le=500)
    macd_slow: int = Field(default=26, ge=3, le=1000)
    macd_signal: int = Field(default=9, ge=2, le=500)
    volume_window: int = Field(default=20, ge=2, le=1000)
    volume_ratio: float = Field(default=3.0, gt=0)
    pct_up: float | None = Field(default=0.05, ge=0)
    pct_down: float | None = Field(default=-0.05, le=0)
    rsi_overbought: float = Field(default=70.0, gt=50, le=100)
    rsi_oversold: float = Field(default=30.0, ge=0, lt=50)
    cooldown_seconds: int = Field(default=900, ge=0, le=604800)

    @model_validator(mode="after")
    def validate_periods(self) -> "RuleConfig":
        if self.ma_fast >= self.ma_slow:
            raise ValueError("ma_fast must be less than ma_slow")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast must be less than macd_slow")
        if self.rsi_oversold >= self.rsi_overbought:
            raise ValueError("rsi_oversold must be less than rsi_overbought")
        return self


class Alert(StrictModel):
    symbol: str
    interval: str
    bar_timestamp: datetime
    trading_date: date
    rule_id: str
    severity: Literal["info", "warning"] = "info"
    message: str
    value: float | None = None
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("bar_timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("bar_timestamp must include a timezone offset")
        return value.astimezone(timezone.utc)


class EvaluationResult(StrictModel):
    symbol: str
    interval: str
    bar_timestamp: datetime | None = None
    alerts: list[Alert] = Field(default_factory=list)
    skipped_rules: dict[str, str] = Field(default_factory=dict)
    diagnostics: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ResearchSignal(StrictModel):
    """Explainable multi-lane observation used by the research radar.

    ``score`` is a screening score, not an expected return or trade command.
    Every signal carries its evidence, uncertainty and review trigger so the
    UI never presents a model output as an unexplained recommendation.
    """

    symbol: str
    name: str = ""
    generated_at: datetime
    latest_trading_date: date | None = None
    latest_price: float | None = Field(default=None, gt=0)
    score: float = Field(ge=-100, le=100)
    confidence: float = Field(ge=0, le=1)
    state: str
    trend_score: float = Field(ge=-35, le=35)
    flow_score: float = Field(ge=-35, le=35)
    news_score: float = Field(ge=-25, le=25)
    risk_penalty: float = Field(ge=-25, le=0)
    risk_level: str
    main_net_inflow: float | None = None
    main_net_ratio: float | None = None
    flow_delta: float | None = None
    flow_observed_at: datetime | None = None
    positive_news: int = Field(default=0, ge=0)
    negative_news: int = Field(default=0, ge=0)
    evidence: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    review_triggers: list[str] = Field(default_factory=list)
    disclaimer: str = "仅作量化研究筛选，不构成投资建议或收益承诺。"

    @field_validator("generated_at", "flow_observed_at")
    @classmethod
    def normalize_signal_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("signal timestamps must include a timezone offset")
        return value.astimezone(timezone.utc)

    @field_validator(
        "latest_price", "score", "confidence", "trend_score", "flow_score",
        "news_score", "risk_penalty", "main_net_inflow", "main_net_ratio", "flow_delta",
    )
    @classmethod
    def require_finite_signal_value(cls, value: float | None) -> float | None:
        if not _finite(value):
            raise ValueError("research signal numeric values must be finite")
        return value
