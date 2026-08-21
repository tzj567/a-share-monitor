# A股数据自动化基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不依赖任何模型 API、同花顺登录态或高级数据栈的前提下，让桌面程序自动维护全 A 证券目录、全市场快照、板块、资金流、公告、交易日调度与可审计的数据质量状态。

**Architecture:** 保持 SQLite 为桌面权威读模型；所有外部数据先经领域模型和质量校验，再原子写入 SQLite，并通过既有事务外箱可选发送 Kafka。AkShare 公共接口仅存在于适配器层，页面和调度器只依赖稳定协议。单一 `MarketAutomationService` 按交易阶段调度端点，保证一个周期内同一端点只请求一次，并把缓存结果分发给全市场、当前股和自选股视图。

**Tech Stack:** Python 3.11、Pydantic 2、SQLite/FTS5、AkShare 1.18.x、pypinyin、pytest、既有 Kafka/Flink/TDengine 可选链路。

**Global Constraints:**

- 普通同花顺账户只提供公开股票页跳转；本计划不得读取 Cookie、客户端内存、登录态、私有协议或受限网页。
- 财联社正文只通过已授权 `CLSAuthorizedNewsProvider` 获取；无 Token 时保留官网原文跳转能力，不抓正文。
- 公共源必须显示 `source_timestamp`、`collected_at`、`quality_state`、`quality_reason`；不得标成交易所实时行情。
- 实时/分时价格保持不复权；日 K 技术分析继续使用显式 `qfq` 字段，不混合口径。
- SQLite 必须在 Kafka、Flink、TDengine 全部不可用时独立运行。
- 不自动下单，不连接券商交易权限，不输出保证收益或无条件买卖指令。
- 保留用户现有未跟踪文件；每次提交只暂存任务清单中列出的文件。

---

## File map

**Create:**

- `src/stock_monitor/instrument_catalog.py` — 目录刷新、拼音索引和本地检索服务。
- `src/stock_monitor/market_snapshot.py` — 快照质量判断、当前股/自选股采样策略。
- `src/stock_monitor/provider_router.py` — 数据源优先级、熔断、退避和降级结果。
- `src/stock_monitor/market_scheduler.py` — 交易日、交易阶段、任务频率与到期判断。
- `src/stock_monitor/automation.py` — 单端点单次请求的自动化编排入口。
- `src/stock_monitor/data_sources/akshare_catalog.py` — `stock_info_a_code_name()` 目录适配器。
- `src/stock_monitor/data_sources/akshare_snapshot.py` — `stock_zh_a_spot_em()` 快照适配器。
- `src/stock_monitor/data_sources/akshare_sector.py` — 行业/概念板块适配器。
- `src/stock_monitor/data_sources/akshare_announcement.py` — `stock_notice_report()` 公告索引适配器。
- `src/stock_monitor/data_sources/akshare_calendar.py` — `tool_trade_date_hist_sina()` 交易日适配器。
- `tests/test_instrument_catalog.py`
- `tests/test_market_snapshot.py`
- `tests/test_akshare_public_sources.py`
- `tests/test_provider_router.py`
- `tests/test_market_scheduler.py`
- `tests/test_automation.py`

**Modify:**

- `pyproject.toml` — 增加拼音依赖并确保桌面构建安装 providers extra。
- `src/stock_monitor/models.py` — 新增目录、快照、板块和公告领域契约。
- `src/stock_monitor/data_sources/common.py` — 新增公共源协议与批次类型。
- `src/stock_monitor/storage.py` — 新表、幂等 upsert、FTS 搜索、快照采样和公告查询。
- `src/stock_monitor/sync.py` — 构建公共适配器并提供兼容的手动同步入口。
- `src/stock_monitor/streaming.py` — 新领域事件与 TDengine 路由标签。
- `src/stock_monitor/config.py` — 公共源开关、质量阈值、重试和调度配置。
- `src/stock_monitor/api.py` — 目录搜索、快照、板块、公告和调度状态只读 API。
- `build_desktop.ps1` — 构建时安装 providers extra 并收集 AkShare/pypinyin。
- `README.md` — 数据来源、频率、风险和启动说明。
- `tests/test_models.py`
- `tests/test_storage.py`
- `tests/test_sync.py`
- `tests/test_streaming.py`
- `tests/test_api.py`
- `tests/test_config.py`

## Stable interfaces

在 `src/stock_monitor/models.py` 增加以下严格模型；所有时间进入模型后转换为 UTC：

