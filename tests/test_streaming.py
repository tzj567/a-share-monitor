from datetime import date, datetime, timezone

from stock_monitor.data_sources.common import daily_timestamp
from stock_monitor.models import Bar, FundFlowSnapshot, NewsItem
from stock_monitor.storage import SQLiteRepository
from stock_monitor.streaming import bar_envelope, fund_flow_envelope, news_envelope


def _bar(close: float = 10) -> Bar:
    trading_date = date(2026, 8, 12)
    return Bar(
        symbol="000001.SZ",
        interval="1d",
        timestamp=daily_timestamp(trading_date),
        trading_date=trading_date,
        open=10,
        high=11,
        low=9,
        close=close,
        volume=100,
        is_closed=True,
        source="test",
    )


def test_bar_event_is_deterministic_but_changes_with_payload():
    first = bar_envelope(_bar(10), "ashare.market.bar.v1")
    repeated = bar_envelope(_bar(10), "ashare.market.bar.v1")
    corrected = bar_envelope(_bar(10.5), "ashare.market.bar.v1")

    assert first.event_id == repeated.event_id
    assert corrected.event_id != first.event_id
    assert first.payload["event_time_ms"] == int(_bar().timestamp.timestamp() * 1000)
    assert first.payload["bar_tbname"].startswith("bar_000001_sz_1d_")


def test_market_save_and_outbox_enqueue_are_atomic(tmp_path):
    repository = SQLiteRepository(tmp_path / "monitor.db")
    bar = _bar()
    event = bar_envelope(bar, "ashare.market.bar.v1")

    repository.save_market_batch([bar], [], stream_events=[event])

    assert repository.count_pending_stream_events() == 1
    assert repository.list_pending_stream_events()[0].event_id == event.event_id
    repository.mark_stream_events_published([event.event_id])
    assert repository.count_pending_stream_events() == 0


def test_flow_and_news_contracts_have_tdengine_routing_tags():
    observed_at = datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)
    flow = FundFlowSnapshot(
        entity_type="stock",
        entity_code="000001.SZ",
        trading_date=date(2026, 8, 12),
        observed_at=observed_at,
        main_net_inflow=100,
        source="test",
    )
    news = NewsItem(
        external_id="42",
        source="财联社",
        published_at=observed_at,
        title="测试资讯",
    )

    assert fund_flow_envelope(flow, "ashare.fund-flow.v1").payload["flow_tbname"].startswith("flow_stock_000001_sz_")
    assert news_envelope(news, "ashare.news.v1").payload["news_tbname"].startswith("news_")
