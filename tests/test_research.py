from datetime import date, datetime, timedelta, timezone

from stock_monitor.models import Bar, FundFlowSnapshot, NewsItem, WatchItem
from stock_monitor.research import ResearchSignalEngine
from stock_monitor.storage import SQLiteRepository


def _seed_bars(repo: SQLiteRepository, symbol: str, prices: list[float]) -> None:
    start = date(2026, 5, 1)
    bars = []
    for index, price in enumerate(prices):
        trading_date = start + timedelta(days=index)
        bars.append(Bar(
            symbol=symbol,
            interval="1d",
            timestamp=datetime.combine(trading_date, datetime.min.time(), tzinfo=timezone(timedelta(hours=8))).replace(hour=15),
            trading_date=trading_date,
            open=price * 0.99,
            high=price * 1.01,
            low=price * 0.98,
            close=price,
            qfq_open=price * 0.99,
            qfq_high=price * 1.01,
            qfq_low=price * 0.98,
            qfq_close=price,
            volume=1_000_000 + index * 10_000,
            is_closed=True,
            source="test",
        ))
    repo.save_market_batch(bars, [])


def test_research_signal_combines_trend_flow_and_news_with_explanations(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    repo.save_watch_item(WatchItem(symbol="000001.SZ", name="测试银行"))
    _seed_bars(repo, "000001.SZ", [10 + index * 0.1 for index in range(60)])
    now = datetime(2026, 8, 12, 6, tzinfo=timezone.utc)
    repo.save_fund_flows([
        FundFlowSnapshot(
            entity_type="stock", entity_code="000001.SZ", entity_name="测试银行",
            trading_date=date(2026, 8, 12), observed_at=now - timedelta(minutes=5),
            main_net_inflow=120_000_000, main_net_ratio=0.06, source="licensed",
        ),
        FundFlowSnapshot(
            entity_type="stock", entity_code="000001.SZ", entity_name="测试银行",
            trading_date=date(2026, 8, 12), observed_at=now - timedelta(minutes=10),
            main_net_inflow=80_000_000, main_net_ratio=0.04, source="licensed",
        ),
    ])
    repo.save_news(NewsItem(
        external_id="n1", source="财联社", published_at=now - timedelta(hours=1),
        title="测试银行业绩预增", symbols=["000001.SZ"], sentiment="利好",
        sentiment_score=0.8, confidence=0.9, evidence="业绩预增",
    ))

    signal = ResearchSignalEngine(repo).rank_watchlist(now=now)[0]
    assert signal.score > 45
    assert signal.flow_delta == 40_000_000
    assert signal.positive_news == 1
    assert signal.evidence
    assert signal.review_triggers
    assert "买入" not in signal.disclaimer


def test_research_signal_surfaces_missing_data_instead_of_guessing(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    repo.save_watch_item(WatchItem(symbol="000002.SZ", name="空数据公司"))
    signal = ResearchSignalEngine(repo).rank_watchlist(now=datetime(2026, 8, 12, tzinfo=timezone.utc))[0]
    assert signal.state == "中性等待"
    assert signal.confidence == 0.05
    assert len(signal.uncertainties) >= 3
    assert signal.latest_price is None
