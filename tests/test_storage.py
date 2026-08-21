from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from stock_monitor.models import Alert, Bar, FundFlowSnapshot, NewsItem, ReferenceClose
from stock_monitor.storage import SQLiteRepository


def make_alert(timestamp: datetime) -> Alert:
    return Alert(
        symbol="000001.SZ",
        interval="1m",
        bar_timestamp=timestamp,
        trading_date=date(2026, 8, 12),
        rule_id="volume_spike",
        message="test",
    )


def test_timezone_normalization_prevents_duplicate_bars(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    common = dict(symbol="000001.SZ", trading_date=date(2026, 8, 12), open=10, high=11, low=9, close=10, volume=100, is_closed=True)
    repo.save_bar(Bar(timestamp="2026-08-12T10:00:00+08:00", **common))
    repo.save_bar(Bar(timestamp="2026-08-12T02:00:00Z", **common))
    assert len(repo.load_closed_bars("000001.SZ")) == 1


def test_reference_close_is_keyed_by_trading_date(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    repo.save_reference_close(ReferenceClose(symbol="000001.SZ", trading_date=date(2026, 8, 12), previous_close=9.8))
    assert repo.get_reference_close("000001.SZ", date(2026, 8, 12)) == 9.8
    assert repo.get_reference_close("000001.SZ", date(2026, 8, 11)) is None


def test_alert_dedup_is_atomic_under_concurrency(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    alert = make_alert(datetime(2026, 8, 12, 2, tzinfo=timezone.utc))
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: repo.insert_alert_if_new(alert, 900), range(10)))
    assert results.count(True) == 1
    assert repo.count_alerts() == 1


def test_cooldown_suppresses_then_allows_boundary(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    first = datetime(2026, 8, 12, 2, tzinfo=timezone.utc)
    assert repo.insert_alert_if_new(make_alert(first), 900)
    assert not repo.insert_alert_if_new(make_alert(first + timedelta(minutes=14)), 900)
    assert repo.insert_alert_if_new(make_alert(first + timedelta(minutes=15)), 900)


def test_market_batch_is_atomic(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    valid = Bar(
        symbol="000001.SZ", interval="1d", timestamp="2026-08-11T15:00:00+08:00",
        trading_date=date(2026, 8, 11), open=10, high=11, low=9, close=10,
        volume=100, is_closed=True, source="test",
    )
    invalid_reference = ReferenceClose(
        symbol="000002.SZ", trading_date=date(2026, 8, 12), previous_close=9.8, source="test",
    )
    # Force a database constraint failure after the bar upsert. Domain objects
    # remain valid, while the mismatched symbol is rejected by this test trigger.
    with repo._connect() as db:
        db.execute(
            """CREATE TRIGGER reject_mismatched_reference BEFORE INSERT ON reference_closes
            WHEN NEW.symbol='000002.SZ' BEGIN SELECT RAISE(ABORT, 'test failure'); END"""
        )
    try:
        repo.save_market_batch([valid], [invalid_reference])
    except Exception:
        pass
    else:
        raise AssertionError("expected injected database failure")
    assert repo.load_closed_bars("000001.SZ", "1d").empty


def test_provider_switch_replaces_canonical_series(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    common = dict(
        symbol="000001.SZ", interval="1d", trading_date=date(2026, 8, 12),
        open=10, high=11, low=9, close=10, volume=100, is_closed=True,
    )
    repo.save_market_batch([Bar(timestamp="2026-08-12T15:00:00+08:00", source="akshare", **common)], [])
    repo.save_market_batch([Bar(timestamp="2026-08-12T15:00:00+08:00", source="tushare", **common)], [])
    bars = repo.load_closed_bars("000001.SZ", "1d")
    assert len(bars) == 1
    assert bars.iloc[0]["source"] == "tushare"


def test_latest_dashboard_can_show_open_bar_but_analysis_loader_cannot(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    closed = Bar(
        symbol="000001.SZ", interval="1d", timestamp="2026-08-11T15:00:00+08:00",
        trading_date=date(2026, 8, 11), open=10, high=11, low=9, close=10,
        volume=100, is_closed=True, source="test",
    )
    open_bar = Bar(
        symbol="000001.SZ", interval="1d", timestamp="2026-08-12T15:00:00+08:00",
        trading_date=date(2026, 8, 12), open=10, high=12, low=9, close=11,
        volume=200, is_closed=False, source="test",
    )
    repo.save_market_batch([closed, open_bar], [])
    assert repo.list_latest_bars()[0]["is_closed"] == 0
    loaded = repo.load_closed_bars("000001.SZ", "1d")
    assert len(loaded) == 1
    assert loaded.iloc[0]["trading_date"] == date(2026, 8, 11)


def test_purge_symbol_removes_market_and_watch_state(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    repo.save_watch_item(__import__("stock_monitor.models", fromlist=["WatchItem"]).WatchItem(symbol="000001.SZ"))
    bar = Bar(
        symbol="000001.SZ", interval="1d", timestamp="2026-08-12T15:00:00+08:00",
        trading_date=date(2026, 8, 12), open=10, high=11, low=9, close=10,
        volume=100, is_closed=True, source="test",
    )
    repo.save_market_batch([bar], [])
    repo.purge_symbol("000001.SZ")
    assert repo.list_watchlist() == []
    assert repo.load_closed_bars("000001.SZ", "1d").empty


def test_news_upsert_updates_analysis_but_reports_only_first_as_new(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    common = dict(
        external_id="n1", source="财联社",
        published_at=datetime(2026, 8, 12, tzinfo=timezone.utc), title="测试资讯",
    )
    assert repo.save_news(NewsItem(**common))
    assert not repo.save_news(NewsItem(**common, sentiment="利好", evidence="测试", confidence=0.8, sentiment_score=0.8))
    stored = repo.list_news()[0]
    assert stored["sentiment"] == "利好"
    assert stored["evidence"] == "测试"


def test_fund_flow_upsert_and_ordering(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    observed = datetime(2026, 8, 12, 2, tzinfo=timezone.utc)
    rows = [
        FundFlowSnapshot(entity_type="stock", entity_code="000001.SZ", entity_name="甲", trading_date=date(2026, 8, 12), observed_at=observed, main_net_inflow=100, source="test"),
        FundFlowSnapshot(entity_type="stock", entity_code="000002.SZ", entity_name="乙", trading_date=date(2026, 8, 12), observed_at=observed, main_net_inflow=-200, source="test"),
    ]
    assert repo.save_fund_flows(rows) == 2
    assert repo.list_latest_fund_flows("stock", order="desc")[0]["entity_code"] == "000001.SZ"
    assert repo.list_latest_fund_flows("stock", order="asc")[0]["entity_code"] == "000002.SZ"


def test_fund_flow_ranking_never_mixes_old_snapshot(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    old = datetime(2026, 8, 12, 2, tzinfo=timezone.utc)
    new = old + timedelta(minutes=5)
    repo.save_fund_flows([
        FundFlowSnapshot(entity_type="stock", entity_code="000001.SZ", trading_date=date(2026, 8, 12), observed_at=old, main_net_inflow=9999, source="test"),
    ])
    repo.save_fund_flows([
        FundFlowSnapshot(entity_type="stock", entity_code="000002.SZ", trading_date=date(2026, 8, 12), observed_at=new, main_net_inflow=10, source="test"),
    ])
    latest = repo.list_latest_fund_flows("stock")
    assert [item["entity_code"] for item in latest] == ["000002.SZ"]


def test_fund_flow_history_returns_newest_first_for_one_entity(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    old = datetime(2026, 8, 12, 2, tzinfo=timezone.utc)
    new = old + timedelta(minutes=5)
    repo.save_fund_flows([
        FundFlowSnapshot(entity_type="stock", entity_code="000001.SZ", trading_date=date(2026, 8, 12), observed_at=old, main_net_inflow=10, source="test"),
        FundFlowSnapshot(entity_type="stock", entity_code="000001.SZ", trading_date=date(2026, 8, 12), observed_at=new, main_net_inflow=30, source="test"),
        FundFlowSnapshot(entity_type="stock", entity_code="000002.SZ", trading_date=date(2026, 8, 12), observed_at=new, main_net_inflow=999, source="test"),
    ])
    history = repo.list_fund_flow_history("stock", "000001.SZ", limit=2)
    assert [item["main_net_inflow"] for item in history] == [30, 10]


def test_source_health_opens_after_five_failures_and_recovers(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    for _ in range(5):
        repo.record_source_health("vendor", success=False, message="down")
    assert not repo.source_can_attempt("vendor", cooldown_seconds=60)
    assert repo.list_source_health()[0]["state"] == "熔断/停滞"
    repo.record_source_health("vendor", success=True, message="ok")
    assert repo.source_can_attempt("vendor")
    assert repo.list_source_health()[0]["state"] == "正常"


def test_sync_cursor_roundtrip(tmp_path):
    repo = SQLiteRepository(tmp_path / "monitor.db")
    assert repo.get_sync_cursor("cls", "news") is None
    repo.set_sync_cursor("cls", "news", "abc")
    assert repo.get_sync_cursor("cls", "news") == "abc"
