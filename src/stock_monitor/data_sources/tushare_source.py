"""TuShare Pro adapter using official SDK endpoints."""

from __future__ import annotations

from datetime import date

import pandas as pd

from stock_monitor.models import Bar, ReferenceClose

from .common import MarketDataBatch, ProviderError, daily_timestamp, is_daily_bar_closed, normalize_symbol, numeric, volume_in_shares


class TuShareProvider:
    name = "TuShare Pro"

    def __init__(self, token: str) -> None:
        if not token.strip():
            raise ProviderError("未配置 TuShare Token")
        self.token = token.strip()

    @staticmethod
    def _module():
        try:
            import tushare as ts
        except ImportError as error:
            raise ProviderError('未安装 TuShare，请执行：python -m pip install -e ".[providers]"') from error
        return ts

    def _client(self):
        ts = self._module()
        return ts, ts.pro_api(self.token)

    def test_connection(self) -> str:
        _, pro = self._client()
        today = date.today().strftime("%Y%m%d")
        try:
            pro.trade_cal(exchange="SSE", start_date=today, end_date=today, fields="exchange,cal_date,is_open")
        except Exception as error:
            raise ProviderError(f"TuShare 连接或权限验证失败：{error}") from error
        return "TuShare Token 与 API 连接正常"

    def fetch(self, symbol: str, start: date, end: date, interval: str = "1d") -> MarketDataBatch:
        if interval != "1d":
            raise ProviderError("TuShare 历史同步当前使用日线；实时分钟权限将在盘中同步模块中启用")
        ts, pro = self._client()
        normalized = normalize_symbol(symbol)
        arguments = {
            "api": pro,
            "ts_code": normalized,
            "asset": "E",
            "freq": "D",
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        }
        try:
            raw = ts.pro_bar(**arguments, adj=None)
            qfq = ts.pro_bar(**arguments, adj="qfq")
        except Exception as error:
            raise ProviderError(f"TuShare 获取 {normalized} 失败：{error}") from error
        return self._convert(normalized, raw, qfq)

    def _convert(self, symbol: str, raw: pd.DataFrame, qfq: pd.DataFrame) -> MarketDataBatch:
        required = {"trade_date", "open", "high", "low", "close", "vol", "pre_close"}
        if raw is None or raw.empty or not required.issubset(raw.columns):
            missing = required if raw is None else required - set(raw.columns)
            raise ProviderError(f"TuShare 未返回所需原始行情字段：{sorted(missing)}")
        if qfq is None or qfq.empty or not {"trade_date", "open", "high", "low", "close"}.issubset(qfq.columns):
            raise ProviderError("TuShare 未返回完整前复权行情；请检查 pro_bar 权限")
        qfq_rows = {str(row["trade_date"]): row for _, row in qfq.iterrows()}
        batch = MarketDataBatch()
        for _, raw_row in raw.sort_values("trade_date").iterrows():
            date_text = str(raw_row["trade_date"])
            qfq_row = qfq_rows.get(date_text)
            if qfq_row is None:
                continue
            trading_date = pd.Timestamp(date_text).date()
            batch.bars.append(
                Bar(
                    symbol=symbol,
                    interval="1d",
                    timestamp=daily_timestamp(trading_date),
                    trading_date=trading_date,
                    open=numeric(raw_row["open"], "open"),
                    high=numeric(raw_row["high"], "high"),
                    low=numeric(raw_row["low"], "low"),
                    close=numeric(raw_row["close"], "close"),
                    qfq_open=numeric(qfq_row["open"], "qfq_open"),
                    qfq_high=numeric(qfq_row["high"], "qfq_high"),
                    qfq_low=numeric(qfq_row["low"], "qfq_low"),
                    qfq_close=numeric(qfq_row["close"], "qfq_close"),
                    volume=volume_in_shares(raw_row["vol"], "vol", "lots"),
                    is_closed=is_daily_bar_closed(trading_date),
                    source="tushare",
                )
            )
            pre_close = numeric(raw_row["pre_close"], "pre_close")
            if pre_close > 0:
                batch.reference_closes.append(
                    ReferenceClose(symbol=symbol, trading_date=trading_date, previous_close=pre_close, source="tushare")
                )
        return batch
