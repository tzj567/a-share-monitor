"""Causal indicator functions with explicit warm-up semantics."""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("period must be positive")
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("period must be positive")
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI.

    There are `period` price changes in the first valid window, so at least
    `period + 1` closes are required. A flat window is neutral (50), an all-gain
    window is 100, and an all-loss window is 0.
    """
    if period < 1:
        raise ValueError("period must be positive")
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    result = pd.Series(float("nan"), index=series.index, dtype="float64")
    normal = avg_loss > 0
    result.loc[normal] = 100 - 100 / (1 + avg_gain.loc[normal] / avg_loss.loc[normal])
    result.loc[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    result.loc[(avg_gain == 0) & (avg_loss > 0)] = 0.0
    result.loc[(avg_gain == 0) & (avg_loss == 0)] = 50.0
    return result


def add_indicators(
    frame: pd.DataFrame,
    *,
    price_column: str,
    ma_fast: int,
    ma_slow: int,
    rsi_period: int,
    macd_fast: int,
    macd_slow: int,
    macd_signal: int,
    volume_window: int,
) -> pd.DataFrame:
    required = {price_column, "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    result = frame.copy()
    price = result[price_column].astype("float64")
    result["ma_fast"] = sma(price, ma_fast)
    result["ma_slow"] = sma(price, ma_slow)
    result["ema_fast"] = ema(price, macd_fast)
    result["ema_slow"] = ema(price, macd_slow)
    result["macd_dif"] = result["ema_fast"] - result["ema_slow"]
    result["macd_signal"] = ema(result["macd_dif"], macd_signal)
    result["macd_hist"] = 2 * (result["macd_dif"] - result["macd_signal"])
    result["rsi"] = rsi(price, rsi_period)
    result["volume_baseline"] = result["volume"].shift(1).rolling(volume_window, min_periods=volume_window).mean()
    result["volume_ratio"] = result["volume"] / result["volume_baseline"]
    return result


def warmup_requirements(
    *,
    ma_slow: int,
    rsi_period: int,
    macd_slow: int,
    macd_signal: int,
    volume_window: int,
) -> dict[str, int]:
    return {
        "ma_cross": ma_slow + 1,
        "rsi": rsi_period + 1,
        "macd_cross": macd_slow + macd_signal,
        "volume_spike": volume_window + 1,
    }

