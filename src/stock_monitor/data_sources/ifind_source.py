"""Licensed Tonghuashun iFinD QuantAPI adapter.

This adapter deliberately uses the documented HTTP API and requires the user's
own refresh token. It does not automate or scrape the Tonghuashun desktop app.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

import pandas as pd
import requests

from stock_monitor.models import Bar, FundFlowSnapshot, ReferenceClose

from .common import MarketDataBatch, ProviderError, daily_timestamp, is_daily_bar_closed, normalize_symbol, numeric, volume_in_shares
from .throttle import RequestThrottle


class IFindProvider:
    name = "同花顺 iFinD QuantAPI"

    def __init__(
        self,
        refresh_token: str,
        base_url: str = "https://quantapi.51ifind.com/api/v1",
        timeout: float = 30.0,
        volume_unit: Literal["shares", "lots"] = "shares",
        request_min_interval: float = 0.1,
        throttle: RequestThrottle | None = None,
    ) -> None:
        if not refresh_token.strip():
            raise ProviderError("未配置 iFinD refresh_token")
        self.refresh_token = refresh_token.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.volume_unit = volume_unit
        self._cached_access_token: str | None = None
        self._throttle = throttle or RequestThrottle(request_min_interval)

    def _access_token(self) -> str:
        try:
            self._throttle.wait()
            response = requests.post(
                f"{self.base_url}/get_access_token",
                headers={"Content-Type": "application/json", "refresh_token": self.refresh_token},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ProviderError(f"iFinD 获取 access_token 失败：{error}") from error
        token = ((payload.get("data") or {}).get("access_token") if isinstance(payload, dict) else None)
        if not token:
            raise ProviderError(f"iFinD 未返回 access_token：{payload}")
        return str(token)

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        token = self._cached_access_token or self._access_token()
        self._cached_access_token = token
        try:
            self._throttle.wait()
            response = requests.post(
                f"{self.base_url}/{endpoint.lstrip('/')}",
                headers={"Content-Type": "application/json", "access_token": token, "ifindlang": "cn"},
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ProviderError(f"iFinD 请求 {endpoint} 失败：{error}") from error
        if not isinstance(payload, dict):
            raise ProviderError("iFinD 返回格式不是 JSON 对象")
        error_code = payload.get("errorcode", payload.get("errorCode", 0))
        if error_code not in (0, "0", None):
            raise ProviderError(f"iFinD 返回错误 {error_code}：{payload.get('errmsg') or payload.get('message')}")
        return payload

    def test_connection(self) -> str:
        self._access_token()
        return "iFinD refresh_token 与 QuantAPI 连接正常"

    def fetch_realtime(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch one authorized iFinD real-time snapshot for each symbol."""
        normalized = [normalize_symbol(symbol) for symbol in symbols]
        if not normalized:
            return {}
        payload = self._post(
            "real_time_quotation",
            {
                "codes": ",".join(normalized),
                "indicators": "tradeDate,tradeTime,preClose,open,high,low,latest,amount,volume,changeRatio",
            },
        )
        tables = payload.get("tables") or (payload.get("data") or {}).get("tables")
        if not isinstance(tables, list):
            raise ProviderError("iFinD 实时行情响应中没有 tables")
        result: dict[str, dict[str, Any]] = {}
        for table in tables:
            if not isinstance(table, dict):
                continue
            code = str(table.get("thscode", table.get("code", ""))).upper()
            values = table.get("table", table.get("data", {}))
            if isinstance(values, dict):
                result[code] = {key: (value[-1] if isinstance(value, list) and value else value) for key, value in values.items()}
        return result

    def fetch_realtime_bar(self, symbol: str) -> MarketDataBatch:
        """Map the latest licensed quote into today's updateable daily bar."""
        normalized = normalize_symbol(symbol)
        row = self.fetch_realtime([normalized]).get(normalized)
        if not row:
            raise ProviderError(f"iFinD 实时行情未返回 {normalized}")
        raw_date = row.get("tradeDate", row.get("tradedate"))
        trading_date = pd.Timestamp(raw_date).date() if raw_date else pd.Timestamp.now(tz="Asia/Shanghai").date()
        latest = numeric(row.get("latest", row.get("close")), "latest")
        open_price = numeric(row.get("open", latest), "open")
        high = max(numeric(row.get("high", latest), "high"), open_price, latest)
        low = min(numeric(row.get("low", latest), "low"), open_price, latest)
        batch = MarketDataBatch(bars=[Bar(
            symbol=normalized,
            interval="1d",
            timestamp=daily_timestamp(trading_date),
            trading_date=trading_date,
            open=open_price,
            high=high,
            low=low,
            close=latest,
            volume=volume_in_shares(row.get("volume", 0), "volume", self.volume_unit),
            is_closed=is_daily_bar_closed(trading_date),
            source="ifind",
        )])
        pre_close = row.get("preClose", row.get("preclose"))
        if pre_close is not None and numeric(pre_close, "preClose") > 0:
            batch.reference_closes.append(ReferenceClose(
                symbol=normalized,
                trading_date=trading_date,
                previous_close=numeric(pre_close, "preClose"),
                source="ifind",
            ))
        return batch

    def fetch_fund_flow(self, limit: int = 100) -> list[FundFlowSnapshot]:
        """Use the documented iFinD WenCai endpoint for authorized flow ranking.

        WenCai result columns depend on the account and generated query. The
        parser accepts stable Chinese labels and returns an actionable mapping
        error when the contract exposes a different schema; no field semantics
        are guessed.
        """
        payload = self._post(
            "smart_stock_picking",
            {"searchstring": f"今日主力资金净流入排名前{limit}的A股", "searchtype": "stock"},
        )
        rows = self._smart_rows(payload)
        observed = pd.Timestamp.now(tz="Asia/Shanghai").to_pydatetime()
        result: list[FundFlowSnapshot] = []
        for row in rows[:limit]:
            lowered = {str(key).lower(): value for key, value in row.items()}
            code = self._pick(lowered, "股票代码", "证券代码", "代码", "thscode", "code")
            name = self._pick(lowered, "股票简称", "证券简称", "名称", "name")
            main = self._pick_fuzzy(lowered, ("主力", "净流入"), exclude=("占比", "比例"))
            ratio = self._pick_fuzzy(lowered, ("主力",), include_any=("占比", "比例"))
            if code is None or main is None:
                continue
            result.append(FundFlowSnapshot(
                entity_type="stock",
                entity_code=normalize_symbol(str(code)),
                entity_name=str(name or ""),
                trading_date=observed.date(),
                observed_at=observed,
                main_net_inflow=numeric(main, "iFinD 主力净流入"),
                main_net_ratio=(numeric(ratio, "iFinD 主力净占比") / 100) if ratio is not None else None,
                source="ifind_wencai",
                is_degraded=False,
            ))
        if not result:
            columns = sorted({str(key) for row in rows[:3] for key in row})
            raise ProviderError(f"iFinD 资金流响应无法映射；请用超级命令确认账号字段。返回列：{columns[:20]}")
        return result

    def fetch(self, symbol: str, start: date, end: date, interval: str = "1d") -> MarketDataBatch:
        if interval != "1d":
            raise ProviderError("iFinD 桌面适配器当前先接入日线；分钟接口可在授权范围内继续扩展")
        normalized = normalize_symbol(symbol)
        payload = self._post(
            "cmd_history_quotation",
            {
                "codes": normalized,
                "indicators": "open,high,low,close,preClose,volume",
                "startdate": start.isoformat(),
                "enddate": end.isoformat(),
                "functionpara": {"Interval": "D", "Fill": "Blank"},
            },
        )
        rows = self._rows(payload, normalized)
        batch = MarketDataBatch()
        for row in rows:
            trading_date = pd.Timestamp(row["time"]).date()
            batch.bars.append(
                Bar(
                    symbol=normalized,
                    interval="1d",
                    timestamp=daily_timestamp(trading_date),
                    trading_date=trading_date,
                    open=numeric(row["open"], "open"),
                    high=numeric(row["high"], "high"),
                    low=numeric(row["low"], "low"),
                    close=numeric(row["close"], "close"),
                    volume=volume_in_shares(row["volume"], "volume", self.volume_unit),
                    is_closed=is_daily_bar_closed(trading_date),
                    source="ifind",
                )
            )
            pre_close = row.get("preClose", row.get("preclose"))
            if pre_close is not None and numeric(pre_close, "preClose") > 0:
                batch.reference_closes.append(
                    ReferenceClose(symbol=normalized, trading_date=trading_date, previous_close=numeric(pre_close, "preClose"), source="ifind")
                )
        return batch

    @staticmethod
    def _rows(payload: dict[str, Any], symbol: str) -> list[dict[str, Any]]:
        tables = payload.get("tables") or (payload.get("data") or {}).get("tables")
        if not isinstance(tables, list) or not tables:
            raise ProviderError("iFinD 行情响应中没有 tables")
        selected = next((item for item in tables if str(item.get("thscode", item.get("code", ""))).upper() == symbol), tables[0])
        table = selected.get("table") or selected.get("data")
        times = selected.get("time") or selected.get("times")
        if isinstance(table, list):
            return table
        if not isinstance(table, dict):
            raise ProviderError("iFinD table 格式无法识别")
        if not isinstance(times, list):
            times = table.get("time")
        if not isinstance(times, list):
            raise ProviderError("iFinD 响应缺少时间序列")
        rows: list[dict[str, Any]] = []
        for index, timestamp in enumerate(times):
            row = {"time": timestamp}
            for field, values in table.items():
                if isinstance(values, list) and index < len(values):
                    row[field] = values[index]
            rows.append(row)
        required = {"time", "open", "high", "low", "close", "volume"}
        if rows and not required.issubset(rows[0]):
            raise ProviderError(f"iFinD 响应缺少字段：{sorted(required - set(rows[0]))}")
        return rows

    @staticmethod
    def _smart_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data: Any = payload.get("data", payload.get("tables", []))
        if isinstance(data, dict):
            data = data.get("tables", data.get("items", data.get("list", data)))
        if isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
            if len(data) == 1 and isinstance(data[0].get("table"), dict):
                table = data[0]["table"]
                lengths = [len(value) for value in table.values() if isinstance(value, list)]
                return [
                    {key: value[index] if isinstance(value, list) and index < len(value) else value for key, value in table.items()}
                    for index in range(max(lengths, default=0))
                ]
            return data
        raise ProviderError("iFinD 智能选股响应无法识别")

    @staticmethod
    def _pick(row: dict[str, Any], *names: str) -> Any:
        for name in names:
            if name.lower() in row:
                return row[name.lower()]
        return None

    @staticmethod
    def _pick_fuzzy(
        row: dict[str, Any], required: tuple[str, ...], *,
        include_any: tuple[str, ...] = (), exclude: tuple[str, ...] = (),
    ) -> Any:
        for key, value in row.items():
            if all(token in key for token in required) and (not include_any or any(token in key for token in include_any)) and not any(token in key for token in exclude):
                return value
        return None