```python
QualityState = Literal["normal", "delayed", "stalled", "degraded", "suspect"]
SecurityType = Literal["stock", "etf", "index", "sector"]

class Instrument(StrictModel):
    symbol: str
    name: str
    pinyin_abbr: str = ""
    exchange: Literal["SH", "SZ", "BJ"]
    security_type: SecurityType = "stock"
    industry: str | None = None
    concepts: list[str] = Field(default_factory=list)
    list_date: date | None = None
    delist_date: date | None = None
    trade_status: Literal["normal", "suspended", "delisting", "delisted", "unknown"] = "unknown"
    source: str
    source_timestamp: datetime | None = None
    collected_at: datetime

class MarketSnapshot(StrictModel):
    symbol: str
    name: str | None = None
    last_price: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    trade_status: Literal["normal", "suspended", "unknown"] = "unknown"
    source: str
    source_timestamp: datetime | None = None
    collected_at: datetime
    quality_state: QualityState
    quality_reason: str

class SectorSnapshot(StrictModel):
    sector_code: str
    sector_name: str
    taxonomy: Literal["industry", "concept"]
    change_pct: float | None = None
    amount: float | None = None
    main_net_inflow: float | None = None
    rising_count: int | None = None
    falling_count: int | None = None
    leading_symbol: str | None = None
    leading_name: str | None = None
    source: str
    source_timestamp: datetime | None = None
    collected_at: datetime
    quality_state: QualityState
    quality_reason: str

class SectorMembership(StrictModel):
    symbol: str
    sector_code: str
    sector_name: str
    taxonomy: Literal["industry", "concept"]
    source: str
    collected_at: datetime

class AnnouncementItem(StrictModel):
    external_id: str
    source: str
    published_at: datetime
    title: str
    url: str
    symbols: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    evidence_excerpt: str | None = None
    collected_at: datetime
    fingerprint: str
```

在 `src/stock_monitor/data_sources/common.py` 增加以下协议：

```python
class InstrumentProvider(Protocol):
    def test_connection(self) -> str: ...
    def fetch_instruments(self) -> list[Instrument]: ...

class SnapshotProvider(Protocol):
    def test_connection(self) -> str: ...
    def fetch_all_snapshots(self) -> list[MarketSnapshot]: ...

class SectorProvider(Protocol):
    def fetch_sectors(self) -> list[SectorSnapshot]: ...
    def fetch_memberships(self, sectors: Iterable[SectorSnapshot]) -> list[SectorMembership]: ...

class AnnouncementProvider(Protocol):
    def fetch_announcements(self, trading_date: date) -> list[AnnouncementItem]: ...

class TradingCalendarProvider(Protocol):
    def fetch_trading_days(self) -> set[date]: ...
```

`provider_router.py` 对调用者只暴露 `ProviderResult[T]`，不得泄漏供应商 DataFrame：

```python
@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    value: T | None
    source: str | None
    degraded: bool
    attempted_sources: tuple[str, ...]
    errors: tuple[str, ...]

class ProviderRouter:
    def call(self, capability: str, operation: Callable[[object], T]) -> ProviderResult[T]: ...
```

`market_scheduler.py` 的公共接口固定为：

```python
class MarketPhase(StrEnum):
    PREOPEN = "preopen"
    AUCTION = "auction"
    CONTINUOUS_AM = "continuous_am"
    LUNCH = "lunch"
    CONTINUOUS_PM = "continuous_pm"
    POSTCLOSE = "postclose"
    CLOSED = "closed"

class ScheduledTask(StrEnum):
    CATALOG = "catalog"
    SNAPSHOT = "snapshot"
    FUND_FLOW = "fund_flow"
    SECTOR = "sector"
    ANNOUNCEMENT = "announcement"
    CLOSE_RECONCILIATION = "close_reconciliation"

@dataclass(frozen=True)
class DueTask:
    task: ScheduledTask
    scheduled_for: datetime

class MarketScheduler:
    def phase_at(self, now: datetime) -> MarketPhase: ...
    def due_tasks(self, now: datetime) -> tuple[DueTask, ...]: ...
    def mark_completed(self, task: ScheduledTask, completed_at: datetime) -> None: ...
```

`automation.py` 的唯一周期入口：

```python
@dataclass(frozen=True)
class AutomationCycleResult:
    started_at: datetime
    finished_at: datetime
    phase: MarketPhase
    completed: tuple[ScheduledTask, ...]
    failed: tuple[ScheduledTask, ...]
    messages: tuple[str, ...]
    next_due_at: datetime | None

class MarketAutomationService:
    def run_due(self, now: datetime, active_symbol: str | None = None) -> AutomationCycleResult: ...
```

`instrument_catalog.py` 在适配器与存储之间保持目录刷新原子性：

```python
@dataclass(frozen=True)
class CatalogRefreshResult:
    instruments_written: int
    memberships_written: int
    source: str
    collected_at: datetime
    degraded: bool
    messages: tuple[str, ...]

class InstrumentCatalogService:
    def refresh(self, now: datetime) -> CatalogRefreshResult: ...
    def search(self, query: str, limit: int = 20) -> list[dict]: ...
```

