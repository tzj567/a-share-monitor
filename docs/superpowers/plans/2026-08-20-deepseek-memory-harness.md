# DeepSeek Memory Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, testable DeepSeek harness, reviewed Codex durable-memory importer, and installable Codex skill without exposing private Codex data or changing the existing market-monitor behavior.

**Architecture:** Add an isolated AI adapter layer under `src/stock_monitor/`. The memory module reads only reviewed ECC-style Markdown plus explicitly selected Codex candidates, while the DeepSeek client receives only bounded records supplied by the caller. CLI and skill layers share those interfaces; the existing market-data and research modules remain untouched.

**Tech Stack:** Python 3.11+, `requests`, `pydantic` already present in the project, `argparse`, SQLite read-only URI mode, Markdown frontmatter, PowerShell skill installer, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-20-deepseek-memory-harness-design.md`

## Global Constraints

- Never read or publish `auth.json`, `cap_sid`, cookies, sessions, logs, state/queue databases, WAL files, `.env` files, private keys, or credential-store exports.
- Project memory is local-only under `.ecc/memory/project/` and must be protected by a fail-closed `.gitignore`.
- `DEEPSEEK_API_KEY` is the only supported API-key source; never write it to JSON, memory, logs, or Git.
- Import defaults to dry-run; writing requires explicit `--apply` and writes only valid redacted `ecc.memory.v1` records.
- The DeepSeek client sends only caller-provided bounded context and never scans local memory itself.
- Retry only HTTP 429 and 5xx with a bounded count; never silently fall back from a failed DeepSeek request.
- Unit tests and CI must not make live DeepSeek requests.
- Preserve the existing CLI commands and all existing tests.
- Do not create a license file or push to GitHub until the user confirms repository identity and license choice.

---

### Task 1: Build the safe ECC memory model, parser, redactor, and retriever

**Files:**
- Create: `src/stock_monitor/memory_export.py`
- Create: `tests/test_memory_export.py`
- Create: `.ecc/memory/project/.gitignore`
- Modify: `.gitignore`

**Interfaces:**
- Produces immutable `MemoryRecord`, `ImportReport`, `MemoryStore`, `parse_memory_markdown()`, `redact_text()`, and `export_source()` used by Tasks 2 and 3.
- `MemoryRecord` fields: `id`, `title`, `kind`, `body`, `source_harness`, `target_harness`, `tags`, `created_at`, and `sha256`.
- `MemoryRecord` is a frozen dataclass; tests construct it directly with keyword arguments and never depend on an undefined factory method.
- `MemoryStore(root: Path).search(query, limit=8, max_chars=12000)` returns deterministic results sorted by token-overlap score, then title, then ID, without exceeding `max_chars`.
- `export_source(source: Path | None, source_db: Path | None, output_root: Path, apply: bool)` accepts exactly one source and returns an `ImportReport` without touching the network.

- [ ] **Step 1: Write failing parser, redaction, retrieval, and safety tests**

```python
import pytest


def test_parse_and_search_ecc_memory(tmp_path):
    root = tmp_path / ".ecc" / "memory" / "project"
    root.mkdir(parents=True)
    (root / "decision.md").write_text(
        "---\n"
        "format: ecc.memory.v1\n"
        "id: mem-1\n"
        "title: Data source decision\n"
        "kind: context\n"
        "source_harness: codex\n"
        "target_harness: all\n"
        "tags: [data, architecture]\n"
        "created_at: 2026-08-20T00:00:00Z\n"
        "---\n"
        "Use the licensed provider before the public fallback.\n",
        encoding="utf-8",
    )
    store = MemoryStore(root)
    result = store.search("licensed provider", limit=1)
    assert result[0].id == "mem-1"
    assert result[0].sha256


def test_redaction_removes_secrets_without_logging_body():
    redacted, count = redact_text(
        "token=sk-live-example password=hunter2 Bearer abc.def.ghi"
    )
    assert count == 3
    assert "sk-live-example" not in redacted
    assert "hunter2" not in redacted
    assert "abc.def.ghi" not in redacted


