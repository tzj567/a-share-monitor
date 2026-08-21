"""Native Windows desktop front end built with Tk/ttk."""

from __future__ import annotations

import queue
import threading
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, TOP, W, X, Y, BooleanVar, StringVar, Text, Tk, filedialog, messagebox
from tkinter import ttk
from typing import Any

from .analysis import analyze_stock
from .chart import StockChart
from .config import ConfigStore, DesktopConfig, SecretStore
from .data_sources.common import ProviderError
from .models import WatchItem
from .research import ResearchSignalEngine
from .storage import SQLiteRepository
from .sync import SyncService, SyncSummary


PROVIDER_LABELS = {
    "CSV 文件": "csv",
    "AkShare（公开数据）": "akshare",
    "TuShare Pro": "tushare",
    "同花顺 iFinD QuantAPI": "ifind",
}
PROVIDER_LABEL_BY_ID = {value: key for key, value in PROVIDER_LABELS.items()}
VOLUME_UNIT_LABELS = {"股（shares）": "shares", "手（lots，入库×100）": "lots"}
VOLUME_UNIT_LABEL_BY_ID = {value: key for key, value in VOLUME_UNIT_LABELS.items()}


class DesktopApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("A 股量化监控")
        self.root.geometry("1240x780")
        self.root.minsize(980, 640)
        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.secrets = SecretStore()
        database_path = self.config.resolved_database_path()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.repository = SQLiteRepository(database_path)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy_count = 0

        self.status_var = StringVar(value="就绪")
        self.symbol_var = StringVar()
        self.name_var = StringVar()
        self.analysis_symbol_var = StringVar()
        self.analysis_period_var = StringVar(value="120")
        self.analysis_heading_var = StringVar(value="请选择股票")
        self.analysis_metrics_var = StringVar(value="同步行情后查看趋势指标")
        self.analysis_risk_var = StringVar(value="风险：--")
        self.analysis_evidence_var = StringVar(value="")
        self.monitor_interval_var = StringVar(value=str(self.config.auto_monitor_interval_minutes))
        self.monitor_button_var = StringVar(value="自动监控：关闭")
        self.radar_health_var = StringVar(value="等待自动同步数据源状态")
        self.radar_summary_var = StringVar(value="市场摘要将在资金流或授权资讯同步后自动生成。")
        self.research_summary_var = StringVar(value="同步数据后生成多维观察清单。")
        self.research_signals: dict[str, Any] = {}
        self.auto_monitoring = False
        self.auto_monitor_after_id: str | None = None
        self.provider_var = StringVar(value=PROVIDER_LABEL_BY_ID.get(self.config.provider, "AkShare（公开数据）"))
        self.sync_days_var = StringVar(value=str(self.config.sync_days))
        self.csv_bars_var = StringVar(value=self.config.csv_bars_path)
        self.csv_closes_var = StringVar(value=self.config.csv_reference_closes_path)
        self.csv_volume_unit_var = StringVar(value=VOLUME_UNIT_LABEL_BY_ID[self.config.csv_volume_unit])
        self.tushare_token_var = StringVar()
        self.ifind_token_var = StringVar()
        self.ifind_url_var = StringVar(value=self.config.ifind_base_url)
        self.ifind_volume_unit_var = StringVar(value=VOLUME_UNIT_LABEL_BY_ID[self.config.ifind_volume_unit])
        self.cls_base_var = StringVar(value=self.config.cls_api_base_url)
        self.cls_endpoint_var = StringVar(value=self.config.cls_news_endpoint)
        self.cls_token_var = StringVar()
        self.advanced_mode_var = BooleanVar(value=self.config.advanced_mode)
        self.kafka_bootstrap_var = StringVar(value=self.config.kafka_bootstrap_servers)
        self.tdengine_url_var = StringVar(value=self.config.tdengine_rest_url)
        self.tdengine_database_var = StringVar(value=self.config.tdengine_database)
        self.tdengine_user_var = StringVar(value=self.config.tdengine_user)
        self.tdengine_password_var = StringVar()
        self.flink_dashboard_var = StringVar(value=self.config.flink_dashboard_url)

        self._configure_style()
        self._build_ui()
        self.refresh_all()
        self.root.after(150, self._drain_events)
        if self.config.auto_monitor_on_start:
            self.root.after(800, self._start_automatic_monitoring)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Hint.TLabel", foreground="#5f6b7a")
        style.configure("Treeview", rowheight=27)

    def _build_ui(self) -> None:
        header = ttk.Frame(self.root, padding=(16, 12))
        header.pack(side=TOP, fill=X)
        ttk.Label(header, text="A 股量化监控", style="Title.TLabel").pack(side=LEFT)
        ttk.Button(header, text="同步行情", command=self.sync_market).pack(side=RIGHT, padx=4)
        ttk.Button(header, text="评估规则", command=self.evaluate_all).pack(side=RIGHT, padx=4)
        ttk.Button(header, text="刷新", command=self.refresh_all).pack(side=RIGHT, padx=4)
        ttk.Button(header, textvariable=self.monitor_button_var, command=self.toggle_auto_monitor).pack(side=RIGHT, padx=4)
        ttk.Label(header, text="分钟").pack(side=RIGHT)
        ttk.Combobox(header, textvariable=self.monitor_interval_var, values=("5", "15", "30", "60"), state="readonly", width=5).pack(side=RIGHT, padx=(4, 2))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=12, pady=(0, 8))
        self.dashboard_tab = ttk.Frame(self.notebook, padding=10)
        self.analysis_tab = ttk.Frame(self.notebook, padding=10)
        self.watchlist_tab = ttk.Frame(self.notebook, padding=10)
        self.news_tab = ttk.Frame(self.notebook, padding=10)
        self.radar_tab = ttk.Frame(self.notebook, padding=10)
        self.research_tab = ttk.Frame(self.notebook, padding=10)
        self.settings_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.dashboard_tab, text="监控中心")
        self.notebook.add(self.analysis_tab, text="可视化研判")
        self.notebook.add(self.research_tab, text="智能筛选")
        self.notebook.add(self.radar_tab, text="市场雷达")
        self.notebook.add(self.watchlist_tab, text="观察列表")
        self.notebook.add(self.news_tab, text="财联社资讯")
        self.notebook.add(self.settings_tab, text="数据源设置")
        self._build_dashboard()
        self._build_analysis()
        self._build_research()
        self._build_radar()
        self._build_watchlist()
        self._build_news()
        self._build_settings()

        status = ttk.Frame(self.root, padding=(12, 5))
        status.pack(side=TOP, fill=X)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=160)
        self.progress.pack(side=RIGHT)
        ttk.Label(status, textvariable=self.status_var).pack(side=LEFT)

    def _build_dashboard(self) -> None:
        pane = ttk.Panedwindow(self.dashboard_tab, orient="vertical")
        pane.pack(fill=BOTH, expand=True)
        bars_box = ttk.Labelframe(pane, text="最新行情", padding=6)
        alerts_box = ttk.Labelframe(pane, text="最近告警", padding=6)
        pane.add(bars_box, weight=1)
        pane.add(alerts_box, weight=1)
        self.bars_tree = self._tree(bars_box, ("symbol", "date", "close", "volume", "state", "source"), ("代码", "交易日", "最新价/收盘", "成交量(股)", "状态", "来源"), (120, 130, 120, 150, 100, 120))
        self.alerts_tree = self._tree(alerts_box, ("time", "symbol", "rule", "message"), ("时间(UTC)", "代码", "规则", "内容"), (190, 110, 170, 600))
        self.bars_tree.bind("<Double-1>", self._open_selected_analysis)

    def _build_analysis(self) -> None:
        controls = ttk.Frame(self.analysis_tab)
        controls.pack(fill=X, pady=(0, 8))
        ttk.Label(controls, text="股票").pack(side=LEFT)
        self.analysis_symbol_combo = ttk.Combobox(controls, textvariable=self.analysis_symbol_var, state="readonly", width=18)
        self.analysis_symbol_combo.pack(side=LEFT, padx=6)
        ttk.Label(controls, text="观察窗口").pack(side=LEFT, padx=(10, 0))
        ttk.Combobox(controls, textvariable=self.analysis_period_var, values=("60", "120", "250"), state="readonly", width=8).pack(side=LEFT, padx=6)
        ttk.Label(controls, text="个交易日").pack(side=LEFT)
        ttk.Button(controls, text="刷新研判", command=self.refresh_analysis).pack(side=LEFT, padx=10)
        ttk.Label(controls, text="仅基于已闭合行情，仅供研究，不构成投资建议", style="Hint.TLabel").pack(side=RIGHT)

        pane = ttk.Panedwindow(self.analysis_tab, orient="horizontal")
        pane.pack(fill=BOTH, expand=True)
        chart_box = ttk.Labelframe(pane, text="价格与成交量", padding=6)
        insight_box = ttk.Labelframe(pane, text="趋势与风险建议", padding=10)
        pane.add(chart_box, weight=3)
        pane.add(insight_box, weight=1)
        self.stock_chart = StockChart(chart_box)
        self.stock_chart.pack(fill=BOTH, expand=True)

        ttk.Label(insight_box, textvariable=self.analysis_heading_var, font=("Microsoft YaHei UI", 14, "bold"), wraplength=310).pack(anchor=W, pady=(2, 8))
        ttk.Label(insight_box, textvariable=self.analysis_metrics_var, wraplength=310, justify=LEFT).pack(anchor=W, fill=X, pady=4)
        ttk.Separator(insight_box).pack(fill=X, pady=8)
        ttk.Label(insight_box, textvariable=self.analysis_risk_var, font=("Microsoft YaHei UI", 11, "bold"), wraplength=310).pack(anchor=W, pady=4)
        self.analysis_suggestion = Text(insight_box, height=7, wrap="word", relief="flat", background="#f6f7f9", font=("Microsoft YaHei UI", 10), padx=8, pady=8)
        self.analysis_suggestion.pack(fill=X, pady=4)
        self.analysis_suggestion.configure(state="disabled")
        ttk.Label(insight_box, text="判断依据", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor=W, pady=(10, 3))
        ttk.Label(insight_box, textvariable=self.analysis_evidence_var, wraplength=310, justify=LEFT, style="Hint.TLabel").pack(anchor=W, fill=X)

    def _build_watchlist(self) -> None:
        form = ttk.Frame(self.watchlist_tab)
        form.pack(fill=X, pady=(0, 8))
        ttk.Label(form, text="股票代码").pack(side=LEFT)
        ttk.Entry(form, textvariable=self.symbol_var, width=18).pack(side=LEFT, padx=6)
        ttk.Label(form, text="名称").pack(side=LEFT)
        ttk.Entry(form, textvariable=self.name_var, width=24).pack(side=LEFT, padx=6)
        ttk.Button(form, text="添加/更新", command=self.add_watch_item).pack(side=LEFT, padx=4)
        ttk.Button(form, text="删除选中", command=self.remove_watch_item).pack(side=LEFT, padx=4)
        self.watch_tree = self._tree(self.watchlist_tab, ("symbol", "name", "enabled"), ("代码", "名称", "启用"), (180, 320, 100))

    def _build_research(self) -> None:
        controls = ttk.Frame(self.research_tab)
        controls.pack(fill=X, pady=(0, 8))
        ttk.Button(controls, text="刷新智能筛选", command=self.refresh_research).pack(side=LEFT, padx=4)
        ttk.Label(
            controls,
            text="趋势 + 资金流 + 授权资讯 + 风险惩罚；评分仅用于排序观察，不是买卖指令",
            style="Hint.TLabel",
        ).pack(side=LEFT, padx=10)
        ttk.Label(
            self.research_tab,
            textvariable=self.research_summary_var,
            wraplength=1500,
            justify=LEFT,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill=X, padx=6, pady=(0, 8))

        pane = ttk.Panedwindow(self.research_tab, orient="horizontal")
        pane.pack(fill=BOTH, expand=True)
        ranking_box = ttk.Labelframe(pane, text="多维观察清单", padding=6)
        detail_box = ttk.Labelframe(pane, text="证据、不确定性与复核条件", padding=8)
        pane.add(ranking_box, weight=3)
        pane.add(detail_box, weight=2)
        self.research_tree = self._tree(
            ranking_box,
            ("symbol", "name", "score", "state", "confidence", "trend", "flow", "news", "risk"),
            ("代码", "名称", "评分", "状态", "置信度", "趋势", "资金", "资讯", "风险"),
            (105, 95, 65, 130, 75, 65, 65, 65, 90),
        )
        self.research_tree.bind("<<TreeviewSelect>>", self._show_research_detail)
        self.research_detail = Text(
            detail_box,
            wrap="word",
            relief="flat",
            background="#f6f7f9",
            font=("Microsoft YaHei UI", 10),
            padx=10,
            pady=10,
        )
        self.research_detail.pack(fill=BOTH, expand=True)
        self.research_detail.insert("1.0", "选择一只股票查看评分依据。")
        self.research_detail.configure(state="disabled")

    def _build_radar(self) -> None:
        controls = ttk.Frame(self.radar_tab)
        controls.pack(fill=X, pady=(0, 8))
        ttk.Button(controls, text="立即更新市场雷达", command=self.sync_radar).pack(side=LEFT, padx=4)
        ttk.Label(controls, text="资金流优先 iFinD，异常时自动降级 AkShare；财联社仅走授权 API", style="Hint.TLabel").pack(side=LEFT, padx=10)
        ttk.Label(
            self.radar_tab,
            textvariable=self.radar_summary_var,
            wraplength=1500,
            justify=LEFT,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill=X, padx=6, pady=(0, 8))

        health = ttk.Frame(self.radar_tab, padding=(4, 6))
        health.pack(side="bottom", fill=X)
        ttk.Label(health, text="数据健康：", font=("Microsoft YaHei UI", 9, "bold")).pack(side=LEFT)
        ttk.Label(health, textvariable=self.radar_health_var, style="Hint.TLabel", wraplength=1450).pack(side=LEFT, fill=X, expand=True)

        pane = ttk.Panedwindow(self.radar_tab, orient="vertical")
        pane.pack(fill=BOTH, expand=True)
        flow_pane = ttk.Panedwindow(pane, orient="horizontal")
        inflow_box = ttk.Labelframe(flow_pane, text="个股主力净流入", padding=6)
        outflow_box = ttk.Labelframe(flow_pane, text="个股主力净流出", padding=6)
        sector_box = ttk.Labelframe(flow_pane, text="行业板块净流入", padding=6)
        flow_pane.add(inflow_box, weight=1)
        flow_pane.add(outflow_box, weight=1)
        flow_pane.add(sector_box, weight=1)
        pane.add(flow_pane, weight=2)
        self.inflow_tree = self._tree(inflow_box, ("name", "flow", "ratio", "change", "source"), ("股票", "主力净额", "净占比", "涨跌", "来源"), (95, 75, 55, 50, 75))
        self.outflow_tree = self._tree(outflow_box, ("name", "flow", "ratio", "change", "source"), ("股票", "主力净额", "净占比", "涨跌", "来源"), (95, 75, 55, 50, 75))
        self.sector_tree = self._tree(sector_box, ("name", "flow", "ratio", "change", "source"), ("板块", "主力净额", "净占比", "涨跌", "来源"), (95, 75, 55, 50, 75))

        news_box = ttk.Labelframe(pane, text="利好 / 利空事件摘要（点击查看证据，双击打开原文）", padding=6)
        pane.add(news_box, weight=2)
        self.radar_news_tree = self._tree(news_box, ("time", "sentiment", "confidence", "title", "symbols", "evidence", "url"), ("时间", "标签", "置信度", "标题", "相关股票", "原文证据", "链接"), (155, 65, 75, 430, 130, 170, 180))
        self.radar_news_tree.bind("<Double-1>", self._open_selected_radar_news)


    def _build_news(self) -> None:
        buttons = ttk.Frame(self.news_tab)
        buttons.pack(fill=X, pady=(0, 8))
        ttk.Button(buttons, text="同步授权资讯", command=self.sync_news).pack(side=LEFT, padx=4)
        ttk.Button(buttons, text="打开财联社官网", command=lambda: webbrowser.open("https://www.cls.cn/telegraph")).pack(side=LEFT, padx=4)
        ttk.Label(buttons, text="未配置书面授权 API 时不会抓取网站内容", style="Hint.TLabel").pack(side=LEFT, padx=10)
        self.news_tree = self._tree(self.news_tab, ("time", "title", "symbols", "url"), ("时间(UTC)", "标题", "相关代码", "链接"), (190, 570, 160, 280))
        self.news_tree.bind("<Double-1>", self._open_selected_news)

    def _build_settings(self) -> None:
        settings_pages = ttk.Notebook(self.settings_tab)
        settings_pages.pack(fill=BOTH, expand=True)
        standard_page = ttk.Frame(settings_pages, padding=8)
        advanced_page = ttk.Frame(settings_pages, padding=8)
        settings_pages.add(standard_page, text="行情与资讯")
        settings_pages.add(advanced_page, text="高级数据架构")

        market = ttk.Labelframe(standard_page, text="行情数据源", padding=12)
        market.pack(fill=X, pady=6)
        self._grid_field(market, 0, "当前数据源", ttk.Combobox(market, textvariable=self.provider_var, values=list(PROVIDER_LABELS), state="readonly", width=34))
        self._grid_field(market, 1, "同步历史天数", ttk.Entry(market, textvariable=self.sync_days_var, width=36))
        self._grid_path_field(market, 2, "CSV K 线", self.csv_bars_var)
        self._grid_path_field(market, 3, "CSV 昨收", self.csv_closes_var)
        self._grid_field(market, 4, "CSV 成交量单位", ttk.Combobox(market, textvariable=self.csv_volume_unit_var, values=list(VOLUME_UNIT_LABELS), state="readonly", width=34))
        self._grid_field(market, 5, "TuShare Token", ttk.Entry(market, textvariable=self.tushare_token_var, show="●", width=38))
        self._grid_field(market, 6, "iFinD refresh_token", ttk.Entry(market, textvariable=self.ifind_token_var, show="●", width=38))
        self._grid_field(market, 7, "iFinD API 地址", ttk.Entry(market, textvariable=self.ifind_url_var, width=58))
        self._grid_field(market, 8, "iFinD 成交量单位", ttk.Combobox(market, textvariable=self.ifind_volume_unit_var, values=list(VOLUME_UNIT_LABELS), state="readonly", width=34))
        ttk.Label(market, text="Token 输入框留空表示保留 Windows 凭据库中的原值。", style="Hint.TLabel").grid(row=9, column=1, sticky=W, pady=3)

        news = ttk.Labelframe(standard_page, text="财联社授权资讯", padding=12)
        news.pack(fill=X, pady=6)
        self._grid_field(news, 0, "授权 API 地址", ttk.Entry(news, textvariable=self.cls_base_var, width=58))
        self._grid_field(news, 1, "资讯端点", ttk.Entry(news, textvariable=self.cls_endpoint_var, width=58))
        self._grid_field(news, 2, "授权 Token", ttk.Entry(news, textvariable=self.cls_token_var, show="●", width=38))
        ttk.Label(news, text="财联社官网声明禁止未经书面授权复制或使用内容，因此只支持合同授权 API。", style="Hint.TLabel").grid(row=3, column=1, sticky=W, pady=3)

        advanced = ttk.Labelframe(advanced_page, text="高级架构（Kafka + Flink + TDengine）", padding=12)
        advanced.pack(fill=X, pady=6)
        self._grid_field(
            advanced,
            0,
            "启用高级数据流",
            ttk.Checkbutton(advanced, variable=self.advanced_mode_var, text="启用；SQLite 保留控制面与断网待发送队列"),
        )
        self._grid_field(advanced, 1, "Kafka 地址", ttk.Entry(advanced, textvariable=self.kafka_bootstrap_var, width=58))
        self._grid_field(advanced, 2, "TDengine REST", ttk.Entry(advanced, textvariable=self.tdengine_url_var, width=58))
        self._grid_field(advanced, 3, "TDengine 数据库", ttk.Entry(advanced, textvariable=self.tdengine_database_var, width=38))
        self._grid_field(advanced, 4, "TDengine 用户", ttk.Entry(advanced, textvariable=self.tdengine_user_var, width=38))
        self._grid_field(advanced, 5, "TDengine 密码", ttk.Entry(advanced, textvariable=self.tdengine_password_var, show="●", width=38))
        self._grid_field(advanced, 6, "Flink 控制台", ttk.Entry(advanced, textvariable=self.flink_dashboard_var, width=58))
        ttk.Label(
            advanced,
            text="先运行 start_advanced_stack.ps1。密码留空表示保留 Windows 凭据库原值；高级栈异常不会阻断本地监控。",
            style="Hint.TLabel",
        ).grid(row=7, column=1, sticky=W, pady=3)

        advanced_actions = ttk.Frame(advanced_page)
        advanced_actions.pack(fill=X, pady=10)
        ttk.Button(advanced_actions, text="保存高级设置", command=self.save_settings).pack(side=LEFT, padx=4)
        ttk.Button(advanced_actions, text="测试 Kafka / Flink / TDengine", command=self.test_advanced).pack(side=LEFT, padx=4)
        ttk.Button(advanced_actions, text="打开 Flink 控制台", command=lambda: webbrowser.open(self.flink_dashboard_var.get())).pack(side=LEFT, padx=4)

        actions = ttk.Frame(standard_page)
        actions.pack(fill=X, pady=10)
        ttk.Button(actions, text="保存设置", command=self.save_settings).pack(side=LEFT, padx=4)
        ttk.Button(actions, text="测试行情连接", command=self.test_market).pack(side=LEFT, padx=4)
        ttk.Button(actions, text="测试财联社连接", command=self.test_news).pack(side=LEFT, padx=4)
        ttk.Button(actions, text="打开 iFinD 接口文档", command=lambda: webbrowser.open("https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/")).pack(side=LEFT, padx=4)

    def _tree(self, parent: Any, columns: tuple[str, ...], headings: tuple[str, ...], widths: tuple[int, ...]) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill=BOTH, expand=True)
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        for column, heading, width in zip(columns, headings, widths, strict=True):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor=W)
        return tree

    @staticmethod
    def _grid_field(parent: Any, row: int, label: str, widget: Any) -> None:
        ttk.Label(parent, text=label, width=20).grid(row=row, column=0, sticky=W, pady=4)
        widget.grid(row=row, column=1, sticky=W, pady=4)

    def _grid_path_field(self, parent: Any, row: int, label: str, variable: StringVar) -> None:
        ttk.Label(parent, text=label, width=20).grid(row=row, column=0, sticky=W, pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=58)
        entry.grid(row=row, column=1, sticky=W, pady=4)
        ttk.Button(parent, text="浏览", command=lambda: self._choose_file(variable)).grid(row=row, column=2, padx=5)

    def _choose_file(self, variable: StringVar) -> None:
        selected = filedialog.askopenfilename(title="选择 CSV 文件", filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")])
        if selected:
            variable.set(selected)

    def add_watch_item(self) -> None:
        try:
            item = WatchItem(symbol=self.symbol_var.get(), name=self.name_var.get() or None)
            self.repository.save_watch_item(item)
        except Exception as error:
            messagebox.showerror("无法添加", str(error), parent=self.root)
            return
        self.symbol_var.set("")
        self.name_var.set("")
        self.refresh_all()

    def remove_watch_item(self) -> None:
        selected = self.watch_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择一只股票", parent=self.root)
            return
        symbol = str(self.watch_tree.item(selected[0], "values")[0])
        if messagebox.askyesno("确认删除", f"从观察列表删除 {symbol}？", parent=self.root):
            self.repository.remove_watch_item(symbol)
            self.refresh_all()

    def save_settings(self, *, notify: bool = True) -> bool:
        try:
            config = DesktopConfig(
                provider=PROVIDER_LABELS[self.provider_var.get()],
                database_path=str(self.config.resolved_database_path()),
                csv_bars_path=self.csv_bars_var.get(),
                csv_reference_closes_path=self.csv_closes_var.get(),
                csv_volume_unit=VOLUME_UNIT_LABELS[self.csv_volume_unit_var.get()],
                ifind_base_url=self.ifind_url_var.get(),
                ifind_volume_unit=VOLUME_UNIT_LABELS[self.ifind_volume_unit_var.get()],
                cls_api_base_url=self.cls_base_var.get(),
                cls_news_endpoint=self.cls_endpoint_var.get(),
                fund_flow_provider=self.config.fund_flow_provider,
                auto_sync_news=self.config.auto_sync_news,
                auto_sync_fund_flow=self.config.auto_sync_fund_flow,
                auto_monitor_on_start=self.config.auto_monitor_on_start,
                auto_monitor_interval_minutes=int(self.monitor_interval_var.get()),
                sync_days=int(self.sync_days_var.get()),
                interval="1d",
                auto_evaluate=True,
                advanced_mode=bool(self.advanced_mode_var.get()),
                kafka_bootstrap_servers=self.kafka_bootstrap_var.get(),
                kafka_market_topic=self.config.kafka_market_topic,
                kafka_fund_flow_topic=self.config.kafka_fund_flow_topic,
                kafka_news_topic=self.config.kafka_news_topic,
                tdengine_rest_url=self.tdengine_url_var.get(),
                tdengine_database=self.tdengine_database_var.get(),
                tdengine_user=self.tdengine_user_var.get(),
                flink_dashboard_url=self.flink_dashboard_var.get(),
            )
            self.config_store.save(config)
            for name, variable in (
                ("tushare_token", self.tushare_token_var),
                ("ifind_refresh_token", self.ifind_token_var),
                ("cls_token", self.cls_token_var),
                ("tdengine_password", self.tdengine_password_var),
            ):
                if variable.get().strip():
                    self.secrets.set(name, variable.get().strip())
                    variable.set("")
            self.config = config
        except Exception as error:
            messagebox.showerror("设置保存失败", str(error), parent=self.root)
            return False
        if notify:
            messagebox.showinfo("设置", "设置已保存；凭据已存入 Windows 凭据库", parent=self.root)
        return True

    def _service(self) -> SyncService:
        return SyncService(self.repository, self.config, self.secrets)

    def sync_market(self) -> None:
        if not self.save_settings(notify=False):
            return
        self._run_background("正在同步行情…", self._service().sync_market, self._market_done)

    def toggle_auto_monitor(self) -> None:
        if self.auto_monitoring:
            self.auto_monitoring = False
            if self.auto_monitor_after_id is not None:
                self.root.after_cancel(self.auto_monitor_after_id)
                self.auto_monitor_after_id = None
            self.monitor_button_var.set("自动监控：关闭")
            self.status_var.set("自动监控已停止")
            self.config = self.config.model_copy(update={"auto_monitor_on_start": False})
            self.config_store.save(self.config)
            return
        if not self.save_settings(notify=False):
            return
        self.auto_monitoring = True
        self.config = self.config.model_copy(update={
            "auto_monitor_on_start": True,
            "auto_monitor_interval_minutes": int(self.monitor_interval_var.get()),
        })
        self.config_store.save(self.config)
        self.monitor_button_var.set("自动监控：运行中")
        self._auto_monitor_tick()

    def _start_automatic_monitoring(self) -> None:
        if not self.auto_monitoring:
            self.toggle_auto_monitor()

    def _auto_monitor_tick(self) -> None:
        if not self.auto_monitoring:
            return
        if self.busy_count == 0:
            self._run_background("自动监控正在更新行情、资金流和资讯…", self._auto_sync_cycle, self._auto_cycle_done)
        interval_ms = int(self.monitor_interval_var.get()) * 60 * 1000
        self.auto_monitor_after_id = self.root.after(interval_ms, self._auto_monitor_tick)

    def _auto_sync_cycle(self) -> tuple[SyncSummary, SyncSummary]:
        service = self._service()
        try:
            market = service.sync_market()
        except Exception as error:
            market = SyncSummary(provider="行情", errors=[str(error)])
        try:
            radar = service.sync_radar()
        except Exception as error:
            radar = SyncSummary(provider="市场雷达", errors=[str(error)])
        return market, radar

    def _auto_cycle_done(self, result: tuple[SyncSummary, SyncSummary]) -> None:
        market, radar = result
        message = f"自动监控：行情 {market.symbols} 只，资金流 {radar.fund_flows} 条，资讯新增 {radar.news} 条"
        if market.errors or radar.errors:
            message += f"，{len(market.errors) + len(radar.errors)} 项异常"
        self._operation_done(message)

    def evaluate_all(self) -> None:
        self._run_background("正在评估规则…", self._service().evaluate_all, lambda results: self._operation_done(f"规则评估完成：{sum(len(item.alerts) for item in results)} 条新告警"))

    def sync_news(self) -> None:
        if not self.save_settings(notify=False):
            return
        self._run_background("正在同步财联社授权资讯…", self._service().sync_news, lambda summary: self._operation_done(f"资讯同步完成：新增 {summary.news} 条"))

    def sync_radar(self) -> None:
        if not self.save_settings(notify=False):
            return
        self._run_background("正在更新资金流和授权资讯…", self._service().sync_radar, self._radar_done)

    def _radar_done(self, summary: SyncSummary) -> None:
        message = f"市场雷达更新：资金流 {summary.fund_flows} 条，资讯新增 {summary.news} 条"
        if self.config.advanced_mode:
            message += f"，高级流已投递 {summary.stream_events} 条、待发送 {summary.stream_pending} 条"
        if summary.errors:
            message += f"，{len(summary.errors)} 项异常"
            messagebox.showwarning("市场雷达部分数据不可用", "\n".join(summary.errors[:10]), parent=self.root)
        self._operation_done(message)

    def test_market(self) -> None:
        if not self.save_settings(notify=False):
            return
        self._run_background("正在测试行情连接…", self._service().test_market_connection, lambda message: messagebox.showinfo("连接成功", message, parent=self.root))

    def test_news(self) -> None:
        if not self.save_settings(notify=False):
            return
        self._run_background("正在测试财联社连接…", self._service().test_news_connection, lambda message: messagebox.showinfo("连接成功", message, parent=self.root))

    def test_advanced(self) -> None:
        if not self.save_settings(notify=False):
            return
        self._run_background(
            "正在测试 Kafka、Flink 和 TDengine…",
            self._service().test_advanced_connection,
            lambda message: messagebox.showinfo("高级架构可用", message, parent=self.root),
        )

    def _market_done(self, summary: SyncSummary) -> None:
        message = f"{summary.provider} 同步完成：{summary.symbols} 只股票，{summary.bars} 根 K 线，{summary.alerts} 条新告警"
        if self.config.advanced_mode:
            message += f"，高级流已投递 {summary.stream_events} 条、待发送 {summary.stream_pending} 条"
        if summary.errors:
            message += f"，{len(summary.errors)} 项失败"
            messagebox.showwarning("部分同步失败", "\n".join(summary.errors[:10]), parent=self.root)
        self._operation_done(message)

    def _operation_done(self, message: str) -> None:
        self.status_var.set(message)
        self.refresh_all()

    def _run_background(self, status: str, operation: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        self.busy_count += 1
        self.progress.start(10)
        self.status_var.set(status)

        def worker() -> None:
            try:
                result = operation()
                self.events.put(("success", (callback, result)))
            except Exception as error:
                self.events.put(("error", error))
            finally:
                self.events.put(("finished", None))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "success":
                    callback, result = payload
                    callback(result)
                elif kind == "error":
                    self.status_var.set("操作失败")
                    messagebox.showerror("操作失败", str(payload), parent=self.root)
                elif kind == "finished":
                    self.busy_count = max(0, self.busy_count - 1)
                    if self.busy_count == 0:
                        self.progress.stop()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    def refresh_all(self) -> None:
        watchlist = self.repository.list_watchlist()
        self._fill_tree(self.watch_tree, [(item["symbol"], item["name"] or "", "是" if item["enabled"] else "否") for item in watchlist])
        self._fill_tree(
            self.bars_tree,
            [
                (
                    item["symbol"], item["trading_date"], f"{item['close']:.3f}", f"{item['volume']:.0f}",
                    "已收盘" if item["is_closed"] else "盘中/未闭合", item["source"],
                )
                for item in self.repository.list_latest_bars()
            ],
        )
        self._fill_tree(self.alerts_tree, [(item["bar_timestamp"], item["symbol"], item["rule_id"], item["message"]) for item in self.repository.list_alerts(limit=200)])
        news = self.repository.list_news(limit=200)
        self._fill_tree(self.news_tree, [(item["published_at"], f"[{item['sentiment']}] {item['title']}", ",".join(item["symbols"]), item["url"] or "") for item in news])
        self._refresh_radar(news)
        self.refresh_research()
        symbols = [item["symbol"] for item in watchlist if item["enabled"]]
        self.analysis_symbol_combo.configure(values=symbols)
        if symbols and self.analysis_symbol_var.get() not in symbols:
            self.analysis_symbol_var.set(symbols[0])
        if self.analysis_symbol_var.get():
            self.refresh_analysis()

    def refresh_research(self) -> None:
        try:
            signals = ResearchSignalEngine(self.repository).rank_watchlist(self.config.interval, limit=200)
        except Exception as error:
            self.research_summary_var.set(f"智能筛选生成失败：{error}")
            self.research_signals = {}
            self._fill_tree(self.research_tree, [])
            return
        self.research_signals = {item.symbol: item for item in signals}
        self._fill_tree(self.research_tree, [
            (
                item.symbol,
                item.name,
                f"{item.score:+.1f}",
                item.state,
                f"{item.confidence:.0%}",
                f"{item.trend_score:+.0f}",
                f"{item.flow_score:+.0f}",
                f"{item.news_score:+.0f}",
                item.risk_level,
            )
            for item in signals
        ])
        strong = sum(item.score >= 18 for item in signals)
        weak = sum(item.score <= -18 for item in signals)
        incomplete = sum(bool(item.uncertainties) for item in signals)
        self.research_summary_var.set(
            f"本次筛选 {len(signals)} 只：偏强观察 {strong}、偏弱观察 {weak}、中性 {len(signals) - strong - weak}；"
            f"其中 {incomplete} 只存在数据缺口或降级来源。请结合原始公告与持牌行情复核。"
        )
        if signals:
            first = self.research_tree.get_children()[0]
            self.research_tree.selection_set(first)
            self.research_tree.focus(first)
            self._show_research_detail(None)

    def _show_research_detail(self, _event: Any) -> None:
        selected = self.research_tree.selection()
        if not selected:
            return
        values = self.research_tree.item(selected[0], "values")
        if not values:
            return
        signal = self.research_signals.get(str(values[0]))
        if signal is None:
            return
        flow_ratio = "--" if signal.main_net_ratio is None else f"{signal.main_net_ratio:+.2%}"
        flow_delta = "--" if signal.flow_delta is None else self._flow_text(signal.flow_delta)
        sections = [
            f"{signal.symbol} {signal.name}｜{signal.state}",
            f"综合评分 {signal.score:+.1f}，置信度 {signal.confidence:.0%}",
            f"分项：趋势 {signal.trend_score:+.0f}｜资金 {signal.flow_score:+.0f}｜资讯 {signal.news_score:+.0f}｜风险 {signal.risk_penalty:+.0f}",
            f"资金净占比 {flow_ratio}｜较前次快照 {flow_delta}",
            "\n【客观证据】\n" + ("\n".join(f"• {item}" for item in signal.evidence) or "• 暂无完整证据"),
            "\n【不确定性】\n" + ("\n".join(f"• {item}" for item in signal.uncertainties) or "• 未发现显著数据缺口"),
            "\n【复核条件】\n" + ("\n".join(f"• {item}" for item in signal.review_triggers) or "• 下一数据周期复核"),
            f"\n{signal.disclaimer}",
        ]
        self.research_detail.configure(state="normal")
        self.research_detail.delete("1.0", END)
        self.research_detail.insert("1.0", "\n".join(sections))
        self.research_detail.configure(state="disabled")

    @staticmethod
    def _flow_text(value: float) -> str:
        if abs(value) >= 100_000_000:
            return f"{value / 100_000_000:+.2f}亿"
        if abs(value) >= 10_000:
            return f"{value / 10_000:+.0f}万"
        return f"{value:+.0f}"

    def _flow_row(self, item: dict) -> tuple[str, str, str, str, str]:
        ratio = "--" if item["main_net_ratio"] is None else f"{item['main_net_ratio']:+.1%}"
        change = "--" if item["change_pct"] is None else f"{item['change_pct']:+.1%}"
        label = f"{item['entity_name']} {item['entity_code']}" if item["entity_type"] == "stock" else item["entity_name"]
        source = "iFinD" if item["source"].startswith("ifind") else "公开源(降级)"
        return label, self._flow_text(item["main_net_inflow"]), ratio, change, source

    def _refresh_radar(self, news: list[dict]) -> None:
        inflow = self.repository.list_latest_fund_flows("stock", limit=15, order="desc")
        outflow = self.repository.list_latest_fund_flows("stock", limit=15, order="asc")
        sectors = self.repository.list_latest_fund_flows("sector", limit=15, order="desc")
        self._fill_tree(self.inflow_tree, [self._flow_row(item) for item in inflow if item["main_net_inflow"] > 0])
        self._fill_tree(self.outflow_tree, [self._flow_row(item) for item in outflow if item["main_net_inflow"] < 0])
        self._fill_tree(self.sector_tree, [self._flow_row(item) for item in sectors if item["main_net_inflow"] > 0])
        self._fill_tree(self.radar_news_tree, [
            (
                item["published_at"], item["sentiment"], f"{item['confidence']:.0%}",
                item["title"], ",".join(item["symbols"]), item["evidence"] or "无方向性证据", item["url"] or "",
            )
            for item in news
        ])
        recent_news = []
        now = datetime.now(timezone.utc)
        for item in news:
            try:
                if (now - datetime.fromisoformat(item["published_at"])).total_seconds() <= 86_400:
                    recent_news.append(item)
            except (TypeError, ValueError):
                continue
        positive = [item for item in recent_news if item["sentiment"] == "利好"]
        negative = [item for item in recent_news if item["sentiment"] == "利空"]
        leaders = "、".join(
            f"{item['entity_name'] or item['entity_code']} {self._flow_text(item['main_net_inflow'])}"
            for item in inflow[:3]
        ) or "暂无有效资金流"
        positive_titles = "；".join(item["title"] for item in positive[:2]) or "暂无明确利好证据"
        self.radar_summary_var.set(
            f"自动摘要｜主力净流入靠前：{leaders}。近24小时授权资讯：利好 {len(positive)} 条、"
            f"利空 {len(negative)} 条、中性 {len(recent_news) - len(positive) - len(negative)} 条。"
            f"最新利好：{positive_titles}。仅作信息整理，不构成投资建议。"
        )
        health = self.repository.list_source_health()
        self.radar_health_var.set(" | ".join(
            f"{item['source']} {self._freshness_label(item)}（{item['message']}）" for item in health
        ) or "尚未自动同步；点击“立即更新市场雷达”")

    @staticmethod
    def _freshness_label(item: dict) -> str:
        if item["state"] != "正常" or not item.get("last_success"):
            return item["state"]
        timestamp = datetime.fromisoformat(item.get("last_data") or item["last_success"])
        seconds = max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds()))
        if seconds <= 90:
            return f"正常·{seconds}秒前"
        if seconds <= 600:
            return f"延迟·{seconds // 60}分钟前"
        return f"停滞·{seconds // 60}分钟前"

    def refresh_analysis(self) -> None:
        symbol = self.analysis_symbol_var.get().strip()
        if not symbol:
            self.stock_chart.clear("请先在观察列表添加股票")
            return
        try:
            limit = int(self.analysis_period_var.get())
            bars = self.repository.load_closed_bars(symbol, self.config.interval, limit=limit)
            if bars.empty:
                self.stock_chart.clear(f"{symbol} 暂无已闭合行情，请先同步")
                self.analysis_heading_var.set(f"{symbol} · 暂无数据")
                self.analysis_metrics_var.set("同步行情后生成趋势、动量、波动与回撤指标。")
                self.analysis_risk_var.set("风险：数据不足")
                self._set_suggestion("当前没有足够数据，不能形成可靠判断。")
                self.analysis_evidence_var.set("")
                return
            latest_date = bars.iloc[-1]["trading_date"]
            previous_close = self.repository.get_reference_close(symbol, latest_date)
            insight = analyze_stock(bars, previous_close)
            self.stock_chart.render(bars)
            change = "--" if insight.change_pct is None else f"{insight.change_pct:+.2%}"
            volatility = "--" if insight.annualized_volatility is None else f"{insight.annualized_volatility:.1%}"
            drawdown = "--" if insight.max_drawdown_60 is None else f"{insight.max_drawdown_60:.1%}"
            rsi = "--" if insight.rsi14 is None else f"{insight.rsi14:.1f}"
            volume_ratio = "--" if insight.volume_ratio is None else f"{insight.volume_ratio:.2f}x"
            self.analysis_heading_var.set(f"{symbol} · {insight.status}")
            self.analysis_metrics_var.set(
                f"{insight.latest_date}  收盘 {insight.latest_price:.2f}  日涨跌 {change}\n"
                f"口径 {insight.basis}  RSI14 {rsi}  量比 {volume_ratio}\n"
                f"年化波动 {volatility}  近60日最大回撤 {drawdown}"
            )
            self.analysis_risk_var.set(f"风险等级：{insight.risk_level}")
            self._set_suggestion(f"{insight.suggestion}\n\n失效/复核条件：{insight.invalidation}")
            self.analysis_evidence_var.set("\n".join(f"• {item}" for item in insight.evidence[:6]))
        except Exception as error:
            self.stock_chart.clear("研判生成失败")
            self.analysis_heading_var.set(f"{symbol} · 无法研判")
            self.analysis_metrics_var.set(str(error))
            self.analysis_risk_var.set("风险：未知")
            self._set_suggestion("请检查行情数据完整性和数据源配置。")
            self.analysis_evidence_var.set("")

    def _set_suggestion(self, value: str) -> None:
        self.analysis_suggestion.configure(state="normal")
        self.analysis_suggestion.delete("1.0", END)
        self.analysis_suggestion.insert("1.0", value)
        self.analysis_suggestion.configure(state="disabled")

    def _open_selected_analysis(self, _event: Any) -> None:
        selected = self.bars_tree.selection()
        if not selected:
            return
        symbol = str(self.bars_tree.item(selected[0], "values")[0])
        self.analysis_symbol_var.set(symbol)
        self.notebook.select(self.analysis_tab)
        self.refresh_analysis()

    @staticmethod
    def _fill_tree(tree: ttk.Treeview, rows: list[tuple[Any, ...]]) -> None:
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert("", END, values=row)

    def _open_selected_news(self, _event: Any) -> None:
        selected = self.news_tree.selection()
        if not selected:
            return
        values = self.news_tree.item(selected[0], "values")
        if len(values) >= 4 and values[3]:
            webbrowser.open(str(values[3]))

    def _open_selected_radar_news(self, _event: Any) -> None:
        selected = self.radar_news_tree.selection()
        if not selected:
            return
        values = self.radar_news_tree.item(selected[0], "values")
        if len(values) >= 7 and values[6]:
            webbrowser.open(str(values[6]))


def main() -> None:
    root = Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
