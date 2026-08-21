"""FastAPI surface and a dependency-free dashboard."""

from __future__ import annotations

import html
import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

from .engine import MonitorEngine
from .models import Bar, EvaluationResult, ReferenceClose, ResearchSignal, RuleConfig, WatchItem
from .research import ResearchSignalEngine
from .storage import SQLiteRepository


def create_app(db_path: str | Path | None = None) -> FastAPI:
    database = str(db_path or os.getenv("STOCK_MONITOR_DB", "stock_monitor.db"))
    repository = SQLiteRepository(database)
    engine = MonitorEngine(repository)
    research_engine = ResearchSignalEngine(repository)
    app = FastAPI(
        title="A 股量化监控",
        version="0.2.0",
        description="只读股票研究监控；不包含自动下单。",
    )
    app.state.repository = repository
    app.state.engine = engine
    app.state.research_engine = research_engine

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "read-only-monitor", "database": database}

    @app.post("/watchlist", status_code=201)
    def upsert_watch_item(item: WatchItem) -> WatchItem:
        repository.save_watch_item(item)
        return item

    @app.get("/watchlist")
    def watchlist() -> list[dict]:
        return repository.list_watchlist()

    @app.post("/bars", status_code=201)
    def ingest_bar(bar: Bar) -> dict[str, str | bool]:
        repository.save_bar(bar)
        return {
            "status": "stored",
            "symbol": bar.symbol,
            "timestamp": bar.timestamp.isoformat(),
            "is_closed": bar.is_closed,
        }

    @app.post("/reference-closes", status_code=201)
    def upsert_reference_close(value: ReferenceClose) -> ReferenceClose:
        repository.save_reference_close(value)
        return value

    @app.post("/evaluate/{symbol}", response_model=EvaluationResult)
    def evaluate_symbol(symbol: str, interval: str = Query(default="1m", min_length=1, max_length=16), config: RuleConfig | None = None) -> EvaluationResult:
        return engine.evaluate_symbol(symbol, interval, config)

    @app.post("/evaluate-all", response_model=list[EvaluationResult])
    def evaluate_all(interval: str = Query(default="1m", min_length=1, max_length=16), config: RuleConfig | None = None) -> list[EvaluationResult]:
        items = repository.list_watchlist()
        return [engine.evaluate_symbol(item["symbol"], interval, config) for item in items if item["enabled"]]

    @app.get("/alerts")
    def list_alerts(symbol: str | None = None, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
        return repository.list_alerts(symbol, limit)

    @app.get("/news")
    def list_news(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
        return repository.list_news(limit)

    @app.get("/fund-flows")
    def list_fund_flows(
        entity_type: str = Query(default="stock", pattern="^(stock|sector)$"),
        order: str = Query(default="desc", pattern="^(asc|desc)$"),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict]:
        return repository.list_latest_fund_flows(entity_type, limit, order)

    @app.get("/source-health")
    def source_health() -> list[dict]:
        return repository.list_source_health()

    @app.get("/research-signals", response_model=list[ResearchSignal])
    def research_signals(
        interval: str = Query(default="1d", min_length=1, max_length=16),
        limit: int = Query(default=100, ge=1, le=500),
        news_window_hours: int = Query(default=72, ge=1, le=720),
    ) -> list[ResearchSignal]:
        return research_engine.rank_watchlist(
            interval,
            limit=limit,
            news_window_hours=news_window_hours,
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> str:
        watch = repository.list_watchlist()
        alerts = repository.list_alerts(limit=100)
        watch_rows = "".join(
            f"<tr><td>{html.escape(item['symbol'])}</td><td>{html.escape(item['name'] or '')}</td><td>{'启用' if item['enabled'] else '停用'}</td></tr>"
            for item in watch
        ) or '<tr><td colspan="3">观察列表为空</td></tr>'
        alert_rows = "".join(
            f"<tr><td>{html.escape(item['bar_timestamp'])}</td><td>{html.escape(item['symbol'])}</td><td>{html.escape(item['rule_id'])}</td><td>{html.escape(item['message'])}</td></tr>"
            for item in alerts
        ) or '<tr><td colspan="4">暂无告警</td></tr>'
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A 股量化监控</title><style>
body{{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0b1220;color:#e5e7eb;margin:0;padding:28px}}
main{{max-width:1180px;margin:auto}}h1{{margin:0 0 8px}}.note{{color:#94a3b8;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:1fr 2fr;gap:20px}}section{{background:#111a2d;border:1px solid #243047;border-radius:14px;padding:18px;overflow:auto}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:10px;border-bottom:1px solid #243047;text-align:left;white-space:nowrap}}th{{color:#7dd3fc}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><h1>A 股量化监控</h1><div class="note">只读研究模式 · 不执行交易 · 数据与信号需自行核验</div>
<div class="grid"><section><h2>观察列表</h2><table><thead><tr><th>代码</th><th>名称</th><th>状态</th></tr></thead><tbody>{watch_rows}</tbody></table></section>
<section><h2>最近告警</h2><table><thead><tr><th>时间（UTC）</th><th>代码</th><th>规则</th><th>内容</th></tr></thead><tbody>{alert_rows}</tbody></table></section></div>
</main></body></html>"""

    return app


app = create_app()