def test_export_is_dry_run_by_default_and_blocks_sensitive_paths(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("---\ntitle: Safe\n---\nKeep this local.\n", encoding="utf-8")
    output = tmp_path / ".ecc" / "memory" / "project"
    report = export_source(source, None, output, apply=False)
    assert report.written == 0
    assert not output.exists()

    blocked = tmp_path / "auth.json"
    blocked.write_text("{}", encoding="utf-8")
    with pytest.raises(MemoryExportError, match="blocked_source"):
        export_source(blocked, None, output, apply=False)
```

- [ ] **Step 2: Run the focused tests and verify they fail for missing symbols**

Run: `python -m pytest tests/test_memory_export.py -q`

Expected: FAIL because `MemoryStore`, `redact_text`, `export_source`, and `MemoryExportError` do not yet exist.

- [ ] **Step 3: Implement the minimal memory module**

Implement these concrete rules:

```python
BLOCKED_NAMES = {
    "auth.json", "cap_sid", "cookies.json", "session_index.jsonl",
}
BLOCKED_GLOBS = ("logs_*.sqlite", "state_*.sqlite", "queue_*.sqlite")
BLOCKED_SUFFIXES = (".db-wal", ".db-shm")
SECRET_PATTERNS = (
    ("api_key", re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)")),
    ("token", re.compile(r"(?i)(token\s*[=:]\s*)([^\s,;]+)")),
    ("bearer", re.compile(r"(?i)(bearer\s+)([^\s]+)")),
    ("password", re.compile(r"(?i)(password\s*[=:]\s*)([^\s,;]+)")),
    ("private_key", re.compile(r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----", re.S)),
)
```

Parse only the known frontmatter keys, require `format: ecc.memory.v1` for store records, normalize tags, and calculate `sha256` from the UTF-8 body. Open Codex SQLite sources with `sqlite3.connect("file:{path}?mode=ro", uri=True)` and select only non-empty `raw_memory` rows with `selected_for_phase2 = 1`; never update or vacuum the database. Reject sources whose final resolved path is a blocked file or escapes the explicitly supplied parent.

Write one deterministic filename per record, such as `<sha256[:16]>.md`, with frontmatter containing `format`, `id`, `title`, `kind`, `source_harness`, `target_harness`, `tags`, `created_at`, and `trust: unreviewed`. Create the project-root `.gitignore` with `*` and `!.gitignore` before any apply write.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `python -m pytest tests/test_memory_export.py -q`

Expected: PASS with coverage for parsing, deterministic search, redaction, dry-run, blocked paths, and read-only source handling.

- [ ] **Step 5: Commit the memory subsystem**

```powershell
git add -- src/stock_monitor/memory_export.py tests/test_memory_export.py .ecc/memory/project/.gitignore .gitignore
git commit -m "feat: add safe ECC memory importer"
```

### Task 2: Implement the DeepSeek API client with sanitized failures

**Files:**
- Create: `src/stock_monitor/deepseek_harness.py`
- Create: `tests/test_deepseek_harness.py`

**Interfaces:**
- Consumes `MemoryRecord` from `stock_monitor.memory_export`.
- Produces `DeepSeekConfig.from_env()`, `DeepSeekResult`, `DeepSeekError`, and `DeepSeekClient.complete()` for Task 3 and the skill documentation.
- `DeepSeekClient(config, session=None, sleep=time.sleep)` accepts an injected `requests.Session` and sleep function so tests never call the network or wait.
- `DeepSeekConfig` fields are `api_key`, `base_url`, `model`, `timeout_seconds`, and `max_retries`; `from_env()` validates URL, model, timeout, and non-negative retry count.
- `DeepSeekResult` contains only `content`, `model`, `request_id`, and optional token counts; it never stores the API key or raw response.

- [ ] **Step 1: Write failing transport tests with a fake session**

```python
class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return next(self.responses)


def test_complete_builds_bounded_deepseek_request(monkeypatch):
    fake = FakeSession([FakeResponse(200, {
        "id": "req-1",
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
    })])
    client = DeepSeekClient(
        DeepSeekConfig(api_key="secret", max_retries=0),
        session=fake,
        sleep=lambda _: None,
    )
    result = client.complete(
        "What changed?",
        context=[MemoryRecord(
            id="mem-1",
            title="Data source decision",
            kind="context",
            body="Use licensed data.",
            source_harness="codex",
            target_harness="all",
            tags=["data"],
            created_at="2026-08-20T00:00:00Z",
            sha256="fixture",
        )],
    )
    assert result.content == "answer"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert fake.calls[0]["json"]["model"] == "deepseek-v4-pro"
    assert "Use licensed data." in fake.calls[0]["json"]["messages"][-1]["content"]


def test_transient_failures_retry_but_auth_failure_does_not_leak_body():
    fake = FakeSession([
        FakeResponse(500, {"error": {"message": "token=secret"}}),
        FakeResponse(200, {"id": "req-2", "model": "deepseek-v4-pro",
                           "choices": [{"message": {"content": "ok"}}]}),
    ])
    client = DeepSeekClient(DeepSeekConfig(api_key="secret", max_retries=1), fake, lambda _: None)
    assert client.complete("question").content == "ok"
    assert len(fake.calls) == 2

    denied = DeepSeekClient(
        DeepSeekConfig(api_key="secret", max_retries=2),
        FakeSession([FakeResponse(401, {"error": {"message": "secret"}})]),
        lambda _: None,
    )
    with pytest.raises(DeepSeekError) as error:
        denied.complete("private question")
    assert "secret" not in str(error.value)
    assert "private question" not in str(error.value)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest tests/test_deepseek_harness.py -q`

Expected: FAIL because the client types and transport implementation are absent.

- [ ] **Step 3: Implement the client**

Use the official endpoint shape:

```python
payload = {
    "model": model,
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(question, context)},
    ],
    "thinking": {"type": "enabled"},
    "reasoning_effort": reasoning_effort,
    "stream": False,
}
```

Load `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, `DEEPSEEK_TIMEOUT_SECONDS`, and `DEEPSEEK_MAX_RETRIES` through a validated config. Use a session-injected `post()` method with a timeout. Retry at most `max_retries` on 429 or 500-599, sleeping `min(0.5 * 2**attempt, 2.0)` seconds. Map 401/403 to `provider_auth`, 429 to `provider_rate_limit`, 5xx/timeouts to `provider_unavailable`, invalid JSON or missing assistant content to `provider_response_invalid`, and missing key to `missing_api_key`. Never include response text in exception strings.

- [ ] **Step 4: Run all client tests**

Run: `python -m pytest tests/test_deepseek_harness.py -q`

Expected: PASS with zero live network calls.

- [ ] **Step 5: Commit the client subsystem**

```powershell
git add -- src/stock_monitor/deepseek_harness.py tests/test_deepseek_harness.py
git commit -m "feat: add DeepSeek API harness"
```

### Task 3: Add CLI commands without changing existing commands

**Files:**
- Modify: `src/stock_monitor/cli.py`
- Create: `tests/test_cli_deepseek.py`

**Interfaces:**
- Consumes `export_source`, `MemoryStore`, `DeepSeekClient`, and their error categories.
- Produces `memory-export` and `deepseek` subcommands while preserving `serve`, `replay`, and `desktop` parser behavior.
- `_memory_export(args)` and `_deepseek(args)` return integer exit codes; `main()` returns the selected handler code and the module entry point raises `SystemExit(main())`.

- [ ] **Step 1: Write failing parser and handler tests**

```python
import argparse


def test_parser_keeps_existing_commands_and_adds_memory_commands():
    parser = build_parser()
    assert parser.parse_args(["memory-export", "--source", "notes.md"]).command == "memory-export"
    assert parser.parse_args(["deepseek", "--question", "hello"]).command == "deepseek"
    assert parser.parse_args(["serve"]).command == "serve"


def test_deepseek_without_include_memory_does_not_search(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = FakeClient("answer")
    monkeypatch.setattr("stock_monitor.cli.DeepSeekClient", lambda *args, **kwargs: client)
    _deepseek(argparse.Namespace(question="hello", include_memory=False, model=None))
    assert client.contexts == [[]]
    assert capsys.readouterr().out.strip() == "answer"
```

- [ ] **Step 2: Run the CLI tests and verify they fail**

Run: `python -m pytest tests/test_cli_deepseek.py -q`

Expected: FAIL because the new parser entries and handlers are absent.

- [ ] **Step 3: Implement handlers and parser entries**

Add `_memory_export(args)` with mutually exclusive `--source`/`--source-db`, `--output-root`, and `--apply`; print a JSON report containing counts and hashes only. Add `_deepseek(args)` with required `--question`, optional `--include-memory`, `--memory-root`, `--model`, and `--reasoning-effort`; call `MemoryStore.search()` only when requested, then print only `DeepSeekResult.content`. Catch domain errors, print the stable category and sanitized message to stderr, and return a non-zero status through `main()` without changing existing handler behavior.

- [ ] **Step 4: Run CLI tests and existing CLI regression tests**

Run: `python -m pytest tests/test_cli_deepseek.py tests/test_config.py tests/test_providers.py -q`

Expected: PASS; `serve`, `replay`, and `desktop` parser choices remain available.

- [ ] **Step 5: Commit CLI integration**

```powershell
git add -- src/stock_monitor/cli.py tests/test_cli_deepseek.py
git commit -m "feat: expose memory export and DeepSeek commands"
```

### Task 4: Add the Codex skill, installer, public-release guard, and docs

**Files:**
- Create: `skills/deepseek-memory-bridge/SKILL.md`
- Create: `skills/deepseek-memory-bridge/agents/openai.yaml`
- Create: `scripts/install_deepseek_memory_skill.ps1`
- Create: `scripts/check_public_release.py`
- Create: `tests/test_public_release.py`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Skill routes Codex requests to the existing DeepSeek API/MCP and describes the repository CLI fallback.
- Installer copies only `SKILL.md`, `agents/`, and skill resources to `%USERPROFILE%\.codex\skills\deepseek-memory-bridge`; it never copies `.ecc`, `.env`, databases, or credentials.
- Public-release checker exposes `check_paths(root: Path, relative_paths: Iterable[str]) -> list[str]` and `check_tracked_files(root: Path) -> list[str]`; the CLI exits non-zero when either finds a blocked path or high-confidence secret.

- [ ] **Step 1: Write failing public-release and skill metadata tests**

```python
from pathlib import Path


def test_public_release_checker_rejects_private_paths(tmp_path):
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    assert any("auth.json" in item for item in check_paths(tmp_path, ["auth.json", "README.md"]))


def test_skill_metadata_has_required_frontmatter():
    skill = Path("skills/deepseek-memory-bridge/SKILL.md").read_text(encoding="utf-8")
    assert "name: deepseek-memory-bridge" in skill
    assert "DEEPSEEK_API_KEY" in skill
    assert "dual-model-qa/ask_model" in skill
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python -m pytest tests/test_public_release.py -q`

Expected: FAIL because the skill, scanner, and CI files do not yet exist.

- [ ] **Step 3: Implement the skill and release guard**

The skill must state the exact boundaries: search project/team memory first, do not import raw sessions or user scope by default, pass bounded context, use `provider: deepseek`, preserve the selected model, and report provider errors without fallback. The installer must validate that the source skill directory is inside the checkout before copying.

The release checker must inspect `git ls-files` when run in a repository, reject the blocked basenames and suffixes from Task 1, reject `.ecc/memory/project/`, and scan text files for `DEEPSEEK_API_KEY=`, `Authorization: Bearer`, private-key markers, and common password/token assignments. It must not print matched secret text.

Add a CI workflow that installs `.[dev]`, runs `python -m pytest`, then runs `python scripts/check_public_release.py`. Add README instructions for installing the editable package, setting `DEEPSEEK_API_KEY` in the process environment, running dry-run memory export, applying reviewed memory locally, installing the skill, and understanding that no real DeepSeek call occurs in CI.

- [ ] **Step 4: Run skill validation and public-release checks**

Run:

```powershell
python -m pytest tests/test_public_release.py -q
python scripts/check_public_release.py
git diff --check
```

Expected: all commands exit 0 and no local memory/database/auth path is tracked.

- [ ] **Step 5: Commit skill and release configuration**

```powershell
git add -- skills/deepseek-memory-bridge scripts/install_deepseek_memory_skill.ps1 scripts/check_public_release.py tests/test_public_release.py .github/workflows/ci.yml README.md .gitignore
git commit -m "docs: add DeepSeek memory bridge skill and release checks"
```

### Task 5: Full verification and release readiness audit

**Files:**
- Modify only files required by verification findings; do not broaden scope.
- Review: all tracked files and `git diff HEAD~N..HEAD` for the feature commits.

**Interfaces:**
- Verifies every interface and global constraint in the spec.
- Produces a clean test report, public-release scan, and a release checklist. No GitHub write is performed in this task.

- [ ] **Step 1: Run the complete local test suite**

Run: `python -m pytest`

Expected: exit code 0 with all existing and new tests passing.

- [ ] **Step 2: Run static and release checks**

Run:

```powershell
python scripts/check_public_release.py
git diff --check
git status --short
```

Expected: the public-release checker reports no blocked tracked files or high-confidence secrets, `git diff --check` is clean, and any remaining untracked files are explicitly reviewed.

- [ ] **Step 3: Run local CLI smoke checks without a key**

Run:

```powershell
python -m stock_monitor --help
python -m stock_monitor memory-export --help
python -m stock_monitor deepseek --help
```

Expected: all help commands exit 0 and list the new flags; no network request is made.

- [ ] **Step 4: Run the importer against the known-empty Codex memory DB in dry-run mode**

Run:

```powershell
python -m stock_monitor memory-export --source-db "C:\Users\20184\.codex\memories_1.sqlite" --dry-run
```

Expected: a zero-candidate report, no output directory creation, and no mutation of the source database.

- [ ] **Step 5: Review the final diff and prepare, but do not execute, GitHub publication**

Confirm the exact staged scope, repository owner/name, visibility, and license before any push. Do not create a GitHub remote, push, or publish until those values and the user’s explicit publication authorization are present.


