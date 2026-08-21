from datetime import date, datetime, timedelta, timezone

import pandas as pd

from stock_monitor.models import RuleConfig
from stock_monitor.rules import evaluate


def make_frame(count: int, *, latest_raw: float | None = None, include_qfq: bool = True, latest_closed: bool = True) -> pd.DataFrame:
    start = datetime(2026, 8, 12, 1, 30, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        close = 10 + index * 0.01
        rows.append(
            {
                "symbol": "000001.SZ",
                "interval": "1m",
                "timestamp": start + timedelta(minutes=index),
                "timestamp_ms": int((start + timedelta(minutes=index)).timestamp() * 1000),
                "trading_date": date(2026, 8, 12),
                "close": close,
                "qfq_close": close if include_qfq else None,
                "volume": 100.0,
                "is_closed": True,
            }
        )
    if latest_raw is not None:
        rows[-1]["close"] = latest_raw
    rows[-1]["is_closed"] = latest_closed
    return pd.DataFrame(rows)


def test_missing_qfq_skips_technical_rules_but_keeps_daily_change():
    result = evaluate("000001.SZ", "1m", make_frame(40, include_qfq=False, latest_raw=10.6), previous_close=10.0)
    assert {"ma_cross", "macd_cross", "rsi"} <= result.skipped_rules.keys()
    assert "daily_change_up" in {alert.rule_id for alert in result.alerts}


def test_daily_change_uses_reference_close_not_previous_minute():
    frame = make_frame(2, latest_raw=10.6)
    frame.loc[0, "close"] = 10.55
    result = evaluate("000001.SZ", "1m", frame, previous_close=10.0, config=RuleConfig(indicator_basis="raw"))
    daily = next(alert for alert in result.alerts if alert.rule_id == "daily_change_up")
    assert round(daily.value or 0, 6) == 0.06


def test_unclosed_bar_is_excluded_from_rules_and_volume_window():
    frame = make_frame(22)
    frame.loc[20, "volume"] = 100.0
    frame.loc[21, "volume"] = 1000000.0
    frame.loc[21, "close"] = 50.0
    frame.loc[21, "qfq_close"] = 50.0
    frame.loc[21, "is_closed"] = False
    result = evaluate("000001.SZ", "1m", frame, previous_close=10.0)
    assert result.bar_timestamp == frame.loc[20, "timestamp"]
    assert "volume_spike" not in {alert.rule_id for alert in result.alerts}
    assert result.diagnostics["closed_bars"] == 21


def test_warmup_returns_explicit_skip_reasons():
    result = evaluate("000001.SZ", "1m", make_frame(14), previous_close=10.0)
    assert "15" in result.skipped_rules["rsi"]
    assert "21" in result.skipped_rules["ma_cross"]
    assert "35" in result.skipped_rules["macd_cross"]


def test_missing_previous_close_never_falls_back_to_previous_bar():
    result = evaluate("000001.SZ", "1m", make_frame(2, latest_raw=20.0), previous_close=None, config=RuleConfig(indicator_basis="raw"))
    assert "daily_change" in result.skipped_rules
    assert not {"daily_change_up", "daily_change_down"} & {alert.rule_id for alert in result.alerts}

