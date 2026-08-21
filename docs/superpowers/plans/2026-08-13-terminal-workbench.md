# 同花顺式桌面工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Tkinter 桌面程序重组为用户已批准的同花顺式信息工作台，支持全局搜索、自选与板块导航、分时/K 线/资金流/资讯联动、明确的数据新鲜度，以及只由用户点击触发的同花顺公开查询入口。

**Architecture:** 保留 `stock_monitor.desktop:main` 和 Tkinter/PyInstaller 技术路线；新建 `desktop_ui` 包承载纯状态、控制器、页面和组件，旧 `desktop.py` 缩减为兼容入口与组合根。后台线程只运行服务并向队列发布不可变 `UiEvent`，Tk 主线程消费事件并更新页面。页面不导入 AkShare、iFinD、财联社客户端或直接执行 SQL，只调用控制器并渲染 `TerminalState`。

**Tech Stack:** Python 3.11、Tkinter/ttk、既有 `StockChart`、Pandas、SQLite、PyInstaller、pytest。

**Global Constraints:**

- 前置条件：先完成 `2026-08-13-data-automation-foundation.md` 中的数据模型、存储和 `MarketAutomationService`。
- 第一轮不迁移 PySide6、不引入 Node/WebView、不替换现有 K 线绘图引擎。
- 同花顺按钮仅打开 `https://stockpage.10jqka.com.cn/{六位代码}/`；不自动登录、不读 Cookie、不抓网页、不控制客户端。
- 财联社列表只显示本地已授权 API 数据或公开链接元数据；无授权时不抓正文。
- 没有可靠盘口契约时固定显示“盘口数据不可用”，不得用历史数据或快照拼成五档/十档。
- UI 文案使用“情景观察”“风险提示”，不使用“买入”“卖出”“必涨”“稳赚”等交易指令。
- 所有旧配置、观察列表、SQLite 和 Windows 凭据路径保持兼容。
- 保留用户现有未跟踪文件；每次提交只暂存任务清单列出的文件。

---

## File map

**Create:**

- `src/stock_monitor/desktop_ui/__init__.py`
- `src/stock_monitor/desktop_ui/pages/__init__.py`
- `src/stock_monitor/desktop_ui/widgets/__init__.py`
- `src/stock_monitor/desktop_ui/state.py` — 不可变页面状态和 UI 事件。
- `src/stock_monitor/desktop_ui/controller.py` — 缓存优先加载、搜索、选择和刷新命令。
- `src/stock_monitor/desktop_ui/formatting.py` — 金额、涨跌、新鲜度和来源格式化。
- `src/stock_monitor/desktop_ui/external_links.py` — 公开 URL 构造和用户点击跳转。
- `src/stock_monitor/desktop_ui/shell.py` — 顶部栏、左/中/右三栏、底部状态栏和事件泵。
- `src/stock_monitor/desktop_ui/pages/workbench.py` — 主工作台页面组合。
- `src/stock_monitor/desktop_ui/pages/settings.py` — 现有设置迁移。
- `src/stock_monitor/desktop_ui/widgets/instrument_search.py`
- `src/stock_monitor/desktop_ui/widgets/watchlist.py`
- `src/stock_monitor/desktop_ui/widgets/intraday_chart.py`
- `src/stock_monitor/desktop_ui/widgets/source_badge.py`
- `src/stock_monitor/desktop_ui/widgets/evidence_panel.py`
- `tests/test_desktop_state.py`
- `tests/test_desktop_controller.py`
- `tests/test_desktop_formatting.py`
- `tests/test_external_links.py`
- `tests/test_desktop_smoke.py`

**Modify:**

- `src/stock_monitor/desktop.py` — 保留入口，委托给新 shell。
- `src/stock_monitor/chart.py` — 支持日/周和 raw/qfq 显式口径，不改变指标含义。
- `src/stock_monitor/config.py` — 窗口布局、启动自动刷新和最后选择项。
- `desktop_launcher.py` — 透传 `--smoke-test` 并输出可诊断退出码。
- `desktop.spec` — 收集新 UI 包和资源。
- `build_desktop.ps1` — 自动化测试、构建、冒烟和桌面副本更新。
- `README.md` — 页面说明、同花顺边界和数据新鲜度图例。
- `tests/test_config.py`
- `tests/test_analysis.py`

## Stable interfaces

`desktop_ui/state.py` 固定页面读模型和事件契约：

