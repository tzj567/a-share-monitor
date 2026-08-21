"""Provider factory and repeatable desktop synchronization service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from time import sleep

import requests

from .config import DesktopConfig, SecretStore
from .data_sources import AkShareFundFlowProvider, AkShareProvider, CLSAuthorizedNewsProvider, IFindProvider, TuShareProvider
from .data_sources.common import HistoricalMarketProvider, MarketDataBatch, ProviderError
from .engine import MonitorEngine
from .models import EvaluationResult
from .news_analysis import enrich_news
from .providers import CSVReplayProvider
from .storage import SQLiteRepository
from .streaming import bar_envelope, build_stream_publisher, fund_flow_envelope, news_envelope


@dataclass(slots=True)
class SyncSummary:
    provider: str
    symbols: int = 0
    bars: int = 0
    reference_closes: int = 0
    alerts: int = 0
    news: int = 0
    fund_flows: int = 0
    stream_events: int = 0
    stream_pending: int = 0
    errors: list[str] = field(default_factory=list)


class CSVHistoricalProvider:
    name = "CSV"

    def __init__(self, bars_path: str, reference_closes_path: str = "", volume_unit: str = "shares") -> None:
        if not bars_path:
            raise ProviderError("请在设置中选择 CSV K 线文件")
        self.provider = CSVReplayProvider(bars_path, reference_closes_path or None, volume_unit=volume_unit)

    def test_connection(self) -> str:
        if not self.provider.bars_path.exists():
            raise ProviderError(f"CSV 文件不存在：{self.provider.bars_path}")
        return f"CSV 文件可读取：{self.provider.bars_path}"

    def fetch(self, symbol: str, start: date, end: date, interval: str = "1d") -> MarketDataBatch:
        bars = [bar for bar in self.provider.bars(symbol, interval) if start <= bar.trading_date <= end]
        closes = [value for value in self.provider.reference_closes(symbol) if start <= value.trading_date <= end]
        return MarketDataBatch(bars=bars, reference_closes=closes)


def build_market_provider(config: DesktopConfig, secrets: SecretStore) -> HistoricalMarketProvider:
    if config.provider == "csv":
        return CSVHistoricalProvider(config.csv_bars_path, config.csv_reference_closes_path, config.csv_volume_unit)
    if config.provider == "akshare":
        return AkShareProvider()
    if config.provider == "tushare":
        return TuShareProvider(secrets.get("tushare_token"))
    if config.provider == "ifind":
        return IFindProvider(
            secrets.get("ifind_refresh_token"),
            config.ifind_base_url,
            volume_unit=config.ifind_volume_unit,
        )
    raise ProviderError(f"不支持的数据源：{config.provider}")


def build_news_provider(config: DesktopConfig, secrets: SecretStore) -> CLSAuthorizedNewsProvider:
    return CLSAuthorizedNewsProvider(
        config.cls_api_base_url,
        config.cls_news_endpoint,
        secrets.get("cls_token"),
    )


class SyncService:
    def __init__(self, repository: SQLiteRepository, config: DesktopConfig, secrets: SecretStore) -> None:
        self.repository = repository
        self.config = config
        self.secrets = secrets

    def test_market_connection(self) -> str:
        return build_market_provider(self.config, self.secrets).test_connection()

    def test_news_connection(self) -> str:
        return build_news_provider(self.config, self.secrets).test_connection()

    def test_advanced_connection(self) -> str:
        publisher = None
        try:
            publisher = build_stream_publisher(self.config)
            tdengine_response = requests.post(
                f"{self.config.tdengine_rest_url.rstrip('/')}/rest/sql",
                data="SHOW DATABASES",
                auth=(self.config.tdengine_user, self.secrets.get("tdengine_password")),
                timeout=10,
            )
            tdengine_response.raise_for_status()
            payload = tdengine_response.json()
            if payload.get("code") != 0:
                raise ProviderError(f"TDengine 返回错误：{payload.get('desc') or payload}")
            flink_response = requests.get(
                f"{self.config.flink_dashboard_url.rstrip('/')}/overview",
                timeout=10,
            )
            flink_response.raise_for_status()
            overview = flink_response.json()
            running = overview.get("jobs-running", 0)
            return f"Kafka 可连接；TDengine 可查询；Flink 正在运行 {running} 个作业"
        finally:
            if publisher is not None:
                publisher.close()

    def _flush_advanced_outbox(self, summary: SyncSummary) -> None:
        if not self.config.advanced_mode:
            return
        pending = self.repository.list_pending_stream_events()
        if not pending:
            summary.stream_pending = 0
            return
        publisher = None
        try:
            publisher = build_stream_publisher(self.config)
            publisher.publish(pending)
            self.repository.mark_stream_events_published(event.event_id for event in pending)
            summary.stream_events += len(pending)
            summary.stream_pending = self.repository.count_pending_stream_events()
            self.repository.record_source_health(
                "advanced_stream",
                success=True,
                message=f"已投递 {len(pending)} 条事件，待发送 {summary.stream_pending} 条",
            )
        except Exception as error:
            self.repository.mark_stream_events_failed((event.event_id for event in pending), str(error))
            summary.stream_pending = self.repository.count_pending_stream_events()
            message = f"高级数据流暂不可用，SQLite 已保留 {summary.stream_pending} 条待发送事件：{error}"
            summary.errors.append(message)
            self.repository.record_source_health("advanced_stream", success=False, message=message)
        finally:
            if publisher is not None:
                publisher.close()

    def sync_market(self) -> SyncSummary:
        provider = build_market_provider(self.config, self.secrets)
        end = date.today()
        start = end - timedelta(days=self.config.sync_days)
        watchlist = [item for item in self.repository.list_watchlist() if item["enabled"]]
        if not watchlist:
            raise ProviderError("观察列表为空，请先添加股票")
        summary = SyncSummary(provider=provider.name)
        engine = MonitorEngine(self.repository)
        for item in watchlist:
            symbol = item["symbol"]
            symbol_start = start
            latest_date = self.repository.get_latest_bar_date(symbol, self.config.interval)
            latest_source = (self.repository.get_latest_bar_source(symbol, self.config.interval) or "").lower()
            if self.config.provider != "csv" and latest_date is not None and self.config.provider in latest_source:
                symbol_start = max(start, latest_date - timedelta(days=5))
            batch: MarketDataBatch | None = None
            last_error: Exception | None = None
            for attempt in range(1, self.config.retry_attempts + 1):
                try:
                    batch = provider.fetch(symbol, symbol_start, end, self.config.interval)
                    if isinstance(provider, IFindProvider) and self.config.interval == "1d":
                        realtime = provider.fetch_realtime_bar(symbol)
                        by_timestamp = {bar.timestamp: bar for bar in batch.bars}
                        by_timestamp.update({bar.timestamp: bar for bar in realtime.bars})
                        batch.bars = sorted(by_timestamp.values(), key=lambda bar: bar.timestamp)
                        references = {(item.symbol, item.trading_date): item for item in batch.reference_closes}
                        references.update({(item.symbol, item.trading_date): item for item in realtime.reference_closes})
                        batch.reference_closes = list(references.values())
                    break
                except Exception as error:
                    last_error = error
                    if attempt < self.config.retry_attempts:
                        sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))
            if batch is None:
                summary.errors.append(f"{symbol}: {last_error}")
                continue
            try:
                stream_events = (
                    [bar_envelope(bar, self.config.kafka_market_topic) for bar in batch.bars]
                    if self.config.advanced_mode else []
                )
                self.repository.save_market_batch(
                    batch.bars,
                    batch.reference_closes,
                    stream_events=stream_events,
                )
            except Exception as error:
                summary.errors.append(f"{symbol}: 写入失败：{error}")
                continue
            summary.symbols += 1
            summary.bars += len(batch.bars)
            summary.reference_closes += len(batch.reference_closes)
            if self.config.auto_evaluate:
                try:
                    result = engine.evaluate_symbol(symbol, self.config.interval)
                    summary.alerts += len(result.alerts)
                except Exception as error:
                    summary.errors.append(f"{symbol}: 规则评估失败：{error}")
        health_source = f"{self.config.provider}_market"
        if summary.symbols:
            self.repository.record_source_health(health_source, success=True, message=f"更新 {summary.symbols} 只股票")
        else:
            self.repository.record_source_health(health_source, success=False, message="；".join(summary.errors[:3]) or "没有行情更新")
        self._flush_advanced_outbox(summary)
        return summary

    def evaluate_all(self) -> list[EvaluationResult]:
        engine = MonitorEngine(self.repository)
        return [
            engine.evaluate_symbol(item["symbol"], self.config.interval)
            for item in self.repository.list_watchlist()
            if item["enabled"]
        ]

    def sync_news(self, limit: int = 100) -> SyncSummary:
        provider = build_news_provider(self.config, self.secrets)
        summary = SyncSummary(provider=provider.name)
        source_key = "cls_news"
        if not self.repository.source_can_attempt(source_key):
            raise ProviderError("财联社授权接口连续失败已暂时熔断，请稍后重试")
        cursor = self.repository.get_sync_cursor("cls", "news")
        latest_id = cursor
        latest_time = None
        try:
            watchlist = self.repository.list_watchlist()
            for item in provider.fetch_news(limit, cursor):
                enriched = enrich_news(item, watchlist)
                stream_event = (
                    news_envelope(enriched, self.config.kafka_news_topic)
                    if self.config.advanced_mode else None
                )
                if self.repository.save_news(enriched, stream_event=stream_event):
                    summary.news += 1
                if latest_time is None or enriched.published_at > latest_time:
                    latest_id = enriched.external_id
                    latest_time = enriched.published_at
            if latest_id:
                self.repository.set_sync_cursor("cls", "news", latest_id)
            self.repository.record_source_health(source_key, success=True, message=f"新增 {summary.news} 条", data_timestamp=latest_time)
        except Exception as error:
            self.repository.record_source_health(source_key, success=False, message=str(error))
            raise
        self._flush_advanced_outbox(summary)
        return summary

    def sync_fund_flow(self, limit: int = 100) -> SyncSummary:
        ifind_token = self.secrets.get("ifind_refresh_token")
        prefer_ifind = self.config.fund_flow_provider in {"auto", "ifind"} and bool(ifind_token)
        providers: list[tuple[str, object]] = []
        if prefer_ifind:
            providers.append(("ifind_fund_flow", IFindProvider(ifind_token, self.config.ifind_base_url, volume_unit=self.config.ifind_volume_unit)))
        if self.config.fund_flow_provider in {"auto", "akshare"}:
            providers.append(("akshare_fund_flow", AkShareFundFlowProvider()))
        if not providers:
            raise ProviderError("资金流选择了 iFinD，但尚未配置 refresh_token")

        errors: list[str] = []
        for source_key, provider in providers:
            if not self.repository.source_can_attempt(source_key):
                errors.append(f"{source_key}: 暂时熔断")
                continue
            try:
                if isinstance(provider, IFindProvider):
                    snapshots = provider.fetch_fund_flow(limit)
                else:
                    errors_for_open_source: list[str] = []
                    snapshots = []
                    try:
                        snapshots.extend(provider.fetch_stock_rank(limit))
                    except Exception as error:
                        errors_for_open_source.append(str(error))
                    try:
                        snapshots.extend(provider.fetch_sector_rank(50))
                    except Exception as error:
                        errors_for_open_source.append(str(error))
                    if not any(item.entity_type == "stock" for item in snapshots):
                        symbols = [item["symbol"] for item in self.repository.list_watchlist() if item["enabled"]]
                        if symbols:
                            snapshots.extend(provider.fetch_watchlist(symbols))
                    if not snapshots:
                        raise ProviderError("；".join(errors_for_open_source) or "公开资金流无可用数据")
                stream_events = (
                    [fund_flow_envelope(item, self.config.kafka_fund_flow_topic) for item in snapshots]
                    if self.config.advanced_mode else []
                )
                count = self.repository.save_fund_flows(snapshots, stream_events=stream_events)
                latest = max((item.observed_at for item in snapshots), default=None)
                self.repository.record_source_health(source_key, success=True, message=f"更新 {count} 条", data_timestamp=latest)
                summary = SyncSummary(provider=provider.name, fund_flows=count)
                self._flush_advanced_outbox(summary)
                return summary
            except Exception as error:
                errors.append(f"{source_key}: {error}")
                self.repository.record_source_health(source_key, success=False, message=str(error))
                if self.config.fund_flow_provider != "auto":
                    break
        raise ProviderError("；".join(errors) or "没有可用资金流数据源")

    def sync_radar(self, limit: int = 100) -> SyncSummary:
        combined = SyncSummary(provider="市场雷达")
        try:
            flow = self.sync_fund_flow(limit)
            combined.fund_flows = flow.fund_flows
            combined.provider = flow.provider
            combined.stream_events += flow.stream_events
            combined.stream_pending = flow.stream_pending
            combined.errors.extend(flow.errors)
        except Exception as error:
            combined.errors.append(f"资金流：{error}")
        if self.config.auto_sync_news and self.config.cls_api_base_url and self.config.cls_news_endpoint and self.secrets.get("cls_token"):
            try:
                news = self.sync_news(limit)
                combined.news = news.news
                combined.stream_events += news.stream_events
                combined.stream_pending = max(combined.stream_pending, news.stream_pending)
                combined.errors.extend(news.errors)
            except Exception as error:
                combined.errors.append(f"财联社：{error}")
        return combined
