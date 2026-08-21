"""SQLite persistence with normalized timestamps and atomic alert deduplication."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .models import Alert, Bar, FundFlowSnapshot, NewsItem, ReferenceClose, WatchItem
from .streaming import StreamEnvelope


def to_epoch_ms(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def from_epoch_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


class SQLiteRepository:
    def __init__(self, path: str | Path = "stock_monitor.db", *, timeout: float = 30.0) -> None:
        self.path = str(path)
        self.timeout = timeout
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    trading_date TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    qfq_open REAL,
                    qfq_high REAL,
                    qfq_low REAL,
                    qfq_close REAL,
                    volume REAL NOT NULL,
                    is_closed INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (symbol, interval, timestamp_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_bars_lookup
                    ON bars(symbol, interval, is_closed, timestamp_ms DESC);

                CREATE TABLE IF NOT EXISTS reference_closes (
                    symbol TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    previous_close REAL NOT NULL,
                    source TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (symbol, trading_date)
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    bar_timestamp_ms INTEGER NOT NULL,
                    trading_date TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    value REAL,
                    context_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    UNIQUE(symbol, interval, bar_timestamp_ms, rule_id)
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_cooldown
                    ON alerts(symbol, interval, rule_id, bar_timestamp_ms DESC);

                CREATE TABLE IF NOT EXISTS news (
                    external_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    published_at_ms INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    url TEXT,
                    symbols_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (source, external_id)
                );
                CREATE INDEX IF NOT EXISTS idx_news_published
                    ON news(published_at_ms DESC);

                CREATE TABLE IF NOT EXISTS fund_flows (
                    entity_type TEXT NOT NULL,
                    entity_code TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    latest_price REAL,
                    change_pct REAL,
                    main_net_inflow REAL NOT NULL,
                    main_net_ratio REAL,
                    super_large_net REAL,
                    large_net REAL,
                    medium_net REAL,
                    small_net REAL,
                    source TEXT NOT NULL,
                    is_degraded INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (entity_type, entity_code, observed_at_ms, source)
                );
                CREATE INDEX IF NOT EXISTS idx_fund_flow_latest
                    ON fund_flows(entity_type, observed_at_ms DESC);

                CREATE TABLE IF NOT EXISTS sync_cursors (
                    vendor TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    cursor_value TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (vendor, data_type)
                );

                CREATE TABLE IF NOT EXISTS source_health (
                    source TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    last_attempt_ms INTEGER NOT NULL,
                    last_success_ms INTEGER,
                    last_data_ms INTEGER,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_outbox (
                    event_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    published_at_ms INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_event_outbox_pending
                    ON event_outbox(published_at_ms, created_at_ms);
                """
            )
            self._ensure_column(db, "news", "sentiment", "TEXT NOT NULL DEFAULT '中性'")
            self._ensure_column(db, "news", "sentiment_score", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(db, "news", "evidence", "TEXT")
            self._ensure_column(db, "news", "confidence", "REAL NOT NULL DEFAULT 0")

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def save_watch_item(self, item: WatchItem) -> None:
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO watchlist(symbol, name, enabled, created_at_ms)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, enabled=excluded.enabled
                """,
                (item.symbol, item.name, int(item.enabled), now_ms),
            )

    def list_watchlist(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT symbol,name,enabled FROM watchlist ORDER BY symbol").fetchall()
        return [{"symbol": row["symbol"], "name": row["name"], "enabled": bool(row["enabled"])} for row in rows]

    def remove_watch_item(self, symbol: str) -> bool:
        with self._connect() as db:
            cursor = db.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),))
        return cursor.rowcount == 1

    def purge_symbol(self, symbol: str) -> None:
        """Remove a symbol and all of its locally stored demo/market state."""
        normalized = symbol.upper()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM alerts WHERE symbol=?", (normalized,))
                db.execute("DELETE FROM reference_closes WHERE symbol=?", (normalized,))
                db.execute("DELETE FROM bars WHERE symbol=?", (normalized,))
                db.execute("DELETE FROM fund_flows WHERE entity_type='stock' AND entity_code=?", (normalized,))
                db.execute("DELETE FROM watchlist WHERE symbol=?", (normalized,))
                db.commit()
            except Exception:
                db.rollback()
                raise

    def save_bar(self, bar: Bar) -> None:
        self.save_market_batch([bar], [])

    @staticmethod
    def _upsert_bar(db: sqlite3.Connection, bar: Bar, now_ms: int) -> None:
        timestamp_ms = to_epoch_ms(bar.timestamp)
        db.execute(
            """
            INSERT INTO bars(
                symbol,interval,timestamp_ms,trading_date,open,high,low,close,
                qfq_open,qfq_high,qfq_low,qfq_close,volume,is_closed,source,updated_at_ms
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol,interval,timestamp_ms) DO UPDATE SET
                trading_date=excluded.trading_date,
                open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                qfq_open=excluded.qfq_open, qfq_high=excluded.qfq_high,
                qfq_low=excluded.qfq_low, qfq_close=excluded.qfq_close,
                volume=excluded.volume, is_closed=excluded.is_closed,
                source=excluded.source, updated_at_ms=excluded.updated_at_ms
            """,
            (
                bar.symbol, bar.interval, timestamp_ms, bar.trading_date.isoformat(),
                bar.open, bar.high, bar.low, bar.close,
                bar.qfq_open, bar.qfq_high, bar.qfq_low, bar.qfq_close,
                bar.volume, int(bar.is_closed), bar.source, now_ms,
            ),
        )

    @staticmethod
    def _upsert_reference_close(db: sqlite3.Connection, value: ReferenceClose, now_ms: int) -> None:
        db.execute(
            """
            INSERT INTO reference_closes(symbol,trading_date,previous_close,source,updated_at_ms)
            VALUES(?,?,?,?,?)
            ON CONFLICT(symbol,trading_date) DO UPDATE SET
                previous_close=excluded.previous_close,
                source=excluded.source,
                updated_at_ms=excluded.updated_at_ms
            """,
            (value.symbol, value.trading_date.isoformat(), value.previous_close, value.source, now_ms),
        )

    def save_market_batch(
        self,
        bars: Iterable[Bar],
        reference_closes: Iterable[ReferenceClose],
        *,
        stream_events: Iterable[StreamEnvelope] = (),
    ) -> None:
        """Atomically replace one provider's canonical series for each touched symbol.

        The monitor intentionally keeps one canonical bar per symbol/interval/time.
        When the configured provider changes, stale rows from another provider are
        removed in the same transaction so indicators never mix vendor histories.
        """
        bars = list(bars)
        reference_closes = list(reference_closes)
        stream_events = list(stream_events)
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                bar_scopes = {(bar.symbol, bar.interval, bar.source) for bar in bars}
                reference_scopes = {(bar.symbol, bar.source) for bar in bars}
                reference_scopes.update((value.symbol, value.source) for value in reference_closes)
                for symbol, interval, source in bar_scopes:
                    db.execute(
                        "DELETE FROM bars WHERE symbol=? AND interval=? AND source<>?",
                        (symbol, interval, source),
                    )
                for symbol, source in reference_scopes:
                    db.execute("DELETE FROM reference_closes WHERE symbol=? AND source<>?", (symbol, source))
                for bar in bars:
                    self._upsert_bar(db, bar, now_ms)
                for value in reference_closes:
                    self._upsert_reference_close(db, value, now_ms)
                for event in stream_events:
                    self._insert_stream_event(db, event, now_ms)
                db.commit()
            except Exception:
                db.rollback()
                raise

    @staticmethod
    def _insert_stream_event(db: sqlite3.Connection, event: StreamEnvelope, now_ms: int) -> int:
        cursor = db.execute(
            """
            INSERT OR IGNORE INTO event_outbox(
                event_id,topic,event_key,payload_json,created_at_ms
            ) VALUES(?,?,?,?,?)
            """,
            (event.event_id, event.topic, event.event_key, event.payload_json(), now_ms),
        )
        return cursor.rowcount

    def enqueue_stream_events(self, events: Iterable[StreamEnvelope]) -> int:
        items = list(events)
        if not items:
            return 0
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        inserted = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for event in items:
                    inserted += self._insert_stream_event(db, event, now_ms)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return inserted

    def list_pending_stream_events(self, limit: int = 2000) -> list[StreamEnvelope]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT event_id,topic,event_key,payload_json
                FROM event_outbox
                WHERE published_at_ms IS NULL
                ORDER BY created_at_ms,event_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            StreamEnvelope(
                event_id=row["event_id"],
                topic=row["topic"],
                event_key=row["event_key"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def mark_stream_events_published(self, event_ids: Iterable[str]) -> None:
        values = list(event_ids)
        if not values:
            return
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.executemany(
                    """
                    UPDATE event_outbox
                    SET published_at_ms=?,attempts=attempts+1,last_error=NULL
                    WHERE event_id=?
                    """,
                    [(now_ms, event_id) for event_id in values],
                )
                cutoff_ms = now_ms - 7 * 24 * 60 * 60 * 1000
                db.execute("DELETE FROM event_outbox WHERE published_at_ms<?", (cutoff_ms,))
                db.commit()
            except Exception:
                db.rollback()
                raise

    def mark_stream_events_failed(self, event_ids: Iterable[str], error: str) -> None:
        values = list(event_ids)
        if not values:
            return
        message = error[:2000]
        with self._connect() as db:
            db.executemany(
                """
                UPDATE event_outbox
                SET attempts=attempts+1,last_error=?
                WHERE event_id=? AND published_at_ms IS NULL
                """,
                [(message, event_id) for event_id in values],
            )

    def count_pending_stream_events(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM event_outbox WHERE published_at_ms IS NULL").fetchone()[0])

    def load_closed_bars(self, symbol: str, interval: str = "1m", limit: int = 5000) -> pd.DataFrame:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM bars
                WHERE symbol=? AND interval=? AND is_closed=1
                ORDER BY timestamp_ms DESC LIMIT ?
                """,
                (symbol.upper(), interval, limit),
            ).fetchall()
        columns = [
            "symbol", "interval", "timestamp", "timestamp_ms", "trading_date",
            "open", "high", "low", "close", "qfq_open", "qfq_high", "qfq_low",
            "qfq_close", "volume", "is_closed", "source",
        ]
        if not rows:
            return pd.DataFrame(columns=columns)
        frame = pd.DataFrame([dict(row) for row in rows]).sort_values("timestamp_ms").reset_index(drop=True)
        frame["timestamp"] = pd.to_datetime(frame["timestamp_ms"], unit="ms", utc=True)
        frame["trading_date"] = pd.to_datetime(frame["trading_date"]).dt.date
        frame["is_closed"] = frame["is_closed"].astype(bool)
        return frame[columns]

    def list_latest_bars(self, limit: int = 200) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT b.symbol,b.interval,b.timestamp_ms,b.trading_date,b.open,b.high,b.low,b.close,b.volume,b.is_closed,b.source
                FROM bars b
                INNER JOIN (
                    SELECT symbol,interval,MAX(timestamp_ms) AS latest_ms
                    FROM bars GROUP BY symbol,interval
                ) latest
                ON b.symbol=latest.symbol AND b.interval=latest.interval AND b.timestamp_ms=latest.latest_ms
                ORDER BY b.timestamp_ms DESC,b.symbol LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["timestamp"] = from_epoch_ms(item.pop("timestamp_ms")).isoformat()
            result.append(item)
        return result

    def save_reference_close(self, value: ReferenceClose) -> None:
        self.save_market_batch([], [value])

    def get_reference_close(self, symbol: str, trading_date: object) -> float | None:
        date_text = trading_date.isoformat() if hasattr(trading_date, "isoformat") else str(trading_date)
        with self._connect() as db:
            row = db.execute(
                "SELECT previous_close FROM reference_closes WHERE symbol=? AND trading_date=?",
                (symbol.upper(), date_text),
            ).fetchone()
        return None if row is None else float(row["previous_close"])

    def insert_alert_if_new(self, alert: Alert, cooldown_seconds: int) -> bool:
        """Atomically apply uniqueness and cooldown across processes.

        `BEGIN IMMEDIATE` serializes competing writers before the latest-alert
        read. The unique constraint is the final guard for the same bar/rule.
        """
        current_ms = to_epoch_ms(alert.bar_timestamp)
        cutoff_ms = current_ms - cooldown_seconds * 1000
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                latest = db.execute(
                    """
                    SELECT bar_timestamp_ms FROM alerts
                    WHERE symbol=? AND interval=? AND rule_id=?
                    ORDER BY bar_timestamp_ms DESC LIMIT 1
                    """,
                    (alert.symbol, alert.interval, alert.rule_id),
                ).fetchone()
                if latest is not None:
                    latest_ms = int(latest["bar_timestamp_ms"])
                    if current_ms <= latest_ms or latest_ms > cutoff_ms:
                        db.rollback()
                        return False
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO alerts(
                        symbol,interval,bar_timestamp_ms,trading_date,rule_id,severity,
                        message,value,context_json,created_at_ms
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        alert.symbol,
                        alert.interval,
                        current_ms,
                        alert.trading_date.isoformat(),
                        alert.rule_id,
                        alert.severity,
                        alert.message,
                        alert.value,
                        json.dumps(alert.context, ensure_ascii=False, sort_keys=True),
                        now_ms,
                    ),
                )
                inserted = cursor.rowcount == 1
                db.commit()
                return inserted
            except Exception:
                db.rollback()
                raise

    def list_alerts(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            if symbol:
                rows = db.execute(
                    "SELECT * FROM alerts WHERE symbol=? ORDER BY bar_timestamp_ms DESC,id DESC LIMIT ?",
                    (symbol.upper(), limit),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM alerts ORDER BY bar_timestamp_ms DESC,id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["bar_timestamp"] = from_epoch_ms(item.pop("bar_timestamp_ms")).isoformat()
            item["created_at"] = from_epoch_ms(item.pop("created_at_ms")).isoformat()
            item["context"] = json.loads(item.pop("context_json"))
            result.append(item)
        return result

    def count_alerts(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0])

    def save_news(self, item: NewsItem, *, stream_event: StreamEnvelope | None = None) -> bool:
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existed = db.execute(
                    "SELECT 1 FROM news WHERE source=? AND external_id=?",
                    (item.source, item.external_id),
                ).fetchone() is not None
                db.execute(
                    """
                    INSERT INTO news(
                        external_id,source,published_at_ms,title,summary,url,symbols_json,created_at_ms,
                        sentiment,sentiment_score,evidence,confidence
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source,external_id) DO UPDATE SET
                        published_at_ms=excluded.published_at_ms,title=excluded.title,
                        summary=excluded.summary,url=excluded.url,symbols_json=excluded.symbols_json,
                        sentiment=excluded.sentiment,sentiment_score=excluded.sentiment_score,
                        evidence=excluded.evidence,confidence=excluded.confidence
                    """,
                    (
                        item.external_id,
                        item.source,
                        to_epoch_ms(item.published_at),
                        item.title,
                        item.summary,
                        item.url,
                        json.dumps(item.symbols, ensure_ascii=False),
                        now_ms,
                        item.sentiment,
                        item.sentiment_score,
                        item.evidence,
                        item.confidence,
                    ),
                )
                if stream_event is not None:
                    self._insert_stream_event(db, stream_event, now_ms)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return not existed

    def list_news(self, limit: int = 200) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM news ORDER BY published_at_ms DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["published_at"] = from_epoch_ms(item.pop("published_at_ms")).isoformat()
            item["created_at"] = from_epoch_ms(item.pop("created_at_ms")).isoformat()
            item["symbols"] = json.loads(item.pop("symbols_json"))
            result.append(item)
        return result

    def save_fund_flows(
        self,
        snapshots: Iterable[FundFlowSnapshot],
        *,
        stream_events: Iterable[StreamEnvelope] = (),
    ) -> int:
        items = list(snapshots)
        stream_events = list(stream_events)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                for item in items:
                    db.execute(
                        """
                        INSERT INTO fund_flows(
                            entity_type,entity_code,entity_name,trading_date,observed_at_ms,
                            latest_price,change_pct,main_net_inflow,main_net_ratio,
                            super_large_net,large_net,medium_net,small_net,source,is_degraded
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(entity_type,entity_code,observed_at_ms,source) DO UPDATE SET
                            entity_name=excluded.entity_name,trading_date=excluded.trading_date,
                            latest_price=excluded.latest_price,change_pct=excluded.change_pct,
                            main_net_inflow=excluded.main_net_inflow,main_net_ratio=excluded.main_net_ratio,
                            super_large_net=excluded.super_large_net,large_net=excluded.large_net,
                            medium_net=excluded.medium_net,small_net=excluded.small_net,
                            is_degraded=excluded.is_degraded
                        """,
                        (
                            item.entity_type, item.entity_code, item.entity_name,
                            item.trading_date.isoformat(), to_epoch_ms(item.observed_at),
                            item.latest_price, item.change_pct, item.main_net_inflow,
                            item.main_net_ratio, item.super_large_net, item.large_net,
                            item.medium_net, item.small_net, item.source, int(item.is_degraded),
                        ),
                    )
                if items:
                    newest_ms = max(to_epoch_ms(item.observed_at) for item in items)
                    cutoff_ms = newest_ms - 14 * 24 * 60 * 60 * 1000
                    db.execute("DELETE FROM fund_flows WHERE observed_at_ms<?", (cutoff_ms,))
                now_ms = to_epoch_ms(datetime.now(timezone.utc))
                for event in stream_events:
                    self._insert_stream_event(db, event, now_ms)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return len(items)

    def list_latest_fund_flows(self, entity_type: str, limit: int = 100, order: str = "desc") -> list[dict]:
        if order not in {"asc", "desc"}:
            raise ValueError("order must be asc or desc")
        direction = "ASC" if order == "asc" else "DESC"
        with self._connect() as db:
            newest = db.execute(
                "SELECT MAX(observed_at_ms) FROM fund_flows WHERE entity_type=?",
                (entity_type,),
            ).fetchone()[0]
            if newest is None:
                return []
            rows = db.execute(
                f"""SELECT f.* FROM fund_flows f
                WHERE f.entity_type=? AND f.observed_at_ms=?
                ORDER BY f.main_net_inflow {direction} LIMIT ?
                """,
                (entity_type, newest, limit),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            item["observed_at"] = from_epoch_ms(item.pop("observed_at_ms")).isoformat()
            item["is_degraded"] = bool(item["is_degraded"])
            result.append(item)
        return result

    def list_fund_flow_history(self, entity_type: str, entity_code: str, limit: int = 100) -> list[dict]:
        """Return one entity's snapshots newest first for change detection."""
        if entity_type not in {"stock", "sector"}:
            raise ValueError("entity_type must be stock or sector")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM fund_flows
                WHERE entity_type=? AND entity_code=?
                ORDER BY observed_at_ms DESC LIMIT ?""",
                (entity_type, entity_code.upper() if entity_type == "stock" else entity_code, limit),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            item["observed_at"] = from_epoch_ms(item.pop("observed_at_ms")).isoformat()
            item["is_degraded"] = bool(item["is_degraded"])
            result.append(item)
        return result

    def source_can_attempt(self, source: str, cooldown_seconds: int = 60) -> bool:
        cutoff_ms = to_epoch_ms(datetime.now(timezone.utc)) - cooldown_seconds * 1000
        with self._connect() as db:
            row = db.execute(
                "SELECT consecutive_failures,last_attempt_ms FROM source_health WHERE source=?",
                (source,),
            ).fetchone()
        return row is None or int(row["consecutive_failures"]) < 5 or int(row["last_attempt_ms"]) <= cutoff_ms

    def get_sync_cursor(self, vendor: str, data_type: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT cursor_value FROM sync_cursors WHERE vendor=? AND data_type=?",
                (vendor, data_type),
            ).fetchone()
        return None if row is None else str(row["cursor_value"])

    def set_sync_cursor(self, vendor: str, data_type: str, value: str) -> None:
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute(
                """INSERT INTO sync_cursors(vendor,data_type,cursor_value,updated_at_ms)
                VALUES(?,?,?,?) ON CONFLICT(vendor,data_type) DO UPDATE SET
                cursor_value=excluded.cursor_value,updated_at_ms=excluded.updated_at_ms""",
                (vendor, data_type, value, now_ms),
            )

    def record_source_health(
        self, source: str, *, success: bool, message: str,
        data_timestamp: datetime | None = None,
    ) -> None:
        now_ms = to_epoch_ms(datetime.now(timezone.utc))
        data_ms = to_epoch_ms(data_timestamp) if data_timestamp else None
        with self._connect() as db:
            current = db.execute(
                "SELECT consecutive_failures,last_success_ms,last_data_ms FROM source_health WHERE source=?",
                (source,),
            ).fetchone()
            failures = 0 if success else (int(current["consecutive_failures"]) + 1 if current else 1)
            state = "正常" if success else ("熔断/停滞" if failures >= 5 else "异常")
            last_success = now_ms if success else (current["last_success_ms"] if current else None)
            last_data = data_ms if data_ms is not None else (current["last_data_ms"] if current else None)
            db.execute(
                """INSERT INTO source_health(
                    source,state,last_attempt_ms,last_success_ms,last_data_ms,consecutive_failures,message
                ) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET
                    state=excluded.state,last_attempt_ms=excluded.last_attempt_ms,
                    last_success_ms=excluded.last_success_ms,last_data_ms=excluded.last_data_ms,
                    consecutive_failures=excluded.consecutive_failures,message=excluded.message""",
                (source, state, now_ms, last_success, last_data, failures, message[:500]),
            )

    def list_source_health(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM source_health ORDER BY source").fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            for column in ("last_attempt_ms", "last_success_ms", "last_data_ms"):
                value = item.pop(column)
                item[column.removesuffix("_ms")] = from_epoch_ms(value).isoformat() if value is not None else None
            result.append(item)
        return result

    def get_latest_bar_date(self, symbol: str, interval: str) -> object | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT trading_date FROM bars WHERE symbol=? AND interval=? ORDER BY timestamp_ms DESC LIMIT 1",
                (symbol.upper(), interval),
            ).fetchone()
        return None if row is None else datetime.fromisoformat(str(row["trading_date"])).date()

    def get_latest_bar_source(self, symbol: str, interval: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT source FROM bars WHERE symbol=? AND interval=? ORDER BY timestamp_ms DESC LIMIT 1",
                (symbol.upper(), interval),
            ).fetchone()
        return None if row is None else str(row["source"])