```python
@dataclass(frozen=True)
class SourceStatusView:
    source: str
    state: Literal["normal", "delayed", "stalled", "degraded", "suspect", "unavailable"]
    source_timestamp: datetime | None
    collected_at: datetime | None
    message: str

@dataclass(frozen=True)
class InstrumentHeaderView:
    symbol: str
    name: str
    industry: str | None
    last_price_text: str
    change_text: str
    change_color: Literal["up", "down", "flat", "muted"]
    source_status: SourceStatusView

@dataclass(frozen=True)
class TerminalState:
    active_symbol: str | None
    search_results: tuple[dict, ...]
    watchlist_rows: tuple[dict, ...]
    sector_rows: tuple[dict, ...]
    header: InstrumentHeaderView | None
    quote_samples: tuple[dict, ...]
    daily_bars: tuple[dict, ...]
    fund_flow_rows: tuple[dict, ...]
    related_news: tuple[dict, ...]
    related_announcements: tuple[dict, ...]
    source_health: tuple[SourceStatusView, ...]
    market_phase: str
    next_refresh_at: datetime | None
    status_message: str
    busy: bool

@dataclass(frozen=True)
class UiEvent:
    kind: Literal["state", "cycle_complete", "operation_error", "shutdown"]
    payload: object
```

`desktop_ui/controller.py` 只返回新状态，不操作 Tk 组件：

```python
class TerminalController:
    def initial_state(self, now: datetime) -> TerminalState: ...
    def search(self, state: TerminalState, query: str) -> TerminalState: ...
    def select_instrument(self, state: TerminalState, symbol: str, now: datetime) -> TerminalState: ...
    def reload_cached(self, state: TerminalState, now: datetime) -> TerminalState: ...
    def run_automation_cycle(self, state: TerminalState, now: datetime) -> UiEvent: ...
```

缓存优先选择流程必须固定为：目录记录 → 最新快照 → quote samples → 日 K → 资金流 → 相关公告/资讯 → 来源状态；后台刷新完成后再生成一份完整新状态，页面不读取半更新数据。

`external_links.py` 固定接口：

```python
def tonghuashun_public_stock_url(symbol: str) -> str: ...

class ExternalLinkService:
    def __init__(self, opener: Callable[[str], bool] = webbrowser.open) -> None: ...
    def open_tonghuashun(self, symbol: str) -> bool: ...
    def open_source_url(self, url: str) -> bool: ...
```

只允许 `http`/`https`，拒绝 `file:`、`javascript:`、`data:` 和空 host。

---

### Task 1: Create pure terminal state, formatting, and cache-first controller

**Files:**

- Create: `src/stock_monitor/desktop_ui/__init__.py`
- Create: `src/stock_monitor/desktop_ui/pages/__init__.py`
- Create: `src/stock_monitor/desktop_ui/widgets/__init__.py`
- Create: `src/stock_monitor/desktop_ui/state.py`
- Create: `src/stock_monitor/desktop_ui/formatting.py`
- Create: `src/stock_monitor/desktop_ui/controller.py`
- Create: `tests/test_desktop_state.py`
- Create: `tests/test_desktop_formatting.py`
- Create: `tests/test_desktop_controller.py`

- [ ] **Step 1: Write failing formatting and state tests**

```python
def test_freshness_text_never_calls_stale_data_realtime():
    status = source_status(snapshot_at="2026-08-13T01:30:00Z", now="2026-08-13T01:33:01Z", state="stalled")
    assert format_source_status(status) == "已停滞 · 数据 3分01秒前 · akshare/public"
    assert "实时" not in format_source_status(status)

@pytest.mark.parametrize(("value", "expected"), [(1_250, "1,250"), (12_500_000, "1,250.00万"), (1_250_000_000, "12.50亿")])
def test_amount_formatting(value, expected):
    assert format_amount(value) == expected

def test_state_is_immutable():
    state = empty_terminal_state()
    with pytest.raises(FrozenInstanceError):
        state.busy = True
```

- [ ] **Step 2: Write a failing cache-first controller test**

```python
def test_select_instrument_loads_cached_panels_without_network(repo):
    seed_terminal_cache(repo, "600519.SH")
    network = Mock()
    controller = TerminalController(repo, automation=network)
    state = controller.select_instrument(controller.initial_state(fixed_now), "600519.SH", fixed_now)
    assert state.header.symbol == "600519.SH"
    assert state.quote_samples
    assert state.related_announcements
    network.assert_not_called()
```

