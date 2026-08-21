import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from stock_monitor.deepseek_harness import DeepSeekError, DeepSeekResult
from stock_monitor.memory_export import ImportReport, MemoryRecord


def test_build_parser_keeps_existing_commands_and_adds_deepseek_commands():
    from stock_monitor.cli import build_parser

    parser = build_parser()

    assert parser.parse_args(["serve"]).command == "serve"
    assert parser.parse_args(["replay", "--bars", "bars.csv", "--symbol", "000001.SZ"]).command == "replay"
    assert parser.parse_args(["desktop"]).command == "desktop"
    assert parser.parse_args(["memory-export", "--source", "notes.md"]).command == "memory-export"
    assert parser.parse_args(["memory-export", "--source-db", "memory.sqlite"]).command == "memory-export"
    deepseek = parser.parse_args(["deepseek", "--question", "What changed?"])
    assert deepseek.command == "deepseek"
    assert deepseek.include_memory is False


def test_memory_export_accepts_explicit_dry_run_flag():
    from stock_monitor.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["memory-export", "--source-db", "memory.sqlite", "--dry-run"])

    assert args.command == "memory-export"
    assert args.source_db == Path("memory.sqlite")
    assert args.apply is False


def test_module_entrypoint_prefers_local_checkout_over_external_pythonpath(tmp_path):
    fake_package = tmp_path / "stock_monitor"
    fake_package.mkdir()
    (fake_package / "__main__.py").write_text(
        "raise SystemExit('external package used')\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(tmp_path)
        if not existing_pythonpath
        else os.pathsep.join([str(tmp_path), existing_pythonpath])
    )
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "-m", "stock_monitor", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "memory-export" in result.stdout
    assert "deepseek" in result.stdout
    assert "external package used" not in result.stderr


def test_memory_export_is_dry_run_by_default_and_prints_hashes_only(monkeypatch, capsys, tmp_path):
    from stock_monitor import cli

    source = tmp_path / "notes.md"
    output_root = tmp_path / ".ecc" / "memory" / "project"
    written_file = output_root / "0123456789abcdef.md"
    seen = {}

    def fake_export_source(source_arg, source_db_arg, output_arg, apply, *, source_root=None):
        seen["call"] = {
            "source": source_arg,
            "source_db": source_db_arg,
            "output_root": output_arg,
            "apply": apply,
            "source_root": source_root,
        }
        return ImportReport(candidates=2, written=1, redacted=3, files=(str(written_file),))

    monkeypatch.setattr(cli, "export_source", fake_export_source)

    exit_code = cli._memory_export(
        cli.build_parser().parse_args(
            ["memory-export", "--source", str(source), "--output-root", str(output_root)]
        )
    )

    assert exit_code == 0
    assert seen["call"] == {
        "source": source,
        "source_db": None,
        "output_root": output_root,
        "apply": False,
        "source_root": None,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "candidates": 2,
        "written": 1,
        "redacted": 3,
        "hashes": ["0123456789abcdef"],
    }
    assert str(output_root) not in json.dumps(payload)


def test_deepseek_without_include_memory_skips_store_lookup(monkeypatch, capsys):
    from stock_monitor import cli

    class FakeClient:
        def __init__(self, config):
            self.config = config
            self.calls = []

        def complete(self, question, *, context=(), model=None, reasoning_effort="high"):
            self.calls.append(
                {
                    "question": question,
                    "context": list(context),
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                }
            )
            return DeepSeekResult(content="answer", model="deepseek-v4-pro", request_id="req-1")

    fake_client = FakeClient(None)
    monkeypatch.setattr(cli, "DeepSeekClient", lambda config: fake_client)
    monkeypatch.setattr(cli.DeepSeekConfig, "from_env", classmethod(lambda cls: object()))

    def fail_store(_root):
        raise AssertionError("MemoryStore.search should not run without --include-memory")

    monkeypatch.setattr(cli, "MemoryStore", fail_store)

    exit_code = cli._deepseek(
        cli.build_parser().parse_args(["deepseek", "--question", "What changed?"])
    )

    assert exit_code == 0
    assert fake_client.calls == [
        {
            "question": "What changed?",
            "context": [],
            "model": None,
            "reasoning_effort": "high",
        }
    ]
    assert capsys.readouterr().out.strip() == "answer"


