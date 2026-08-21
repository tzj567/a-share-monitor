from datetime import date
from unittest.mock import Mock, patch

from stock_monitor.config import DesktopConfig
from stock_monitor.data_sources.common import MarketDataBatch, ProviderError, daily_timestamp
from stock_monitor.models import Bar, WatchItem
from stock_monitor.storage import SQLiteRepository
from stock_monitor.sync import SyncService


def _bar(symbol: str) -> Bar:
    trading_date = date.today()
    return Bar(
        symbol=symbol,
        interval="1d",
        timestamp=daily_timestamp(trading_date),
        trading_date=trading_date,
        open=10,
        high=11,
        low=9,
        close=10,
        volume=100,
        is_closed=True,
        source="test",
    )


def test_sync_retries_and_isolates_failed_symbols(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    repo.save_watch_item(WatchItem(symbol="000001.SZ"))
    repo.save_watch_item(WatchItem(symbol="000002.SZ"))
    provider = Mock(name="provider")
    provider.name = "test"
    calls: dict[str, int] = {}

    def fetch(symbol, *_args):
        calls[symbol] = calls.get(symbol, 0) + 1
        if symbol == "000001.SZ" and calls[symbol] == 1:
            raise ProviderError("temporary")
        if symbol == "000002.SZ":
            raise ProviderError("down")
        return MarketDataBatch(bars=[_bar(symbol)])

    provider.fetch.side_effect = fetch
    config = DesktopConfig(retry_attempts=2, retry_backoff_seconds=0, auto_evaluate=False)
    service = SyncService(repo, config, Mock())
    with patch("stock_monitor.sync.build_market_provider", return_value=provider):
        summary = service.sync_market()

    assert summary.symbols == 1
    assert summary.bars == 1
    assert len(summary.errors) == 1
    assert calls == {"000001.SZ": 2, "000002.SZ": 2}


def test_news_sync_uses_cursor_enriches_and_deduplicates(tmp_path):
    from datetime import datetime, timezone
    from stock_monitor.models import NewsItem

    repo = SQLiteRepository(tmp_path / "monitor.db")
    repo.save_watch_item(WatchItem(symbol="000001.SZ", name="平安银行"))
    provider = Mock()
    provider.name = "财联社授权资讯接口"
    provider.fetch_news.return_value = [NewsItem(
        external_id="42", source="财联社", published_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        title="平安银行业绩预增", summary="净利润同比增长",
    )]
    service = SyncService(repo, DesktopConfig(cls_api_base_url="https://licensed.example", cls_news_endpoint="news"), Mock())
    with patch("stock_monitor.sync.build_news_provider", return_value=provider):
        first = service.sync_news()
        second = service.sync_news()
    assert first.news == 1
    assert second.news == 0
    assert repo.get_sync_cursor("cls", "news") == "42"
    assert repo.list_news()[0]["sentiment"] == "利好"
    assert provider.fetch_news.call_args_list[1].args[1] == "42"


def test_open_fund_flow_falls_back_to_watchlist_when_rankings_fail(tmp_path):
    from datetime import datetime, timezone
    from stock_monitor.models import FundFlowSnapshot

    repo = SQLiteRepository(tmp_path / "monitor.db")
    repo.save_watch_item(WatchItem(symbol="000001.SZ"))
    provider = Mock()
    provider.name = "AkShare/东方财富资金流"
    provider.fetch_stock_rank.side_effect = ProviderError("rank down")
    provider.fetch_sector_rank.side_effect = ProviderError("sector down")
    provider.fetch_watchlist.return_value = [FundFlowSnapshot(
        entity_type="stock", entity_code="000001.SZ", trading_date=date.today(),
        observed_at=datetime.now(timezone.utc), main_net_inflow=100, source="fallback", is_degraded=True,
    )]
    service = SyncService(repo, DesktopConfig(fund_flow_provider="akshare"), Mock())
    with patch("stock_monitor.sync.AkShareFundFlowProvider", return_value=provider):
        summary = service.sync_fund_flow()
    assert summary.fund_flows == 1
    provider.fetch_watchlist.assert_called_once_with(["000001.SZ"])