---

### Task 1: Define strict public-data domain contracts

**Files:**

- Modify: `src/stock_monitor/models.py`
- Modify: `src/stock_monitor/data_sources/common.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing model normalization and rejection tests**

```python
def test_market_snapshot_normalizes_symbol_time_and_units():
    item = MarketSnapshot(
        symbol="600519", last_price=1680.0, volume=12500, amount=21_000_000,
        source="akshare/eastmoney", source_timestamp=datetime(2026, 8, 13, 10, 0),
        collected_at=datetime(2026, 8, 13, 10, 0, 5), quality_state="normal", quality_reason="fresh",
    )
    assert item.symbol == "600519.SH"
    assert item.collected_at.tzinfo == timezone.utc
    assert item.volume == 12500

@pytest.mark.parametrize("field,value", [("last_price", float("nan")), ("amount", -1), ("volume", -1)])
def test_market_snapshot_rejects_non_finite_or_negative_values(field, value):
    payload = valid_snapshot()
    payload[field] = value
    with pytest.raises(ValidationError):
        MarketSnapshot(**payload)

def test_future_provider_timestamp_is_rejected():
    payload = valid_snapshot(collected_at=datetime(2026, 8, 13, 2, tzinfo=timezone.utc))
    payload["source_timestamp"] = datetime(2026, 8, 13, 2, 2, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        MarketSnapshot(**payload)
```

- [ ] **Step 2: Run the tests and confirm they fail because the contracts do not exist**

Run: `python -m pytest tests/test_models.py -q`

Expected: FAIL with import errors for `Instrument`, `MarketSnapshot`, `SectorSnapshot`, or `AnnouncementItem`.

- [ ] **Step 3: Implement the four models and provider protocols**

Use the stable interfaces above. Reuse `normalize_symbol()` semantics, reject NaN/Inf, reject negative volume/amount, enforce `high >= max(open, low, last_price)` only when all compared values exist, and reject `source_timestamp > collected_at + 30 seconds`.

- [ ] **Step 4: Run focused and existing model tests**

Run: `python -m pytest tests/test_models.py tests/test_data_sources.py -q`

Expected: PASS.

- [ ] **Step 5: Commit only this contract slice**

```powershell
git add -- src/stock_monitor/models.py src/stock_monitor/data_sources/common.py tests/test_models.py
git commit -m "feat: define public market data contracts"
```

### Task 2: Add SQLite catalog, FTS5, latest snapshots, quote samples, sectors, and announcements

**Files:**

- Create: `src/stock_monitor/instrument_catalog.py`
- Modify: `src/stock_monitor/storage.py`
- Create: `tests/test_instrument_catalog.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write failing persistence and search tests**

```python
def test_catalog_search_supports_code_name_and_pinyin(tmp_path):
    repo = SQLiteRepository(tmp_path / "market.db")
    repo.upsert_instruments([
        instrument("600519.SH", "贵州茅台", "gzmt", industry="白酒"),
        instrument("000001.SZ", "平安银行", "payh", industry="银行"),
    ])
    assert repo.search_instruments("6005", 10)[0]["symbol"] == "600519.SH"
    assert repo.search_instruments("茅台", 10)[0]["name"] == "贵州茅台"
    assert repo.search_instruments("gzmt", 10)[0]["symbol"] == "600519.SH"

def test_older_snapshot_never_overwrites_newer_snapshot(tmp_path):
    repo = SQLiteRepository(tmp_path / "market.db")
    repo.save_market_snapshots([snapshot("600519.SH", minute=2, price=12)], {"600519.SH"})
    repo.save_market_snapshots([snapshot("600519.SH", minute=1, price=9)], {"600519.SH"})
    assert repo.get_latest_snapshot("600519.SH")["last_price"] == 12
    assert len(repo.list_quote_samples("600519.SH", limit=10)) == 1

def test_announcement_upsert_is_idempotent(tmp_path):
    repo = SQLiteRepository(tmp_path / "market.db")
    item = announcement("sha256:abc")
    assert repo.save_announcements([item]) == 1
    assert repo.save_announcements([item]) == 0

def test_catalog_refresh_does_not_replace_memberships_on_partial_failure(repo):
    seed_complete_memberships(repo)
    service = InstrumentCatalogService(repo, catalog_provider=working_catalog(), sector_provider=partial_failure())
    result = service.refresh(fixed_now)
    assert result.degraded is True
    assert repo.list_instrument_sectors("600519.SH") == previously_seeded_memberships()

def test_local_search_p95_is_below_fifty_milliseconds(repo):
    repo.upsert_instruments(make_catalog(6000))
    elapsed = []
    for query in representative_queries(1000):
        started = perf_counter()
        repo.search_instruments(query, 20)
        elapsed.append(perf_counter() - started)
    assert statistics.quantiles(elapsed, n=100, method="inclusive")[94] <= 0.050
```

- [ ] **Step 2: Run focused tests and observe missing repository methods**

Run: `python -m pytest tests/test_instrument_catalog.py tests/test_storage.py -q`

Expected: FAIL because the tables and methods are absent.

- [ ] **Step 3: Add schema and idempotent repository methods**

Create these tables in `_initialize()` without dropping existing data:

- `instruments(symbol PRIMARY KEY, name, pinyin_abbr, exchange, security_type, industry, concepts_json, list_date, delist_date, trade_status, source, source_timestamp_ms, collected_at_ms)`.
- `instrument_fts` as FTS5 with `symbol UNINDEXED, name, pinyin_abbr, industry, concepts`; rebuild affected rows inside the same transaction as `instruments`.
- `latest_market_snapshots(symbol PRIMARY KEY, ... source_timestamp_ms, collected_at_ms, effective_timestamp_ms, quality_state, quality_reason)`; `effective_timestamp_ms` is provider timestamp when present, otherwise collection timestamp. Update only when incoming `(effective_timestamp_ms, collected_at_ms)` is newer.
- `quote_samples(symbol, sample_time_ms, source_timestamp_ms, last_price, volume, amount, source, collected_at_ms, quality_state, PRIMARY KEY(symbol, sample_time_ms))`; `sample_time_ms` uses the same explicit provider-time-first rule. Write only symbols passed in `sampled_symbols`, retain 10 calendar days after successful insert.
- `latest_sector_snapshots(taxonomy, sector_code, ... quality_state, quality_reason, PRIMARY KEY(taxonomy, sector_code))`.
- `instrument_sector_memberships(symbol, taxonomy, sector_code, sector_name, source, collected_at_ms, PRIMARY KEY(symbol, taxonomy, sector_code, source))`; a successful daily taxonomy refresh replaces that source/taxonomy set atomically and rebuilds the affected instrument FTS terms.
- `announcements(fingerprint PRIMARY KEY, external_id, source, published_at_ms, title, url, symbols_json, sectors_json, evidence_excerpt, collected_at_ms)`.
- `trading_calendar(trading_date PRIMARY KEY, is_open, source, collected_at_ms)`; replace a fetched source range atomically and retain the last successful calendar when refresh fails.

Implement exact methods:

```python
def upsert_instruments(self, items: Iterable[Instrument]) -> int: ...
def search_instruments(self, query: str, limit: int = 20) -> list[dict]: ...
def save_market_snapshots(self, items: Iterable[MarketSnapshot], sampled_symbols: set[str], stream_events: Iterable[StreamEnvelope] = ()) -> int: ...
def get_latest_snapshot(self, symbol: str) -> dict | None: ...
def list_latest_snapshots(self, symbols: Iterable[str] | None = None, limit: int = 6000) -> list[dict]: ...
def list_quote_samples(self, symbol: str, limit: int = 500) -> list[dict]: ...
def save_sector_snapshots(self, items: Iterable[SectorSnapshot], stream_events: Iterable[StreamEnvelope] = ()) -> int: ...
def list_latest_sector_snapshots(self, taxonomy: str | None = None, limit: int = 100) -> list[dict]: ...
def replace_sector_memberships(self, items: Iterable[SectorMembership], taxonomy: str, source: str) -> int: ...
def list_instrument_sectors(self, symbol: str) -> list[dict]: ...
def save_announcements(self, items: Iterable[AnnouncementItem], stream_events: Iterable[StreamEnvelope] = ()) -> int: ...
def list_announcements(self, symbol: str | None = None, limit: int = 100) -> list[dict]: ...
def replace_trading_calendar(self, trading_days: set[date], source: str, collected_at: datetime) -> int: ...
def list_trading_days(self, start: date, end: date) -> set[date]: ...
```

Search ranking must be deterministic: exact code, code prefix, exact name, name prefix, pinyin prefix, then FTS rank. Empty query returns `[]`; `limit` clamps to 1–100.

`replace_sector_memberships()` also refreshes denormalized `instruments.industry` and `instruments.concepts_json` from the active membership set in the same transaction. A concept list is sorted and deduplicated so its JSON and FTS representation are stable.

Implement `InstrumentCatalogService.refresh()` so the instrument list is upserted independently, but board memberships are replaced only for a complete validated taxonomy batch. `search()` is a thin bounded repository call and never starts a provider request.

- [ ] **Step 4: Add migration and backward-compatibility tests**

Open a database initialized by the current schema, construct a new repository, and assert watchlist, bars, news and funds remain readable after new tables are created.

- [ ] **Step 5: Run storage suites**

Run: `python -m pytest tests/test_instrument_catalog.py tests/test_storage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the persistence slice**

```powershell
git add -- src/stock_monitor/instrument_catalog.py src/stock_monitor/storage.py tests/test_instrument_catalog.py tests/test_storage.py
git commit -m "feat: persist searchable market catalog and snapshots"
```

### Task 3: Implement AkShare public adapters with schema-drift failure

**Files:**

- Create: `src/stock_monitor/data_sources/akshare_catalog.py`
- Create: `src/stock_monitor/data_sources/akshare_snapshot.py`
- Create: `src/stock_monitor/data_sources/akshare_sector.py`
- Create: `src/stock_monitor/data_sources/akshare_announcement.py`
- Create: `src/stock_monitor/data_sources/akshare_calendar.py`
- Modify: `pyproject.toml`
- Create: `tests/test_akshare_public_sources.py`

- [ ] **Step 1: Write adapter tests against injected fake AkShare modules**

```python
def test_catalog_adapter_adds_exchange_and_pinyin():
    fake = SimpleNamespace(stock_info_a_code_name=lambda: DataFrame({"code": ["600519"], "name": ["贵州茅台"]}))
    rows = AkShareCatalogProvider(ak_module=fake, clock=fixed_clock).fetch_instruments()
    assert rows[0].symbol == "600519.SH"
    assert rows[0].pinyin_abbr == "gzmt"
    assert rows[0].source == "akshare/public"

def test_snapshot_adapter_normalizes_volume_as_shares():
    fake = SimpleNamespace(stock_zh_a_spot_em=lambda: DataFrame({
        "代码": ["600519"], "名称": ["贵州茅台"], "最新价": [1680.0], "涨跌幅": [1.2],
        "涨跌额": [19.92], "成交量": [12500], "成交额": [21_000_000],
        "今开": [1660.0], "最高": [1690.0], "最低": [1650.0], "昨收": [1660.08],
    }))
    item = AkShareSnapshotProvider(ak_module=fake, clock=fixed_clock).fetch_all_snapshots()[0]
    assert item.volume == 1_250_000  # 东方财富现货表成交量为手，统一转换为股

def test_schema_drift_raises_explicit_provider_error():
    fake = SimpleNamespace(stock_zh_a_spot_em=lambda: DataFrame({"unexpected": [1]}))
    with pytest.raises(ProviderError, match="缺少字段"):
        AkShareSnapshotProvider(ak_module=fake).fetch_all_snapshots()
```

Also cover:

- `stock_board_industry_name_em()` and `stock_board_concept_name_em()` stay separate taxonomies.
- `stock_board_industry_cons_em(symbol=sector_name)` and `stock_board_concept_cons_em(symbol=sector_name)` map board members to normalized symbols; a failed member endpoint leaves the previous complete membership set intact.
- `stock_notice_report(symbol="全部", date="YYYYMMDD")` produces stable SHA-256 fingerprints and never stores page HTML.
- `tool_trade_date_hist_sina()` maps `trade_date` to a `set[date]`.
- Empty DataFrames and non-numeric required columns raise `ProviderError`; optional columns become `None`.

- [ ] **Step 2: Run tests and confirm missing adapters**

Run: `python -m pytest tests/test_akshare_public_sources.py -q`

Expected: FAIL with import errors.

- [ ] **Step 3: Implement adapters with injection and throttling**

Each constructor accepts `ak_module`, `clock`, and existing `RequestThrottle`; production lazily imports AkShare. Required-column aliases are explicit tuples in the adapter. Do not infer an unknown replacement column after a source layout change.

Board rankings call the two board-name endpoints once per scheduled sector cycle. Board memberships refresh only with the daily catalog task: one throttled constituent request per board, never one request per stock. Write a taxonomy only after every requested board returns and validates, so partial responses cannot erase the previous mapping.

`stock_zh_a_spot_em()` does not guarantee a provider timestamp in each returned row. When no timestamp column exists, keep `source_timestamp=None`, set `quality_state="degraded"`, and use `quality_reason="供应商未提供逐行时间戳"`; never copy `collected_at` into the provider timestamp field.

Add `pypinyin>=0.55,<1` to the `providers` optional dependency. Generate abbreviation with `lazy_pinyin(name, style=Style.FIRST_LETTER)` and lowercase ASCII. If pypinyin fails for one name, keep `pinyin_abbr=""` and continue that row.

- [ ] **Step 4: Run adapter and regression tests**

Run: `python -m pytest tests/test_akshare_public_sources.py tests/test_data_sources.py tests/test_throttle.py -q`

Expected: PASS.

- [ ] **Step 5: Commit adapters**

```powershell
git add -- pyproject.toml src/stock_monitor/data_sources/akshare_catalog.py src/stock_monitor/data_sources/akshare_snapshot.py src/stock_monitor/data_sources/akshare_sector.py src/stock_monitor/data_sources/akshare_announcement.py src/stock_monitor/data_sources/akshare_calendar.py tests/test_akshare_public_sources.py
git commit -m "feat: add guarded AkShare public adapters"
```

### Task 4: Add provider routing, freshness, retry, and circuit breaking

**Files:**

- Create: `src/stock_monitor/provider_router.py`
- Create: `src/stock_monitor/market_snapshot.py`
- Modify: `src/stock_monitor/config.py`
- Create: `tests/test_provider_router.py`
- Create: `tests/test_market_snapshot.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing routing and quality tests**

```python
def test_router_falls_back_after_primary_failure(repo):
    primary = provider_raising("timeout")
    fallback = provider_returning([snapshot(source="akshare/public")])
    router = ProviderRouter(repo, {"snapshot": [primary, fallback]}, retry_limit=1, clock=fixed_clock)
    result = router.call("snapshot", lambda provider: provider.fetch_all_snapshots())
    assert result.value is not None
    assert result.degraded is True
    assert result.attempted_sources == ("ifind", "akshare/public")
    assert "timeout" in result.errors[0]

def test_freshness_marks_stalled_without_relabeling_timestamp():
    assessed = assess_snapshot_quality(snapshot_at("2026-08-13T01:30:00Z"), now=utc("2026-08-13T01:33:01Z"), max_age=120)
    assert assessed.quality_state == "stalled"
    assert assessed.source_timestamp == utc("2026-08-13T01:30:00Z")
```

Cover five consecutive failures opening the existing source-health circuit, one probe after cooldown, success closing the circuit, exponential backoff delays `1, 2, 4` seconds plus injected jitter, and redaction of tokens/URLs in errors.

- [ ] **Step 2: Run tests and observe missing router/quality service**

Run: `python -m pytest tests/test_provider_router.py tests/test_market_snapshot.py tests/test_config.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement deterministic routing and quality assessment**

Use existing `source_can_attempt()` and `record_source_health()`. `ProviderRouter` retries only timeout, connection and provider-transient errors; schema errors skip retries and immediately try the next source. The current registry contains AkShare public providers. A future authorized iFinD adapter may be inserted before AkShare only for capabilities that its approved contract and tests actually support. An unconfigured provider is omitted rather than counted as failed.

Add config fields with bounds:

```python
public_data_enabled: bool = True
request_timeout_seconds: int = Field(default=15, ge=3, le=120)
request_retry_limit: int = Field(default=2, ge=0, le=5)
circuit_failure_threshold: int = Field(default=5, ge=2, le=20)
circuit_cooldown_seconds: int = Field(default=60, ge=10, le=3600)
snapshot_stale_seconds: int = Field(default=120, ge=30, le=1800)
quote_sample_retention_days: int = Field(default=10, ge=1, le=90)
```

`assess_snapshot_quality()` must preserve original timestamps and return a copied model with an explicit reason.

- [ ] **Step 4: Run focused suites**

Run: `python -m pytest tests/test_provider_router.py tests/test_market_snapshot.py tests/test_config.py tests/test_storage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit routing and quality**

```powershell
git add -- src/stock_monitor/provider_router.py src/stock_monitor/market_snapshot.py src/stock_monitor/config.py tests/test_provider_router.py tests/test_market_snapshot.py tests/test_config.py
git commit -m "feat: route public data with freshness and fallback"
```

### Task 5: Implement trading calendar and phase-aware scheduler

**Files:**

- Create: `src/stock_monitor/market_scheduler.py`
- Create: `tests/test_market_scheduler.py`

- [ ] **Step 1: Write failing phase and frequency tests**

```python
@pytest.mark.parametrize(("local_time", "expected"), [
    ("2026-08-13 09:10", MarketPhase.PREOPEN),
    ("2026-08-13 09:20", MarketPhase.AUCTION),
    ("2026-08-13 09:27", MarketPhase.PREOPEN),
    ("2026-08-13 10:00", MarketPhase.CONTINUOUS_AM),
    ("2026-08-13 12:00", MarketPhase.LUNCH),
    ("2026-08-13 14:00", MarketPhase.CONTINUOUS_PM),
    ("2026-08-13 15:10", MarketPhase.POSTCLOSE),
])
def test_market_phase(local_time, expected, scheduler):
    assert scheduler.phase_at(shanghai(local_time)) == expected

def test_snapshot_due_once_per_minute_in_continuous_session(scheduler):
    now = shanghai("2026-08-13 10:00:00")
    assert ScheduledTask.SNAPSHOT in {item.task for item in scheduler.due_tasks(now)}
    scheduler.mark_completed(ScheduledTask.SNAPSHOT, now)
    assert ScheduledTask.SNAPSHOT not in {item.task for item in scheduler.due_tasks(now + timedelta(seconds=59))}
    assert ScheduledTask.SNAPSHOT in {item.task for item in scheduler.due_tasks(now + timedelta(seconds=60))}

def test_holiday_is_closed_even_on_weekday(scheduler_with_holiday):
    assert scheduler_with_holiday.phase_at(shanghai("2026-10-01 10:00")) == MarketPhase.CLOSED
```

Cover startup checks, catalog once pre-open and once post-close, funds/sectors every 180 seconds, announcements every 240 seconds in-session and 900 seconds off-session, lunch snapshot every 300 seconds, and close reconciliation once after 15:00.

- [ ] **Step 2: Run scheduler tests and confirm failure**

Run: `python -m pytest tests/test_market_scheduler.py -q`

Expected: FAIL because `MarketScheduler` is absent.

- [ ] **Step 3: Implement Shanghai-time scheduler with persisted completion**

Use `zoneinfo.ZoneInfo("Asia/Shanghai")`; do not use machine-local naive datetimes. Trading dates come from the cached calendar provider. If calendar refresh fails and no cache exists, weekdays are provisional and scheduler messages include `交易日历降级为工作日判断`; weekends stay closed.

Phase boundaries are explicit: pre-open 08:30–09:14:59, auction 09:15–09:25, pre-open transition 09:25:01–09:29:59, continuous morning 09:30–11:30, lunch 11:30:01–12:59:59, continuous afternoon 13:00–15:00, post-close 15:00:01–18:00, otherwise closed. Boundary tests pin every transition.

Persist last-completed timestamps via existing `sync_cursors` under vendor `scheduler` and one data type per `ScheduledTask`. `due_tasks()` is pure except cursor reads; `mark_completed()` is the only state write.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_market_scheduler.py tests/test_storage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit scheduler**

```powershell
git add -- src/stock_monitor/market_scheduler.py tests/test_market_scheduler.py
git commit -m "feat: schedule updates by A-share market phase"
```

### Task 6: Orchestrate automatic collection and preserve advanced-stream isolation

**Files:**

- Create: `src/stock_monitor/automation.py`
- Modify: `src/stock_monitor/sync.py`
- Modify: `src/stock_monitor/streaming.py`
- Create: `tests/test_automation.py`
- Modify: `tests/test_sync.py`
- Modify: `tests/test_streaming.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_cycle_calls_all_market_endpoint_once_and_fans_out(repo):
    provider = CountingSnapshotProvider([snapshot("600519.SH"), snapshot("000001.SZ")])
    service = automation_service(repo, snapshot_provider=provider, due={ScheduledTask.SNAPSHOT})
    result = service.run_due(shanghai("2026-08-13 10:00"), active_symbol="600519.SH")
    assert provider.calls == 1
    assert result.completed == (ScheduledTask.SNAPSHOT,)
    assert repo.get_latest_snapshot("000001.SZ") is not None
    assert len(repo.list_quote_samples("600519.SH")) == 1

def test_kafka_failure_does_not_roll_back_sqlite(repo):
    service = automation_service(repo, publisher=FailingPublisher(), due={ScheduledTask.SNAPSHOT})
    result = service.run_due(shanghai("2026-08-13 10:00"))
    assert repo.get_latest_snapshot("600519.SH") is not None
    assert result.completed == (ScheduledTask.SNAPSHOT,)
    assert repo.count_pending_stream_events() > 0

def test_one_task_failure_does_not_block_other_due_tasks(repo):
    service = automation_service(repo, snapshot_provider=FailingProvider(), announcement_provider=working_announcements(), due={ScheduledTask.SNAPSHOT, ScheduledTask.ANNOUNCEMENT})
    result = service.run_due(shanghai("2026-08-13 10:00"))
    assert ScheduledTask.SNAPSHOT in result.failed
    assert ScheduledTask.ANNOUNCEMENT in result.completed
```

- [ ] **Step 2: Run tests and confirm orchestration is missing**

Run: `python -m pytest tests/test_automation.py tests/test_sync.py tests/test_streaming.py -q`

Expected: FAIL.

- [ ] **Step 3: Add stream envelopes for new contracts**

Implement deterministic events:

```python
def market_snapshot_envelope(item: MarketSnapshot, topic: str) -> StreamEnvelope: ...
def sector_snapshot_envelope(item: SectorSnapshot, topic: str) -> StreamEnvelope: ...
def announcement_envelope(item: AnnouncementItem, topic: str) -> StreamEnvelope: ...
```

Use source timestamps as event time, `collected_at` only as ingestion metadata, and stable identity keys. Add TDengine routing tags without changing existing bar/fund/news contracts.

- [ ] **Step 4: Implement `MarketAutomationService`**

For each due task:

1. Check the router/circuit.
2. Call the endpoint once.
3. Validate the complete batch.
4. Save SQLite and outbox in one transaction where the repository supports it.
5. Mark scheduler completion only after SQLite succeeds.
6. Flush advanced outbox as best effort after local completion.
7. Collect one bounded, credential-redacted message per failure.

Sample only `watchlist symbols ∪ {active_symbol}` into `quote_samples`; save all rows into `latest_market_snapshots`. Keep existing `SyncService.sync_market()`, `sync_news()` and `sync_radar()` working as manual commands, but have public-source manual refresh delegate to the same lower-level services.

- [ ] **Step 5: Run orchestration and regression suites**

Run: `python -m pytest tests/test_automation.py tests/test_sync.py tests/test_streaming.py tests/test_storage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit automation**

```powershell
git add -- src/stock_monitor/automation.py src/stock_monitor/sync.py src/stock_monitor/streaming.py tests/test_automation.py tests/test_sync.py tests/test_streaming.py
git commit -m "feat: orchestrate automatic market collection"
```

### Task 7: Expose read-only APIs, package providers, and document the public-source contract

**Files:**

- Modify: `src/stock_monitor/api.py`
- Modify: `build_desktop.ps1`
- Modify: `README.md`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_search_and_snapshot_endpoints(client, seeded_repository):
    response = client.get("/api/instruments/search", params={"q": "gzmt", "limit": 20})
    assert response.status_code == 200
    assert response.json()[0]["symbol"] == "600519.SH"
    snapshot = client.get("/api/market/snapshots/600519.SH").json()
    assert snapshot["source"] == "akshare/public"
    assert snapshot["quality_state"] in {"normal", "delayed", "stalled", "degraded", "suspect"}

def test_scheduler_status_is_read_only(client):
    before = client.app.state.repository.get_sync_cursor("scheduler", "snapshot")
    response = client.get("/api/automation/status")
    after = client.app.state.repository.get_sync_cursor("scheduler", "snapshot")
    assert response.status_code == 200
    assert before == after
```

- [ ] **Step 2: Run API tests and confirm routes are absent**

Run: `python -m pytest tests/test_api.py -q`

Expected: FAIL with 404 responses.

- [ ] **Step 3: Add bounded read-only routes**

Add:

- `GET /api/instruments/search?q=&limit=`
- `GET /api/market/snapshots/{symbol}`
- `GET /api/market/snapshots?symbols=&limit=`
- `GET /api/sectors?taxonomy=&limit=`
- `GET /api/announcements?symbol=&limit=`
- `GET /api/automation/status`

Validate symbols and query bounds using FastAPI/Pydantic; none of these routes trigger a provider request.

- [ ] **Step 4: Update packaging and operator documentation**

`build_desktop.ps1` installs `.[desktop-build,providers,streaming]`, runs tests before PyInstaller, and collects AkShare/pypinyin metadata. README states actual target frequencies, no real-time SLA, fund-flow methodology caveat, source timestamps, manual refresh behavior, and future iFinD authorization boundary.

- [ ] **Step 5: Run full verification**

Run: `python -m pytest -q`

Expected: all tests pass.

Run: `python -m pytest --cov=stock_monitor --cov-report=term-missing -q`

Expected: no regression in existing coverage and every new service has direct tests.

Run: `python -m compileall -q src`

Expected: exit code 0.

- [ ] **Step 6: Commit the public API and packaging slice**

```powershell
git add -- src/stock_monitor/api.py build_desktop.ps1 README.md tests/test_api.py
git commit -m "feat: expose automated market data read models"
```

## Phase acceptance gate

- [ ] Full-market snapshot adapter performs one provider call per scheduled cycle, never one call per stock.
- [ ] Code, Chinese name and pinyin-prefix search pass and P95 for 1,000 representative local searches is at most 50 ms on the target PC.
- [ ] Every visible data record has source, provider timestamp, collection timestamp and quality state.
- [ ] Schema drift, timeout and rate-limit tests produce explicit degraded states; stale data cannot overwrite newer data.
- [ ] Kafka/Flink/TDengine disabled test leaves SQLite collection and API reads usable.
- [ ] No model API key is required for startup, collection, search or rule-based signals.
- [ ] No test or implementation reads Tonghuashun/CLS login state, Cookie, process memory or restricted endpoints.