- [ ] **Step 3: Run tests and confirm missing package**

Run: `python -m pytest tests/test_desktop_state.py tests/test_desktop_formatting.py tests/test_desktop_controller.py -q`

Expected: FAIL with imports from `stock_monitor.desktop_ui`.

- [ ] **Step 4: Implement immutable views, deterministic formatting, and controller**

Formatting rules:

- A-share positive change uses red theme, negative uses green, zero/unknown uses neutral; expose semantic color names instead of hex in the controller.
- Unknown amount/price/time returns `--`, never `0`.
- Freshness is calculated from provider timestamp first, collection time only as fallback and labelled `采集时间`.
- A `suspect` or `stalled` snapshot may display the cached number but its badge and status message must remain visible.

Controller catches repository read failures per panel and returns an empty tuple plus a bounded message; one panel failure does not erase other panels.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_desktop_state.py tests/test_desktop_formatting.py tests/test_desktop_controller.py -q`

Expected: PASS.

- [ ] **Step 6: Commit state and controller**

```powershell
git add -- src/stock_monitor/desktop_ui/__init__.py src/stock_monitor/desktop_ui/pages/__init__.py src/stock_monitor/desktop_ui/widgets/__init__.py src/stock_monitor/desktop_ui/state.py src/stock_monitor/desktop_ui/formatting.py src/stock_monitor/desktop_ui/controller.py tests/test_desktop_state.py tests/test_desktop_formatting.py tests/test_desktop_controller.py
git commit -m "refactor: add cache-first desktop presentation layer"
```

### Task 2: Build global search, watchlist, sector navigation, and safe external links

**Files:**

- Create: `src/stock_monitor/desktop_ui/external_links.py`
- Create: `src/stock_monitor/desktop_ui/widgets/instrument_search.py`
- Create: `src/stock_monitor/desktop_ui/widgets/watchlist.py`
- Create: `tests/test_external_links.py`
- Modify: `tests/test_desktop_controller.py`

- [ ] **Step 1: Write failing external-link tests**

```python
def test_tonghuashun_url_uses_only_normalized_six_digit_code():
    assert tonghuashun_public_stock_url("600519.SH") == "https://stockpage.10jqka.com.cn/600519/"

@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///C:/secret", "data:text/plain,x", "https://"])
def test_external_link_service_rejects_unsafe_urls(url):
    opener = Mock()
    with pytest.raises(ValueError):
        ExternalLinkService(opener).open_source_url(url)
    opener.assert_not_called()

def test_open_tonghuashun_is_user_action_only():
    opener = Mock(return_value=True)
    service = ExternalLinkService(opener)
    assert service.open_tonghuashun("000001.SZ") is True
    opener.assert_called_once_with("https://stockpage.10jqka.com.cn/000001/")
```

- [ ] **Step 2: Add failing search interaction tests at the controller boundary**

```python
def test_search_selection_updates_active_symbol(controller):
    state = controller.search(controller.initial_state(fixed_now), "gzmt")
    selected = controller.select_instrument(state, state.search_results[0]["symbol"], fixed_now)
    assert selected.active_symbol == "600519.SH"

def test_blank_search_closes_result_popup(controller):
    state = controller.search(controller.initial_state(fixed_now), "")
    assert state.search_results == ()
```

- [ ] **Step 3: Run tests and confirm missing behavior**

Run: `python -m pytest tests/test_external_links.py tests/test_desktop_controller.py -q`

Expected: FAIL.

- [ ] **Step 4: Implement links and Tk widgets**

`InstrumentSearch` debounces keystrokes by 150 ms with `after_cancel`, displays at most 20 local results, supports Up/Down/Enter/Escape, and never starts a network request. `WatchlistWidget` and sector tree emit only symbol/sector selection callbacks. Widget constructors receive callbacks and semantic style names; they do not import the controller or repository.

The public Tonghuashun URL template was verified on 2026-08-13 with `https://stockpage.10jqka.com.cn/600519/`; the returned page identifies 贵州茅台 (600519). Treat this as an external public link, not a data contract. A navigation failure only reports that the browser could not open the link and never triggers scraping or a fallback private endpoint.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_external_links.py tests/test_desktop_controller.py -q`

Expected: PASS.

- [ ] **Step 6: Commit navigation components**