def test_deepseek_with_include_memory_searches_requested_root(monkeypatch, capsys, tmp_path):
    from stock_monitor import cli

    memory_root = tmp_path / ".ecc" / "memory" / "project"
    record = MemoryRecord(
        id="mem-1",
        title="Context",
        kind="context",
        body="Use the licensed provider.\n",
        source_harness="codex",
        target_harness="all",
        tags=("data",),
        created_at="2026-08-20T00:00:00Z",
        sha256="digest",
    )
    seen = {}

    class FakeStore:
        def __init__(self, root):
            seen["root"] = root

        def search(self, query, limit=8, max_chars=12000):
            seen["search"] = {"query": query, "limit": limit, "max_chars": max_chars}
            return [record]

    class FakeClient:
        def complete(self, question, *, context=(), model=None, reasoning_effort="high"):
            seen["complete"] = {
                "question": question,
                "context": list(context),
                "model": model,
                "reasoning_effort": reasoning_effort,
            }
            return DeepSeekResult(content="grounded answer", model="deepseek-v4-flash")

    monkeypatch.setattr(cli, "MemoryStore", FakeStore)
    monkeypatch.setattr(cli, "DeepSeekClient", lambda config: FakeClient())
    monkeypatch.setattr(cli.DeepSeekConfig, "from_env", classmethod(lambda cls: object()))

    exit_code = cli._deepseek(
        cli.build_parser().parse_args(
            [
                "deepseek",
                "--question",
                "What changed?",
                "--include-memory",
                "--memory-root",
                str(memory_root),
                "--model",
                "deepseek-v4-flash",
                "--reasoning-effort",
                "low",
            ]
        )
    )

    assert exit_code == 0
    assert seen["root"] == memory_root
    assert seen["search"] == {"query": "What changed?", "limit": 8, "max_chars": 12000}
    assert seen["complete"] == {
        "question": "What changed?",
        "context": [record],
        "model": "deepseek-v4-flash",
        "reasoning_effort": "low",
    }
    assert capsys.readouterr().out.strip() == "grounded answer"


def test_main_returns_non_zero_with_sanitized_error(monkeypatch, capsys):
    from stock_monitor import cli

    def raise_error(_args):
        raise DeepSeekError("provider_auth", "token=secret")

    monkeypatch.setattr(cli, "_deepseek", raise_error)

    exit_code = cli.main(["deepseek", "--question", "private question"])

    assert exit_code == 1
    stderr = capsys.readouterr().err.strip()
    assert "provider_auth" in stderr
    assert "secret" not in stderr
    assert "private question" not in stderr


def test_main_preserves_legacy_value_error_behavior(monkeypatch):
    from stock_monitor import cli

    def raise_error(_args):
        raise ValueError("legacy failure")

    monkeypatch.setattr(cli, "_serve", raise_error)

    with pytest.raises(ValueError, match="legacy failure"):
        cli.main(["serve"])


def test_main_returns_non_zero_for_missing_deepseek_key(monkeypatch, capsys):
    from stock_monitor import cli

    def raise_missing_key(cls):
        raise DeepSeekError("missing_api_key", "token=secret")

    monkeypatch.setattr(cli.DeepSeekConfig, "from_env", classmethod(raise_missing_key))

    exit_code = cli.main(["deepseek", "--question", "private question"])

    assert exit_code == 1
    stderr = capsys.readouterr().err.strip()
    assert "missing_api_key" in stderr
    assert "secret" not in stderr
    assert "private question" not in stderr


def test_main_returns_non_zero_for_deepseek_provider_failure(monkeypatch, capsys):
    from stock_monitor import cli

    class FakeClient:
        def __init__(self, _config):
            pass

        def complete(self, question, *, context=(), model=None, reasoning_effort="high"):
            raise DeepSeekError("provider_unavailable", f"prompt={question} token=secret")

    monkeypatch.setattr(cli.DeepSeekConfig, "from_env", classmethod(lambda cls: object()))
    monkeypatch.setattr(cli, "DeepSeekClient", FakeClient)

    exit_code = cli.main(["deepseek", "--question", "private question"])

    assert exit_code == 1
    stderr = capsys.readouterr().err.strip()
    assert "provider_unavailable" in stderr
    assert "secret" not in stderr
    assert "private question" not in stderr


def test_memory_export_requires_exactly_one_input():
    from stock_monitor.cli import build_parser

    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["memory-export"])
    with pytest.raises(SystemExit):
        parser.parse_args(["memory-export", "--source", "notes.md", "--source-db", "memory.sqlite"])
