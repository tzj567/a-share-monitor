from datetime import date, timedelta

import pandas as pd

from stock_monitor.analysis import analyze_stock


def make_bars(prices: list[float]) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for index, price in enumerate(prices):
        rows.append({
            "symbol": "000001.SZ",
            "timestamp": pd.Timestamp(start + timedelta(days=index), tz="Asia/Shanghai"),
            "timestamp_ms": index,
            "trading_date": start + timedelta(days=index),
            "open": price * 0.99,
            "high": price * 1.01,
            "low": price * 0.98,
            "close": price,
            "qfq_close": price,
            "volume": 1000 + index * 10,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_strong_trend_is_explainable_and_not_a_buy_order():
    insight = analyze_stock(make_bars([10 + index * 0.1 for index in range(60)]), previous_close=15.8)
    assert insight.status in {"趋势偏强", "偏强但过热"}
    assert insight.ma5 > insight.ma20
    assert "等待" in insight.suggestion
    assert "买入" not in insight.suggestion
    assert insight.invalidation


def test_weak_trend_prioritizes_risk_control():
    insight = analyze_stock(make_bars([20 - index * 0.1 for index in range(60)]), previous_close=14.2)
    assert insight.status == "趋势偏弱"
    assert "风险" in insight.suggestion


def test_short_history_is_marked_insufficient():
    insight = analyze_stock(make_bars([10 + index * 0.1 for index in range(20)]), previous_close=11.8)
    assert insight.risk_level == "数据不足"
    assert "至少积累 35 根" in insight.suggestion