```powershell
git add -- src/stock_monitor/desktop_ui/external_links.py src/stock_monitor/desktop_ui/widgets/instrument_search.py src/stock_monitor/desktop_ui/widgets/watchlist.py tests/test_external_links.py tests/test_desktop_controller.py
git commit -m "feat: add local instrument navigation and public links"
```

### Task 3: Build center detail area with intraday, K-line, funds, announcements, and news

**Files:**

- Create: `src/stock_monitor/desktop_ui/widgets/intraday_chart.py`
- Create: `src/stock_monitor/desktop_ui/widgets/source_badge.py`
- Create: `src/stock_monitor/desktop_ui/pages/workbench.py`
- Modify: `src/stock_monitor/chart.py`
- Modify: `tests/test_analysis.py`
- Modify: `tests/test_desktop_state.py`

- [ ] **Step 1: Write failing chart preparation tests**

```python
def test_intraday_series_keeps_unadjusted_prices_and_orders_time():
    rows = [quote("10:01", 10.2), quote("10:00", 10.0)]
    series = prepare_intraday_series(rows)
    assert list(series["last_price"]) == [10.0, 10.2]
    assert "qfq" not in series.columns

def test_kline_basis_is_explicit():
    frame, label = prepare_kline_frame(seed_bars(), period="daily", basis="qfq")
    assert label == "日K · 前复权"
    assert list(frame["display_close"]) == list(frame["qfq_close"])

def test_weekly_kline_does_not_average_prices():
    frame, _ = prepare_kline_frame(seed_two_weeks(), period="weekly", basis="raw")
    assert frame.iloc[0]["display_open"] == seed_two_weeks().iloc[0]["raw_open"]
    assert frame.iloc[0]["display_high"] == seed_two_weeks().iloc[:5]["raw_high"].max()
    assert frame.iloc[0]["display_close"] == seed_two_weeks().iloc[4]["raw_close"]
```

- [ ] **Step 2: Run tests and confirm preparation functions are absent**

Run: `python -m pytest tests/test_analysis.py tests/test_desktop_state.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement pure chart preparation and reusable widgets**

Extract frame preparation from drawing so tests need no Tk root. `IntradayChart` draws a price line and volume bars from `quote_samples`; gaps larger than 180 seconds break the line. `StockChart` accepts `period in {daily, weekly}` and `basis in {raw, qfq}` and renders the basis in its title.

`SourceBadge` always shows source, provider time, collection time and state tooltip. It may not hide a degraded/stalled badge when values are present.

- [ ] **Step 4: Assemble `WorkbenchPage`**

Approved layout:

- Top of center: name, code, industry, latest price, change, source badge, `立即刷新`, `同花顺查询`.
- Center notebook: `分时`, `K线`, `资金流`, `公告资讯`.
- Funds table: main/super-large/large/medium/small net amounts and provider methodology note.
- Announcement/news table: time, source, title, related symbol; double click calls `ExternalLinkService.open_source_url()`.
- Missing fields display `--`; missing order book displays the fixed unavailable panel created in Task 4.

- [ ] **Step 5: Run analysis and state tests**

Run: `python -m pytest tests/test_analysis.py tests/test_desktop_state.py tests/test_desktop_controller.py -q`

Expected: PASS.

- [ ] **Step 6: Commit center workbench**

```powershell
git add -- src/stock_monitor/desktop_ui/widgets/intraday_chart.py src/stock_monitor/desktop_ui/widgets/source_badge.py src/stock_monitor/desktop_ui/pages/workbench.py src/stock_monitor/chart.py tests/test_analysis.py tests/test_desktop_state.py
git commit -m "feat: add linked stock detail workbench"
```

### Task 4: Build right-side order-book placeholder, sector linkage, and evidence panel

**Files:**

- Create: `src/stock_monitor/desktop_ui/widgets/evidence_panel.py`
- Modify: `src/stock_monitor/desktop_ui/pages/workbench.py`
- Modify: `src/stock_monitor/desktop_ui/state.py`
- Modify: `tests/test_desktop_state.py`

- [ ] **Step 1: Write failing right-panel projection tests**

```python
def test_order_book_is_unavailable_without_valid_depth_contract():
    panel = project_order_book(snapshot=valid_snapshot(), depth=None)
    assert panel.available is False
    assert panel.message == "盘口数据不可用"
    assert panel.rows == ()

