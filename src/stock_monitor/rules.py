"""Evaluate deterministic rules on the latest closed bar only."""

from __future__ import annotations

import math

import pandas as pd

from .indicators import add_indicators, warmup_requirements
from .models import Alert, EvaluationResult, RuleConfig


def _valid(*values: object) -> bool:
    return all(value is not None and pd.notna(value) and isinstance(value, (int, float)) and math.isfinite(float(value)) for value in values)


def evaluate(
    symbol: str,
    interval: str,
    bars: pd.DataFrame,
    *,
    previous_close: float | None,
    config: RuleConfig | None = None,
) -> EvaluationResult:
    config = config or RuleConfig()
    symbol = symbol.upper()
    if bars.empty:
        return EvaluationResult(symbol=symbol, interval=interval, skipped_rules={"all": "没有已闭合 K 线"}, diagnostics={"closed_bars": 0})

    closed = bars[bars["is_closed"].astype(bool)].copy() if "is_closed" in bars.columns else bars.copy()
    closed = closed.sort_values("timestamp_ms" if "timestamp_ms" in closed.columns else "timestamp").reset_index(drop=True)
    if closed.empty:
        return EvaluationResult(symbol=symbol, interval=interval, skipped_rules={"all": "没有已闭合 K 线"}, diagnostics={"closed_bars": 0})

    latest = closed.iloc[-1]
    timestamp = pd.Timestamp(latest["timestamp"]).to_pydatetime()
    trading_date = latest["trading_date"]
    result = EvaluationResult(
        symbol=symbol,
        interval=interval,
        bar_timestamp=timestamp,
        diagnostics={"closed_bars": len(closed), "indicator_basis": config.indicator_basis},
    )

    price_column = "close" if config.indicator_basis == "raw" else "qfq_close"
    if price_column not in closed or closed[price_column].isna().any():
        result.skipped_rules.update(
            {
                "ma_cross": f"{config.indicator_basis} 价格序列不完整",
                "macd_cross": f"{config.indicator_basis} 价格序列不完整",
                "rsi": f"{config.indicator_basis} 价格序列不完整",
            }
        )
        enriched = closed.copy()
        for column in ("ma_fast", "ma_slow", "macd_hist", "rsi"):
            enriched[column] = float("nan")
        baseline = enriched["volume"].shift(1).rolling(config.volume_window, min_periods=config.volume_window).mean()
        enriched["volume_baseline"] = baseline
        enriched["volume_ratio"] = enriched["volume"] / baseline
    else:
        enriched = add_indicators(
            closed,
            price_column=price_column,
            ma_fast=config.ma_fast,
            ma_slow=config.ma_slow,
            rsi_period=config.rsi_period,
            macd_fast=config.macd_fast,
            macd_slow=config.macd_slow,
            macd_signal=config.macd_signal,
            volume_window=config.volume_window,
        )

    latest = enriched.iloc[-1]
    previous = enriched.iloc[-2] if len(enriched) >= 2 else None
    requirements = warmup_requirements(
        ma_slow=config.ma_slow,
        rsi_period=config.rsi_period,
        macd_slow=config.macd_slow,
        macd_signal=config.macd_signal,
        volume_window=config.volume_window,
    )

    if "ma_cross" not in result.skipped_rules:
        if len(enriched) < requirements["ma_cross"]:
            result.skipped_rules["ma_cross"] = f"需要至少 {requirements['ma_cross']} 根已闭合 K 线"
        elif previous is not None and _valid(latest["ma_fast"], latest["ma_slow"], previous["ma_fast"], previous["ma_slow"]):
            if latest["ma_fast"] > latest["ma_slow"] and previous["ma_fast"] <= previous["ma_slow"]:
                result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="ma_golden_cross", message=f"MA{config.ma_fast} 上穿 MA{config.ma_slow}", value=float(latest["ma_fast"]), context={"basis": config.indicator_basis}))
            elif latest["ma_fast"] < latest["ma_slow"] and previous["ma_fast"] >= previous["ma_slow"]:
                result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="ma_death_cross", message=f"MA{config.ma_fast} 下穿 MA{config.ma_slow}", value=float(latest["ma_fast"]), context={"basis": config.indicator_basis}))

    if "macd_cross" not in result.skipped_rules:
        if len(enriched) < requirements["macd_cross"]:
            result.skipped_rules["macd_cross"] = f"需要至少 {requirements['macd_cross']} 根已闭合 K 线"
        elif previous is not None and _valid(latest["macd_hist"], previous["macd_hist"]):
            if latest["macd_hist"] > 0 >= previous["macd_hist"]:
                result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="macd_bullish_cross", message="MACD 柱由非正转正", value=float(latest["macd_hist"]), context={"basis": config.indicator_basis}))
            elif latest["macd_hist"] < 0 <= previous["macd_hist"]:
                result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="macd_bearish_cross", message="MACD 柱由非负转负", value=float(latest["macd_hist"]), context={"basis": config.indicator_basis}))

    if "rsi" not in result.skipped_rules:
        if len(enriched) < requirements["rsi"]:
            result.skipped_rules["rsi"] = f"需要至少 {requirements['rsi']} 根已闭合 K 线"
        elif _valid(latest["rsi"]):
            if latest["rsi"] >= config.rsi_overbought:
                result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="rsi_overbought", severity="warning", message=f"RSI{config.rsi_period} 达到超买阈值", value=float(latest["rsi"]), context={"basis": config.indicator_basis}))
            elif latest["rsi"] <= config.rsi_oversold:
                result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="rsi_oversold", severity="warning", message=f"RSI{config.rsi_period} 达到超卖阈值", value=float(latest["rsi"]), context={"basis": config.indicator_basis}))

    if len(enriched) < requirements["volume_spike"]:
        result.skipped_rules["volume_spike"] = f"需要至少 {requirements['volume_spike']} 根已闭合 K 线"
    elif _valid(latest["volume_baseline"], latest["volume_ratio"]) and latest["volume_baseline"] > 0 and latest["volume_ratio"] >= config.volume_ratio:
        result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="volume_spike", message=f"成交量达到前 {config.volume_window} 根均量的 {latest['volume_ratio']:.2f} 倍", value=float(latest["volume_ratio"])))
    elif _valid(latest["volume_baseline"]) and latest["volume_baseline"] == 0:
        result.skipped_rules["volume_spike"] = "历史成交量基线为零"

    if previous_close is None:
        result.skipped_rules["daily_change"] = "缺少该交易日的原始昨收"
    elif not math.isfinite(previous_close) or previous_close <= 0:
        result.skipped_rules["daily_change"] = "昨收无效"
    else:
        change = float(latest["close"] / previous_close - 1)
        result.diagnostics["daily_change"] = change
        result.diagnostics["previous_close"] = previous_close
        if config.pct_up is not None and change >= config.pct_up:
            result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="daily_change_up", message=f"相对昨收上涨 {change:.2%}", value=change, context={"basis": "raw", "previous_close": previous_close}))
        if config.pct_down is not None and change <= config.pct_down:
            result.alerts.append(Alert(symbol=symbol, interval=interval, bar_timestamp=timestamp, trading_date=trading_date, rule_id="daily_change_down", message=f"相对昨收下跌 {change:.2%}", value=change, context={"basis": "raw", "previous_close": previous_close}))
    return result

