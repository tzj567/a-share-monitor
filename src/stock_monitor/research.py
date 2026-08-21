"""Explainable multi-source research radar.

The design borrows the loose-coupling ideas used by mature quantitative
projects: normalized data remains independent from features, while this layer
only composes already validated market, fund-flow and news observations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

from .analysis import analyze_stock
from .models import ResearchSignal
from .storage import SQLiteRepository


_TREND_SCORES = {
    "趋势偏强": 30.0,
    "偏强但过热": 18.0,
    "震荡/信号不足": 0.0,
    "趋势偏弱": -30.0,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _state(score: float, risk_level: str) -> str:
    if risk_level == "高" and score > 0:
        return "偏强但风险较高"
    if score >= 45:
        return "多维共振偏强"
    if score >= 18:
        return "偏强观察"
    if score <= -45:
        return "多维共振偏弱"
    if score <= -18:
        return "偏弱观察"
    return "中性等待"


class ResearchSignalEngine:
    """Compose objective observations without producing buy/sell commands."""

    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def rank_watchlist(
        self,
        interval: str = "1d",
        *,
        limit: int = 100,
        news_window_hours: int = 72,
        now: datetime | None = None,
    ) -> list[ResearchSignal]:
        if limit < 1:
            raise ValueError("limit must be positive")
        generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        news_cutoff = generated_at - timedelta(hours=news_window_hours)
        all_news = self.repository.list_news(limit=1000)
        news_by_symbol: dict[str, list[dict]] = {}
        for item in all_news:
            try:
                published_at = datetime.fromisoformat(item["published_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if published_at < news_cutoff:
                continue
            for symbol in item.get("symbols", []):
                news_by_symbol.setdefault(str(symbol).upper(), []).append(item)

        signals = [
            self._build_signal(
                item["symbol"],
                item.get("name") or "",
                interval,
                news_by_symbol.get(item["symbol"].upper(), []),
                generated_at,
            )
            for item in self.repository.list_watchlist()
            if item["enabled"]
        ]
        signals.sort(key=lambda item: (item.score, item.confidence, item.symbol), reverse=True)
        return signals[:limit]

    def _build_signal(
        self,
        symbol: str,
        name: str,
        interval: str,
        news: list[dict],
        generated_at: datetime,
    ) -> ResearchSignal:
        bars = self.repository.load_closed_bars(symbol, interval, limit=250)
        flows = self.repository.list_fund_flow_history("stock", symbol, limit=2)
        evidence: list[str] = []
        uncertainties: list[str] = []
        review_triggers: list[str] = []
        trend_score = 0.0
        risk_penalty = 0.0
        risk_level = "数据不足"
        latest_trading_date = None
        latest_price = None
        basis_confidence = 0.0

        if bars.empty:
            uncertainties.append("缺少已闭合行情，趋势与波动指标未参与评分")
            review_triggers.append("获得至少 35 根已闭合 K 线后重新评估")
        else:
            latest_trading_date = bars.iloc[-1]["trading_date"]
            previous_close = self.repository.get_reference_close(symbol, latest_trading_date)
            insight = analyze_stock(bars, previous_close)
            latest_price = insight.latest_price
            risk_level = insight.risk_level
            trend_score = _TREND_SCORES.get(insight.status, 0.0)
            if insight.risk_level == "高":
                risk_penalty = -20.0
            elif insight.risk_level == "中":
                risk_penalty = -8.0
            evidence.extend(insight.evidence[:4])
            review_triggers.append(insight.invalidation)
            basis_confidence = 0.15 if insight.basis == "前复权" else 0.05
            if insight.basis != "前复权":
                uncertainties.append("缺少完整前复权序列，技术指标使用原始价格")
            if insight.risk_level == "数据不足":
                uncertainties.append("已闭合行情少于 35 根，趋势结论置信度受限")

        flow_score = 0.0
        main_net_inflow = None
        main_net_ratio = None
        flow_delta = None
        flow_observed_at = None
        if flows:
            latest_flow = flows[0]
            main_net_inflow = float(latest_flow["main_net_inflow"])
            main_net_ratio = latest_flow.get("main_net_ratio")
            main_net_ratio = None if main_net_ratio is None else float(main_net_ratio)
            flow_observed_at = datetime.fromisoformat(latest_flow["observed_at"])
            direction = 1.0 if main_net_inflow > 0 else -1.0 if main_net_inflow < 0 else 0.0
            flow_score = direction * 8.0
            if main_net_ratio is not None:
                flow_score = direction * _clamp(8.0 + abs(main_net_ratio) * 220.0, 8.0, 30.0)
                evidence.append(f"主力净流入占比 {main_net_ratio:+.2%}")
            else:
                evidence.append(f"主力净流入额 {main_net_inflow:+.0f} 元（缺少标准化占比）")
                uncertainties.append("资金流缺少流通市值标准化占比，金额不可直接跨股票比较")
            if len(flows) > 1:
                flow_delta = main_net_inflow - float(flows[1]["main_net_inflow"])
                if flow_delta != 0:
                    delta_direction = 1.0 if flow_delta > 0 else -1.0
                    flow_score += delta_direction * 5.0
                    evidence.append(f"较上一资金快照变化 {flow_delta:+.0f} 元")
            flow_score = _clamp(flow_score, -35.0, 35.0)
            age_hours = max(0.0, (generated_at - flow_observed_at).total_seconds() / 3600)
            if age_hours > 36:
                uncertainties.append(f"资金流快照已超过 {age_hours:.0f} 小时，可能不代表当前状态")
            if latest_flow.get("is_degraded"):
                uncertainties.append("资金流来自公开源降级路径，需用持牌行情复核")
            review_triggers.append("下一次资金流快照方向反转或净占比显著收敛时复核")
        else:
            uncertainties.append("缺少该股票的资金流快照")
            review_triggers.append("资金流数据接入后重新评估")

        news_score_raw = 0.0
        positive_news = 0
        negative_news = 0
        for item in news:
            sentiment = item.get("sentiment")
            confidence = float(item.get("confidence") or 0)
            sentiment_score = float(item.get("sentiment_score") or 0)
            if sentiment == "利好":
                positive_news += 1
            elif sentiment == "利空":
                negative_news += 1
            news_score_raw += sentiment_score * max(0.25, confidence)
        news_score = _clamp(news_score_raw * 12.5, -25.0, 25.0)
        if news:
            evidence.append(f"近 72 小时相关资讯：利好 {positive_news}、利空 {negative_news}、共 {len(news)} 条")
            review_triggers.append("出现公告、监管披露或业绩信息时，以原始披露重新核验")
        else:
            uncertainties.append("近 72 小时未匹配到该股票的授权资讯；不等同于没有事件")

        score = _clamp(trend_score + flow_score + news_score + risk_penalty, -100.0, 100.0)
        confidence = 0.0
        if not bars.empty:
            confidence += 0.35 + basis_confidence
        if flows:
            confidence += 0.25
        if news:
            confidence += 0.15
        if len(uncertainties) == 0:
            confidence += 0.10
        confidence = _clamp(confidence, 0.05, 0.95)

        # Normalize negative zero introduced by floating-point arithmetic.
        score = 0.0 if math.isclose(score, 0.0) else round(score, 2)
        return ResearchSignal(
            symbol=symbol,
            name=name,
            generated_at=generated_at,
            latest_trading_date=latest_trading_date,
            latest_price=latest_price,
            score=score,
            confidence=round(confidence, 2),
            state=_state(score, risk_level),
            trend_score=trend_score,
            flow_score=round(flow_score, 2),
            news_score=round(news_score, 2),
            risk_penalty=risk_penalty,
            risk_level=risk_level,
            main_net_inflow=main_net_inflow,
            main_net_ratio=main_net_ratio,
            flow_delta=flow_delta,
            flow_observed_at=flow_observed_at,
            positive_news=positive_news,
            negative_news=negative_news,
            evidence=evidence,
            uncertainties=uncertainties,
            review_triggers=list(dict.fromkeys(review_triggers)),
        )