def test_sector_linkage_keeps_taxonomy_and_source_visible():
    rows = project_sector_links(instrument_with_industry_and_concepts(), sector_snapshots())
    assert {(row.taxonomy, row.source) for row in rows} == {
        ("industry", "akshare/public"), ("concept", "akshare/public")
    }

def test_rule_summary_is_not_labeled_as_model_summary():
    view = project_summary(rule_only_summary())
    assert view.provider_label == "本地规则摘要"
    assert view.sections[0].title == "客观事实"
```

- [ ] **Step 2: Run state tests and observe missing projections**

Run: `python -m pytest tests/test_desktop_state.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement projections and evidence widget**

Right column order:

1. `盘口` — only validated depth data may populate it; otherwise fixed unavailable message and source requirement note.
2. `板块联动` — industry, concepts, broad index relation and relative strength when available, with taxonomy/source labels.
3. `证据摘要` — facts, inference, counter-evidence, scenarios, invalidation and review time. Until the AI phase exists, render the existing local rule/research result as `本地规则摘要` and retain original links.

The widget exposes clickable evidence IDs/URLs but never interprets or rewrites evidence.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_desktop_state.py tests/test_research.py tests/test_news_analysis.py -q`

Expected: PASS.

- [ ] **Step 5: Commit right-side panels**

```powershell
git add -- src/stock_monitor/desktop_ui/widgets/evidence_panel.py src/stock_monitor/desktop_ui/pages/workbench.py src/stock_monitor/desktop_ui/state.py tests/test_desktop_state.py
git commit -m "feat: add auditable market context panels"
```

### Task 5: Replace monolithic UI construction with shell and event-driven background cycles

**Files:**

- Create: `src/stock_monitor/desktop_ui/shell.py`
- Create: `src/stock_monitor/desktop_ui/pages/settings.py`
- Modify: `src/stock_monitor/desktop.py`
- Modify: `src/stock_monitor/config.py`
- Modify: `tests/test_config.py`
- Create: `tests/test_desktop_smoke.py`

- [ ] **Step 1: Write failing shell lifecycle tests without opening a visible window**

```python
def test_smoke_mode_builds_shell_and_closes(monkeypatch):
    root = FakeRoot()
    app = create_desktop_app(root=root, smoke_test=True)
    assert app.controller is not None
    assert root.after_calls
    app.close()
    assert root.destroyed is True

def test_background_completion_is_applied_only_on_ui_thread(app):
    event = UiEvent("cycle_complete", completed_cycle_payload())
    app.event_queue.put(event)
    app.drain_events()
    assert app.current_state.busy is False
    assert app.workbench.last_rendered_state == app.current_state

def test_background_error_does_not_stop_event_pump(app):
    app.event_queue.put(UiEvent("operation_error", "timeout"))
    app.drain_events()
    assert "timeout" in app.current_state.status_message
    assert app.root.after_calls[-1].delay_ms == 100
```

- [ ] **Step 2: Run smoke/config tests and confirm shell is absent**

Run: `python -m pytest tests/test_desktop_smoke.py tests/test_config.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement shell and migrate settings**

The shell must contain:

- Top bar: local search, market phase, primary source/state, newest provider time, next-refresh countdown, manual refresh and settings.
- Left column: watchlist, industry/concept tree, hot sectors and main net inflow/outflow ranking.
- Center column: `WorkbenchPage`.
- Right column: order-book status, sector linkage and evidence summary.
- Bottom bar: concise operation status, advanced stack status and persistent risk disclaimer.

Move current settings fields and secret handling to `desktop_ui/pages/settings.py` without changing `ConfigStore` or `SecretStore` locations. Add config fields:

```python
start_automatic_monitoring: bool = True
window_geometry: str = "1440x900"
last_active_symbol: str = ""
left_panel_width: int = Field(default=260, ge=200, le=500)
right_panel_width: int = Field(default=340, ge=260, le=600)
```

Run at most one background automation cycle at a time. A manual refresh while busy sets one coalesced rerun flag; it must not start a concurrent provider call. All Tk mutations remain in `drain_events()` on the main thread.

- [ ] **Step 4: Reduce `desktop.py` to a compatibility composition root**

Keep `DesktopApp` import compatibility by aliasing/importing the new shell class. Change `main(argv: Sequence[str] | None = None)` to parse only `--smoke-test`, create services, and exit automatically after the first successful render in smoke mode. Existing `stock-monitor-desktop` and `desktop_launcher.py` entrypoints must keep working.

