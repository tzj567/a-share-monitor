"""Capital-flow adapters with normalized, vendor-labelled output."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from typing import Any

import pandas as pd

from stock_monitor.models import FundFlowSnapshot

from .common import ProviderError, SHANGHAI, normalize_symbol, numeric


def _optional_number(row: Any, column: str, *, percentage: bool = False) -> float | None:
    if column not in row or pd.isna(row[column]):
        return None
    value = numeric(row[column], column)
    return value / 100 if percentage else value


class AkShareFundFlowProvider:
    name = "AkShare/东方财富资金流"

    @staticmethod
    def _akshare() -> Any:
        try:
            import akshare as ak
        except ImportError as error:
            raise ProviderError('未安装 AkShare，请执行：python -m pip install -e ".[providers]"') from error
        return ak

    def test_connection(self) -> str:
        rows = self.fetch_stock_rank(limit=1)
        if not rows:
            raise ProviderError("资金流接口未返回数据")
        return f"{self.name} 连接正常"

    @staticmethod
    def _quiet_call(function: Any, /, *args: Any, **kwargs: Any) -> Any:
        """Keep provider progress bars away from GUI-only/pythonw streams."""
        sink = StringIO()
        with redirect_stdout(sink), redirect_stderr(sink):
            return function(*args, **kwargs)

    def fetch_stock_rank(self, limit: int = 100) -> list[FundFlowSnapshot]:
        try:
            frame = self._quiet_call(self._akshare().stock_individual_fund_flow_rank, indicator="今日")
        except Exception as error:
            raise ProviderError(f"AkShare 个股资金流获取失败：{error}") from error
        required = {"代码", "名称", "今日主力净流入-净额"}
        if not required.issubset(frame.columns):
            raise ProviderError(f"个股资金流缺少字段：{sorted(required - set(frame.columns))}")
        observed = datetime.now(SHANGHAI)
        result: list[FundFlowSnapshot] = []
        for _, row in frame.head(limit).iterrows():
            try:
                result.append(FundFlowSnapshot(
                    entity_type="stock",
                    entity_code=normalize_symbol(str(row["代码"])),
                    entity_name=str(row["名称"]),
                    trading_date=observed.date(),
                    observed_at=observed,
                    latest_price=_optional_number(row, "最新价"),
                    change_pct=_optional_number(row, "今日涨跌幅", percentage=True),
                    main_net_inflow=numeric(row["今日主力净流入-净额"], "今日主力净流入-净额"),
                    main_net_ratio=_optional_number(row, "今日主力净流入-净占比", percentage=True),
                    super_large_net=_optional_number(row, "今日超大单净流入-净额"),
                    large_net=_optional_number(row, "今日大单净流入-净额"),
                    medium_net=_optional_number(row, "今日中单净流入-净额"),
                    small_net=_optional_number(row, "今日小单净流入-净额"),
                    source="akshare_eastmoney",
                    is_degraded=True,
                ))
            except (ValueError, TypeError, ProviderError):
                continue
        return result

    def fetch_watchlist(self, symbols: list[str]) -> list[FundFlowSnapshot]:
        """Lower-volume fallback when the all-market ranking endpoint is blocked."""
        observed = datetime.now(SHANGHAI)
        result: list[FundFlowSnapshot] = []
        ak = self._akshare()
        for symbol in symbols:
            normalized = normalize_symbol(symbol)
            code, suffix = normalized.split(".", 1)
            try:
                frame = self._quiet_call(
                    ak.stock_individual_fund_flow,
                    stock=code,
                    market=suffix.lower(),
                )
            except Exception:
                continue
            if frame.empty:
                continue
            row = frame.iloc[-1]
            try:
                trading_date = pd.Timestamp(row["日期"]).date()
                result.append(FundFlowSnapshot(
                    entity_type="stock",
                    entity_code=normalized,
                    entity_name="",
                    trading_date=trading_date,
                    observed_at=observed,
                    latest_price=_optional_number(row, "收盘价"),
                    change_pct=_optional_number(row, "涨跌幅", percentage=True),
                    main_net_inflow=numeric(row["主力净流入-净额"], "主力净流入-净额"),
                    main_net_ratio=_optional_number(row, "主力净流入-净占比", percentage=True),
                    super_large_net=_optional_number(row, "超大单净流入-净额"),
                    large_net=_optional_number(row, "大单净流入-净额"),
                    medium_net=_optional_number(row, "中单净流入-净额"),
                    small_net=_optional_number(row, "小单净流入-净额"),
                    source="akshare_eastmoney",
                    is_degraded=True,
                ))
            except (ValueError, TypeError, ProviderError, KeyError):
                continue
        if not result:
            raise ProviderError("AkShare 观察列表资金流也未返回可用数据")
        return result

    def fetch_sector_rank(self, limit: int = 100) -> list[FundFlowSnapshot]:
        try:
            frame = self._quiet_call(
                self._akshare().stock_sector_fund_flow_rank,
                indicator="今日",
                sector_type="行业资金流",
            )
        except Exception as error:
            raise ProviderError(f"AkShare 板块资金流获取失败：{error}") from error
        required = {"名称", "今日主力净流入-净额"}
        if not required.issubset(frame.columns):
            raise ProviderError(f"板块资金流缺少字段：{sorted(required - set(frame.columns))}")
        observed = datetime.now(SHANGHAI)
        result: list[FundFlowSnapshot] = []
        for index, row in frame.head(limit).iterrows():
            name = str(row["名称"])
            result.append(FundFlowSnapshot(
                entity_type="sector",
                entity_code=f"SECTOR:{name or index}",
                entity_name=name,
                trading_date=observed.date(),
                observed_at=observed,
                change_pct=_optional_number(row, "今日涨跌幅", percentage=True),
                main_net_inflow=numeric(row["今日主力净流入-净额"], "今日主力净流入-净额"),
                main_net_ratio=_optional_number(row, "今日主力净流入-净占比", percentage=True),
                super_large_net=_optional_number(row, "今日超大单净流入-净额"),
                large_net=_optional_number(row, "今日大单净流入-净额"),
                medium_net=_optional_number(row, "今日中单净流入-净额"),
                small_net=_optional_number(row, "今日小单净流入-净额"),
                source="akshare_eastmoney",
                is_degraded=True,
            ))
        return result

    def fetch(self, stock_limit: int = 100, sector_limit: int = 100) -> Iterable[FundFlowSnapshot]:
        yield from self.fetch_stock_rank(stock_limit)
        yield from self.fetch_sector_rank(sector_limit)
