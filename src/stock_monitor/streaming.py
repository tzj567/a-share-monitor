"""Advanced-mode event contracts and Kafka publishing.

The desktop process always commits its local SQLite state first.  Canonical
events are placed in SQLite's outbox and are then delivered to Kafka with
deterministic event IDs, so a retry is safe for Flink/TDengine consumers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import Field

from .config import DesktopConfig
from .models import Bar, FundFlowSnapshot, NewsItem, StrictModel


SCHEMA_VERSION = "1.0"
_TABLE_PART = re.compile(r"[^a-z0-9_]+")


class StreamEnvelope(StrictModel):
    event_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    topic: str = Field(min_length=1, max_length=249)
    event_key: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any]

    def payload_json(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class StreamPublisher(Protocol):
    def publish(self, events: Iterable[StreamEnvelope]) -> None: ...

    def close(self) -> None: ...


def _event_id(event_type: str, *identity: object) -> str:
    canonical = "|".join((SCHEMA_VERSION, event_type, *(str(value) for value in identity)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _table_name(prefix: str, *parts: str) -> str:
    normalized = "_".join(_TABLE_PART.sub("_", part.lower()).strip("_") for part in parts)
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]
    readable = f"{prefix}_{normalized}"[:178].rstrip("_")
    return f"{readable}_{digest}"


def _base_payload(event_id: str, event_type: str, event_time: datetime) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "event_time": event_time.astimezone(timezone.utc).isoformat(),
        "event_time_ms": int(event_time.astimezone(timezone.utc).timestamp() * 1000),
        "ingest_time": now.isoformat(),
        "ingest_time_ms": int(now.timestamp() * 1000),
    }


def bar_envelope(bar: Bar, topic: str) -> StreamEnvelope:
    event_id = _event_id("market_bar", bar.model_dump_json(exclude_none=False))
    payload = _base_payload(event_id, "market_bar", bar.timestamp)
    payload.update(
        {
            "symbol": bar.symbol,
            "interval_code": bar.interval,
            "trading_date": bar.trading_date.isoformat(),
            "open_price": bar.open,
            "high_price": bar.high,
            "low_price": bar.low,
            "close_price": bar.close,
            "qfq_open": bar.qfq_open,
            "qfq_high": bar.qfq_high,
            "qfq_low": bar.qfq_low,
            "qfq_close": bar.qfq_close,
            "volume": bar.volume,
            "is_closed": bar.is_closed,
            "source": bar.source,
            "bar_tbname": _table_name("bar", bar.symbol, bar.interval),
            "activity_tbname": _table_name("activity", bar.symbol, bar.interval),
        }
    )
    return StreamEnvelope(event_id=event_id, topic=topic, event_key=bar.symbol, payload=payload)


def fund_flow_envelope(item: FundFlowSnapshot, topic: str) -> StreamEnvelope:
    event_id = _event_id("fund_flow", item.model_dump_json(exclude_none=False))
    payload = _base_payload(event_id, "fund_flow", item.observed_at)
    payload.update(
        {
            "entity_type": item.entity_type,
            "entity_code": item.entity_code,
            "entity_name": item.entity_name,
            "trading_date": item.trading_date.isoformat(),
            "latest_price": item.latest_price,
            "change_pct": item.change_pct,
            "main_net_inflow": item.main_net_inflow,
            "main_net_ratio": item.main_net_ratio,
            "super_large_net": item.super_large_net,
            "large_net": item.large_net,
            "medium_net": item.medium_net,
            "small_net": item.small_net,
            "source": item.source,
            "is_degraded": item.is_degraded,
            "flow_tbname": _table_name("flow", item.entity_type, item.entity_code),
        }
    )
    return StreamEnvelope(
        event_id=event_id,
        topic=topic,
        event_key=f"{item.entity_type}:{item.entity_code}",
        payload=payload,
    )


def news_envelope(item: NewsItem, topic: str) -> StreamEnvelope:
    event_id = _event_id("news", item.model_dump_json(exclude_none=False))
    payload = _base_payload(event_id, "news", item.published_at)
    payload.update(item.model_dump(mode="json"))
    payload["event_id"] = event_id
    payload["event_type"] = "news"
    payload["schema_version"] = SCHEMA_VERSION
    payload["news_tbname"] = _table_name("news", item.source)
    return StreamEnvelope(event_id=event_id, topic=topic, event_key=item.source, payload=payload)


class KafkaEventPublisher:
    """Small, optional Kafka client used only when advanced mode is enabled."""

    def __init__(self, bootstrap_servers: str, *, timeout_seconds: float = 10.0) -> None:
        try:
            from kafka import KafkaProducer
        except ImportError as error:
            raise RuntimeError("高级模式缺少 Kafka 客户端，请安装项目的 streaming 可选依赖") from error
        self.timeout_seconds = timeout_seconds
        self._producer = KafkaProducer(
            bootstrap_servers=[item.strip() for item in bootstrap_servers.split(",") if item.strip()],
            acks="all",
            retries=5,
            enable_idempotence=True,
            request_timeout_ms=int(timeout_seconds * 1000),
            api_version_auto_timeout_ms=int(timeout_seconds * 1000),
            max_block_ms=int(timeout_seconds * 1000),
        )

    def publish(self, events: Iterable[StreamEnvelope]) -> None:
        futures = [
            self._producer.send(
                event.topic,
                key=event.event_key.encode("utf-8"),
                value=event.payload_json().encode("utf-8"),
            )
            for event in events
        ]
        for future in futures:
            future.get(timeout=self.timeout_seconds)
        self._producer.flush(timeout=self.timeout_seconds)

    def close(self) -> None:
        self._producer.close(timeout=self.timeout_seconds)


def build_stream_publisher(config: DesktopConfig) -> StreamPublisher:
    return KafkaEventPublisher(config.kafka_bootstrap_servers)
