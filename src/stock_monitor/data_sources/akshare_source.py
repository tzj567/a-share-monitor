"""AkShare adapter using its documented A-share history interface."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from stock_monitor.models import Bar, ReferenceClose

from .common import MarketDataBatch, ProviderError, daily_timestamp, is_daily_bar_closed, normalize_symbol, numeric, volume_in_shares


class AkShareProvider:
    name = "AkShare"

    @staticmethod
    def _module():
        try:
            import akshare as ak
        except ImportError as error:
            raise ProviderError('未安装 AkShare，请执行：python -m pip install -e ".[providers]"') from error
        return ak

    def test_connection(self) -> str:
        ak = self._module()
        version = getattr(ak, "__version__", "unknown")
        return f"AkShare {version} 已安装；实际数据连通性将在同步时验证"

    def fetch(self, symbol: str, start: date, end: date, interval: str = "1d") -> MarketDataBatch:
        if interval != "1d":
            raise ProviderError("AkShare 桌面适配器当前稳定支持日线；分钟线请使用 TuShare/iFinD 授权接口")
        ak = self._module()
        normalized = normalize_symbol(symbol)
        code = normalized.split(".")[0]
        fetch_start = start - timedelta(days=14)
        arguments = {
            "symbol": code,
            "period": "daily",
            "start_date": fetch_start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        }
        try:
            raw = ak.stock_zh_a_hist(**arguments, adjust="")
            qfq = ak.stock_zh_a_hist(**arguments, adjust="qfq")
        except Exception as error:
            raise ProviderError(f"AkShare 获取 {normalized} 失败：{error}") from error
        return self._convert(normalized, raw, qfq, start, end)

    def _convert(self, symbol: str, raw: pd.DataFrame, qfq: pd.DataFrame, start: date, end: date) -> MarketDataBatch:
        required = {"日期", "开盘", "收盘", "最高", "最低", "成交量"}
        if raw.empty or not required.issubset(raw.columns):
            raise ProviderError(f"AkShare 未返回所需原始行情字段：{sorted(required - set(raw.columns))}")
        if qfq.empty or not required.issubset(qfq.columns):
            raise ProviderError("AkShare 未返回完整前复权行情")
        raw_rows = {pd.Timestamp(row["日期"]).date(): row for _, row in raw.iterrows()}
        qfq_rows = {pd.Timestamp(row["日期"]).date(): row for _, row in qfq.iterrows()}
        dates = sorted(raw_rows)
        batch = MarketDataBatch()
        previous_raw_close: float | None = None
        for trading_date in dates:
            raw_row = raw_rows[trading_date]
            qfq_row = qfq_rows.get(trading_date)
            if start <= trading_date <= end and qfq_row is not None:
                batch.bars.append(
                    Bar(
                        symbol=symbol,
                        interval="1d",
                        timestamp=daily_timestamp(trading_date),
                        trading_date=trading_date,
                        open=numeric(raw_row["开盘"], "开盘"),
                        high=numeric(raw_row["最高"], "最高"),
                        low=numeric(raw_row["最低"], "最低"),
                        close=numeric(raw_row["收盘"], "收盘"),
                        qfq_open=numeric(qfq_row["开盘"], "qfq开盘"),
                        qfq_high=numeric(qfq_row["最高"], "qfq最高"),
                        qfq_low=numeric(qfq_row["最低"], "qfq最低"),
                        qfq_close=numeric(qfq_row["收盘"], "qfq收盘"),
                        volume=volume_in_shares(raw_row["成交量"], "成交量", "lots"),
                        is_closed=is_daily_bar_closed(trading_date),
                        source="akshare",
                    )
                )
                if previous_raw_close is not None:
                    batch.reference_closes.append(
                        ReferenceClose(symbol=symbol, trading_date=trading_date, previous_close=previous_raw_close, source="akshare")
                    )
            previous_raw_close = numeric(raw_row["收盘"], "收盘")
        return batch
