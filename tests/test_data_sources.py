from datetime import date, datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_monitor.data_sources.akshare_source import AkShareProvider
from stock_monitor.data_sources.cls_source import CLSAuthorizedNewsProvider
from stock_monitor.data_sources.common import ProviderError, is_daily_bar_closed
from stock_monitor.data_sources.ifind_source import IFindProvider
from stock_monitor.data_sources.fund_flow_source import AkShareFundFlowProvider
from stock_monitor.data_sources.tushare_source import TuShareProvider
from stock_monitor.data_sources.common import volume_in_shares


def test_akshare_conversion_merges_raw_and_qfq_and_converts_lots():
    raw = pd.DataFrame([
        {"日期": "2026-08-10", "开盘": 9, "最高": 10, "最低": 8, "收盘": 9.5, "成交量": 10},
        {"日期": "2026-08-11", "开盘": 10, "最高": 11, "最低": 9, "收盘": 10.5, "成交量": 20},
    ])
    qfq = pd.DataFrame([
        {"日期": "2026-08-10", "开盘": 4.5, "最高": 5, "最低": 4, "收盘": 4.75, "成交量": 10},
        {"日期": "2026-08-11", "开盘": 5, "最高": 5.5, "最低": 4.5, "收盘": 5.25, "成交量": 20},
    ])
    batch = AkShareProvider()._convert("000001.SZ", raw, qfq, date(2026, 8, 11), date(2026, 8, 11))
    assert len(batch.bars) == 1
    assert batch.bars[0].qfq_close == 5.25
    assert batch.bars[0].volume == 2000
    assert batch.reference_closes[0].previous_close == 9.5


def test_tushare_conversion_uses_official_pre_close():
    raw = pd.DataFrame([{"trade_date": "20260811", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 20, "pre_close": 9.8}])
    qfq = pd.DataFrame([{"trade_date": "20260811", "open": 5, "high": 5.5, "low": 4.5, "close": 5.25}])
    batch = TuShareProvider("token")._convert("000001.SZ", raw, qfq)
    assert batch.bars[0].volume == 2000
    assert batch.reference_closes[0].previous_close == 9.8


def test_ifind_rows_supports_columnar_response():
    payload = {
        "tables": [{
            "thscode": "000001.SZ",
            "time": ["2026-08-11", "2026-08-12"],
            "table": {"open": [10, 11], "high": [11, 12], "low": [9, 10], "close": [10.5, 11.5], "volume": [100, 200]},
        }]
    }
    rows = IFindProvider._rows(payload, "000001.SZ")
    assert rows[1]["close"] == 11.5


def test_cls_authorized_news_requires_configuration_and_maps_epoch():
    with pytest.raises(ProviderError):
        CLSAuthorizedNewsProvider("", "", "")
    provider = CLSAuthorizedNewsProvider("https://licensed.example", "news", "token")
    provider._request = Mock(return_value={"data": [{"id": 1, "publish_time": 1786500000, "title": "测试资讯", "stocks": "000001.SZ"}]})
    item = list(provider.fetch_news())[0]
    assert item.source == "财联社"
    assert item.published_at.tzinfo is not None
    assert item.symbols == ["000001.SZ"]


def test_cls_authorized_news_maps_millisecond_epoch():
    provider = CLSAuthorizedNewsProvider("https://licensed.example", "news", "token")
    provider._request = Mock(return_value={"items": [{"id": 1, "publish_time": 1786500000000, "title": "测试资讯"}]})
    item = list(provider.fetch_news())[0]
    assert item.published_at.year == 2026


def test_ifind_token_error_is_reported_without_leaking_token():
    provider = IFindProvider("very-secret-token")
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"errorcode": -1, "errmsg": "denied"}
    with patch("stock_monitor.data_sources.ifind_source.requests.post", return_value=response):
        with pytest.raises(ProviderError) as caught:
            provider._access_token()
    assert "very-secret-token" not in str(caught.value)


def test_volume_units_are_explicitly_normalized_to_shares():
    assert volume_in_shares(12, "volume", "shares") == 12
    assert volume_in_shares(12, "volume", "lots") == 1200


def test_daily_bar_is_not_closed_before_market_close():
    shanghai = ZoneInfo("Asia/Shanghai")
    trading_date = date(2026, 8, 12)
    assert not is_daily_bar_closed(trading_date, datetime(2026, 8, 12, 14, 59, tzinfo=shanghai))
    assert is_daily_bar_closed(trading_date, datetime(2026, 8, 12, 15, 0, tzinfo=shanghai))


def test_akshare_fund_flow_normalizes_percentages_and_marks_fallback():
    frame = pd.DataFrame([{
        "代码": "000001", "名称": "平安银行", "最新价": 10.5, "今日涨跌幅": 2.5,
        "今日主力净流入-净额": 12_000_000, "今日主力净流入-净占比": 8.5,
        "今日超大单净流入-净额": 7_000_000, "今日大单净流入-净额": 5_000_000,
        "今日中单净流入-净额": -2_000_000, "今日小单净流入-净额": -10_000_000,
    }])
    provider = AkShareFundFlowProvider()
    fake_ak = Mock()
    fake_ak.stock_individual_fund_flow_rank.return_value = frame
    with patch.object(provider, "_akshare", return_value=fake_ak):
        item = provider.fetch_stock_rank()[0]
    assert item.entity_code == "000001.SZ"
    assert item.main_net_ratio == 0.085
    assert item.change_pct == 0.025
    assert item.is_degraded


def test_akshare_fund_flow_quiet_call_works_without_console_streams():
    def noisy() -> str:
        print("progress")
        return "ok"

    assert AkShareFundFlowProvider._quiet_call(noisy) == "ok"


def test_akshare_watchlist_fallback_maps_latest_daily_flow():
    frame = pd.DataFrame([{
        "日期": "2026-08-12", "收盘价": 10.5, "涨跌幅": 2.5,
        "主力净流入-净额": 12_000_000, "主力净流入-净占比": 8.5,
        "超大单净流入-净额": 7_000_000, "大单净流入-净额": 5_000_000,
        "中单净流入-净额": -2_000_000, "小单净流入-净额": -10_000_000,
    }])
    provider = AkShareFundFlowProvider()
    fake_ak = Mock()
    fake_ak.stock_individual_fund_flow.return_value = frame
    with patch.object(provider, "_akshare", return_value=fake_ak):
        item = provider.fetch_watchlist(["000001.SZ"])[0]
    assert item.entity_code == "000001.SZ"
    assert item.main_net_inflow == 12_000_000


def test_ifind_smart_rows_supports_columnar_table():
    payload = {"data": {"tables": [{"table": {"股票代码": ["000001.SZ"], "主力资金净流入": [100]}}]}}
    assert IFindProvider._smart_rows(payload) == [{"股票代码": "000001.SZ", "主力资金净流入": 100}]
