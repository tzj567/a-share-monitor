"""Explainable technical-state analysis for the desktop visualization.

The output is deliberately scenario guidance rather than a trade instruction.
It uses only closed bars and never claims a guaranteed return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

import pandas as pd

from .indicators import add_indicators


@dataclass(slots=True)
class StockInsight:
    symbol: str
    basis: str
    latest_date: str
    latest_price: float
    change_pct: float | None
    ma5: float | None
    ma20: float | None
    rsi14: float | None
    macd_hist: float | None
    volume_ratio: float | None
    annualized_volatility: float | None
    max_drawdown_60: float | None
    status: str
    risk_level: str
    suggestion: str
    invalidation: str
    evidence: list[str] = field(default_factory=list)


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def analyze_stock(bars: pd.DataFrame, previous_close: float | None = None) -> StockInsight:
    if bars.empty:
        raise ValueError("没有可分析的已闭合 K 线")
    frame = bars.sort_values("timestamp_ms" if "timestamp_ms" in bars else "timestamp").reset_index(drop=True).copy()
    if "is_closed" in frame:
        frame = frame[frame["is_closed"].astype(bool)].reset_index(drop=True)
    if frame.empty:
        raise ValueError("没有可分析的已闭合 K 线")

    qfq_complete = "qfq_close" in frame and not frame["qfq_close"].isna().any()
    basis = "前复权" if qfq_complete else "原始价格"
    price_column = "qfq_close" if qfq_complete else "close"
    enriched = add_indicators(
        frame,
        price_column=price_column,
        ma_fast=5,
        ma_slow=20,
        rsi_period=14,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        volume_window=20,
    )
    latest = enriched.iloc[-1]
    technical_price = _number(latest[price_column])
    raw_price = float(latest["close"])
    change_pct = None
    if previous_close is not None and math.isfinite(previous_close) and previous_close > 0:
        change_pct = raw_price / previous_close - 1

    ma5 = _number(latest["ma_fast"])
    ma20 = _number(latest["ma_slow"])
    rsi14 = _number(latest["rsi"])
    macd_hist = _number(latest["macd_hist"])
    volume_ratio = _number(latest["volume_ratio"])
    prices = enriched[price_column].astype(float)
    returns = prices.pct_change().dropna().tail(20)
    annualized_volatility = _number(returns.std(ddof=1) * math.sqrt(252)) if len(returns) >= 10 else None
    recent = prices.tail(60)
    max_drawdown = _number((recent / recent.cummax() - 1).min()) if len(recent) >= 2 else None

    score = 0
    evidence: list[str] = []
    if ma5 is not None and ma20 is not None:
        if ma5 > ma20:
            score += 1
            evidence.append("MA5 位于 MA20 上方，短期趋势偏强")
        else:
            score -= 1
            evidence.append("MA5 位于 MA20 下方，短期趋势偏弱")
    if technical_price is not None and ma20 is not None:
        if technical_price > ma20:
            score += 1
            evidence.append("价格位于 MA20 上方")
        else:
            score -= 1
            evidence.append("价格位于 MA20 下方")
    if macd_hist is not None:
        if macd_hist > 0:
            score += 1
            evidence.append("MACD 柱为正，动量偏多")
        elif macd_hist < 0:
            score -= 1
            evidence.append("MACD 柱为负，动量偏空")
    if rsi14 is not None:
        evidence.append(f"RSI14 为 {rsi14:.1f}")
    if volume_ratio is not None:
        evidence.append(f"成交量为前 20 日均量的 {volume_ratio:.2f} 倍")

    risk_flags: list[str] = []
    if rsi14 is not None and rsi14 >= 75:
        risk_flags.append("RSI 进入偏热区域")
    if annualized_volatility is not None and annualized_volatility >= 0.45:
        risk_flags.append("近 20 日波动率较高")
    if max_drawdown is not None and max_drawdown <= -0.15:
        risk_flags.append("近 60 日回撤超过 15%")
    if volume_ratio is not None and volume_ratio >= 3 and change_pct is not None and change_pct < 0:
        risk_flags.append("放量下跌需要警惕")

    if rsi14 is not None and rsi14 >= 75 and score >= 1:
        status = "偏强但过热"
        suggestion = "趋势仍偏强，但短线过热；等待回踩确认，避免追高，并预先设定风险退出条件。"
    elif score >= 2:
        status = "趋势偏强"
        suggestion = "保持观察，等待回踩企稳或放量突破确认；不要仅凭单一指标追涨。"
    elif score <= -2:
        status = "趋势偏弱"
        suggestion = "优先控制回撤与仓位风险；在趋势重新转强前保持谨慎。"
    else:
        status = "震荡/信号不足"
        suggestion = "方向尚不清晰，等待价格、均线和成交量形成一致确认。"

    if len(frame) < 35:
        risk_level = "数据不足"
        suggestion = "历史数据不足，当前只适合观察；至少积累 35 根已闭合 K 线后再综合判断。"
    elif len(risk_flags) >= 2:
        risk_level = "高"
    elif risk_flags:
        risk_level = "中"
    else:
        risk_level = "常规"
    evidence.extend(risk_flags)

    if ma20 is not None:
        invalidation = f"若{basis}收盘价有效跌破/收复 MA20（约 {ma20:.2f}），应重新评估当前判断。"
    else:
        invalidation = "均线样本不足；新增数据后重新评估。"

    return StockInsight(
        symbol=str(latest["symbol"]),
        basis=basis,
        latest_date=str(latest["trading_date"]),
        latest_price=raw_price,
        change_pct=change_pct,
        ma5=ma5,
        ma20=ma20,
        rsi14=rsi14,
        macd_hist=macd_hist,
        volume_ratio=volume_ratio,
        annualized_volatility=annualized_volatility,
        max_drawdown_60=max_drawdown,
        status=status,
        risk_level=risk_level,
        suggestion=suggestion,
        invalidation=invalidation,
        evidence=evidence,
    )
