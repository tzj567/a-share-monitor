import numpy as np
import pandas as pd

from stock_monitor.indicators import add_indicators, rsi, warmup_requirements


def test_rsi_warmup_and_boundaries():
    increasing = pd.Series(np.arange(15, dtype=float))
    decreasing = pd.Series(np.arange(15, 0, -1, dtype=float))
    flat = pd.Series([10.0] * 15)
    assert rsi(increasing, 14).iloc[:14].isna().all()
    assert rsi(increasing, 14).iloc[-1] == 100.0
    assert rsi(decreasing, 14).iloc[-1] == 0.0
    assert rsi(flat, 14).iloc[-1] == 50.0


def test_volume_baseline_excludes_current_bar():
    frame = pd.DataFrame({"qfq_close": np.arange(21, dtype=float) + 10, "volume": [100.0] * 20 + [500.0]})
    result = add_indicators(
        frame,
        price_column="qfq_close",
        ma_fast=5,
        ma_slow=20,
        rsi_period=14,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        volume_window=20,
    )
    assert result.iloc[-1]["volume_baseline"] == 100.0
    assert result.iloc[-1]["volume_ratio"] == 5.0


def test_cross_warmup_requires_previous_computable_row():
    requirements = warmup_requirements(ma_slow=20, rsi_period=14, macd_slow=26, macd_signal=9, volume_window=20)
    assert requirements == {"ma_cross": 21, "rsi": 15, "macd_cross": 35, "volume_spike": 21}