- [ ] **Step 5: Run desktop and regression tests**

Run: `python -m pytest tests/test_desktop_smoke.py tests/test_config.py tests/test_sync.py tests/test_storage.py -q`

Expected: PASS.

- [ ] **Step 6: Commit shell migration**

```powershell
git add -- src/stock_monitor/desktop_ui/shell.py src/stock_monitor/desktop_ui/pages/settings.py src/stock_monitor/desktop.py src/stock_monitor/config.py tests/test_config.py tests/test_desktop_smoke.py
git commit -m "refactor: compose desktop terminal from pages and widgets"
```

### Task 6: Package, smoke-test, and update the desktop copy

**Files:**

- Modify: `desktop_launcher.py`
- Modify: `desktop.spec`
- Modify: `build_desktop.ps1`
- Modify: `README.md`
- Modify: `tests/test_desktop_smoke.py`

- [ ] **Step 1: Add failing launcher argument test**

```python
def test_launcher_forwards_smoke_test(monkeypatch):
    called = {}
    monkeypatch.setattr("stock_monitor.desktop.main", lambda argv=None: called.setdefault("argv", argv))
    run_launcher(["--smoke-test"])
    assert called["argv"] == ["--smoke-test"]
```

- [ ] **Step 2: Run the launcher test and confirm current launcher cannot inject arguments**

Run: `python -m pytest tests/test_desktop_smoke.py -q`

Expected: FAIL.

- [ ] **Step 3: Update launcher, PyInstaller spec, and build script**

Build flow is exact:

1. `python -m pytest -q`
2. `python -m compileall -q src`
3. Install `.[desktop-build,providers,streaming]` into the build environment.
4. `python -m PyInstaller --noconfirm --clean desktop.spec`
5. `dist\A股智能监控终端\A股智能监控终端.exe --smoke-test`
6. Copy the complete one-folder distribution to a resolved staging directory under `%USERPROFILE%\Desktop`, smoke-test it there, rename any existing app folder to a timestamped sibling backup, rename staging to `A股智能监控终端`, launch the final path once, and restore the backup if that launch fails.

Before replacing the desktop distribution, resolve staging, target and backup paths and assert all are direct children of the Desktop directory. Keep the previous backup until the user has accepted the new build; report its exact location and recovery procedure. Never delete the app data directory returned by `default_app_dir()`; the EXE update must not touch config, SQLite or keyring data.

- [ ] **Step 4: Document the final UI behavior**

README includes the source-state legend, search shortcuts, manual refresh semantics, data-source settings, Tonghuashun public-link boundary, unavailable depth behavior, automatic update targets and investment-risk disclaimer.

- [ ] **Step 5: Run final verification**

Run: `python -m pytest -q`

Expected: all tests pass.

Run: `python -m compileall -q src`

Expected: exit code 0.

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File .\build_desktop.ps1`

Expected: PyInstaller succeeds, packaged smoke test exits 0, desktop copy is updated only after success.

- [ ] **Step 6: Launch the desktop copy once for visual acceptance**

Check at 1440×900 and Windows 125% scaling:

- Search popup remains inside the top bar and keyboard selection works.
- Left/center/right columns are readable without horizontal clipping.
- Switching watchlist symbols updates all center/right panels together.
- Source/timestamp/state is visible without opening settings.
- Missing depth shows `盘口数据不可用`.
- The risk disclaimer remains visible.

- [ ] **Step 7: Commit packaging and documentation**

```powershell
git add -- desktop_launcher.py desktop.spec build_desktop.ps1 README.md tests/test_desktop_smoke.py
git commit -m "build: package and smoke-test desktop terminal"
```

## Phase acceptance gate

- [ ] Local search accepts code, Chinese name and pinyin abbreviation and updates selection without network access.
- [ ] Selecting a stock renders cached content immediately; the background refresh never freezes Tk.
- [ ] Watchlist, sector, chart, funds, announcements/news and evidence panels remain synchronized to one active symbol.
- [ ] Every market number exposes source, provider timestamp, collection timestamp and quality state.
- [ ] The Tonghuashun button opens only the public stock URL after a user click; no browser/client automation exists.
- [ ] Unavailable depth never renders fabricated bid/ask rows.
- [ ] Existing config, watchlist, database and Windows keyring values survive the EXE update.
- [ ] Desktop EXE passes automated smoke test and launches from the desktop folder.
