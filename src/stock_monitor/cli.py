"""CLI for serving the API and replaying deterministic CSV data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import uvicorn

from .deepseek_harness import DeepSeekClient, DeepSeekConfig, DeepSeekError
from .engine import MonitorEngine
from .memory_export import MemoryExportError, MemoryStore, export_source, redact_text
from .models import RuleConfig
from .providers import CSVReplayProvider
from .storage import SQLiteRepository


def _serve(args: argparse.Namespace) -> int:
    os.environ["STOCK_MONITOR_DB"] = args.database
    uvicorn.run("stock_monitor.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def _replay(args: argparse.Namespace) -> int:
    repository = SQLiteRepository(args.database)
    provider = CSVReplayProvider(args.bars, args.reference_closes)
    for value in provider.reference_closes(args.symbol):
        repository.save_reference_close(value)
    accepted = 0
    engine = MonitorEngine(repository)
    for bar in provider.bars(args.symbol, args.interval):
        repository.save_bar(bar)
        if bar.is_closed:
            accepted += len(engine.evaluate_symbol(args.symbol, args.interval, RuleConfig()).alerts)
    print(f"回放完成：写入 K 线，新增告警 {accepted} 条")
    return 0


def _desktop(_args: argparse.Namespace) -> int:
    from .desktop import main as desktop_main

    desktop_main()
    return 0


def _memory_export(args: argparse.Namespace) -> int:
    report = export_source(args.source, args.source_db, args.output_root, apply=args.apply)
    payload = {
        "candidates": report.candidates,
        "written": report.written,
        "redacted": report.redacted,
        "hashes": [Path(file_path).stem for file_path in report.files],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _deepseek(args: argparse.Namespace) -> int:
    context = []
    if args.include_memory:
        context = MemoryStore(args.memory_root).search(args.question)
    client = DeepSeekClient(DeepSeekConfig.from_env())
    result = client.complete(
        args.question,
        context=context,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    print(result.content)
    return 0


def _error_category(error: Exception) -> str:
    category = getattr(error, "category", None)
    if isinstance(category, str) and category:
        return category
    if isinstance(error, MemoryExportError):
        return "memory_export_error"
    if isinstance(error, ValueError):
        return "invalid_argument"
    return "cli_error"


def _sanitize_error_text(message: str) -> str:
    sanitized, _ = redact_text(message)
    return sanitized


def _format_cli_error(error: Exception) -> str:
    category = _error_category(error)
    if isinstance(error, DeepSeekError):
        return category
    message = _sanitize_error_text(str(error))
    return f"{category}: {message}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A 股只读量化监控")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="启动 API 和仪表盘")
    serve.add_argument("--database", default="stock_monitor.db")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    replay = subparsers.add_parser("replay", help="逐根回放 CSV K 线")
    replay.add_argument("--database", default="stock_monitor.db")
    replay.add_argument("--bars", required=True)
    replay.add_argument("--reference-closes")
    replay.add_argument("--symbol", required=True)
    replay.add_argument("--interval", default="1m")
    replay.set_defaults(func=_replay)

    desktop = subparsers.add_parser("desktop", help="启动 Windows 桌面软件")
    desktop.set_defaults(func=_desktop)

    memory_export = subparsers.add_parser("memory-export", help="导出记忆数据")
    memory_input = memory_export.add_mutually_exclusive_group(required=True)
    memory_input.add_argument("--source", type=Path)
    memory_input.add_argument("--source-db", type=Path)
    memory_export.add_argument("--output-root", type=Path, default=Path(".ecc") / "memory" / "project")
    memory_mode = memory_export.add_mutually_exclusive_group()
    memory_mode.add_argument("--dry-run", action="store_true")
    memory_mode.add_argument("--apply", action="store_true")
    memory_export.set_defaults(func=_memory_export)

    deepseek = subparsers.add_parser("deepseek", help="运行 DeepSeek 研究问答")
    deepseek.add_argument("--question", required=True)
    deepseek.add_argument("--include-memory", action="store_true")
    deepseek.add_argument("--memory-root", type=Path, default=Path(".ecc") / "memory" / "project")
    deepseek.add_argument("--model")
    deepseek.add_argument("--reasoning-effort", default="high")
    deepseek.set_defaults(func=_deepseek)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command not in {"memory-export", "deepseek"}:
        return int(args.func(args) or 0)
    try:
        return int(args.func(args) or 0)
    except (DeepSeekError, MemoryExportError, ValueError) as error:
        print(_format_cli_error(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
