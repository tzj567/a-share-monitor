from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from stock_monitor.models import Bar


def valid_bar(**overrides):
    values = {
        "symbol": "000001.sz",
        "timestamp": "2026-08-12T10:00:00+08:00",
        "trading_date": "2026-08-12",
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 100.0,
        "is_closed": True,
    }
    values.update(overrides)
    return Bar.model_validate(values)


def test_bar_normalizes_symbol_and_timezone():
    bar = valid_bar()
    assert bar.symbol == "000001.SZ"
    assert bar.timestamp == datetime(2026, 8, 12, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 8.0},
        {"close": 12.0},
        {"volume": -1.0},
        {"close": float("nan")},
        {"timestamp": "2026-08-12T10:00:00"},
        {"trading_date": "2026-08-11"},
        {"qfq_close": 10.5},
    ],
)
def test_invalid_bars_are_rejected(overrides):
    with pytest.raises(ValidationError):
        valid_bar(**overrides)


def test_qfq_ohlc_requires_valid_complete_range():
    bar = valid_bar(qfq_open=5, qfq_high=6, qfq_low=4, qfq_close=5.5)
    assert bar.qfq_close == 5.5
    with pytest.raises(ValidationError):
        valid_bar(qfq_open=5, qfq_high=4, qfq_low=6, qfq_close=5)

