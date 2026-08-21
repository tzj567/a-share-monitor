# DeepSeek Memory Harness Design

## Goal

Add an open-source, auditable DeepSeek harness to the existing A-share monitoring repository, with a safe Codex durable-memory importer and a reusable Codex skill. The harness must be usable from the repository without exposing private Codex state, credentials, raw sessions, or unreviewed memory.

## Scope

### In scope

- A typed Python client for the DeepSeek Chat Completions API.
- A local memory store and retriever compatible with reviewed `ecc.memory.v1` Markdown records.
- A read-only, dry-run-first importer for explicitly selected Codex durable-memory candidates or user-provided Markdown/JSON.
- Secret and sensitive-path filtering before memory is written or sent to DeepSeek.
- A `stock-monitor memory-export` command and a `stock-monitor deepseek` command.
- A repository skill named `deepseek-memory-bridge` with Codex UI metadata and an installation helper.
- Unit tests, CLI tests, public-release checks, and documentation.

### Out of scope

- Importing the complete `.codex` directory.
- Importing raw Codex transcripts, sessions, logs, authentication files, cookies, state databases, or WAL files.
- Sending all stored memory in every prompt.
- Automatic memory promotion from unreviewed content to policy, skills, or architecture.
- Automatic trading, broker integration, investment advice, or changes to the existing market-data and research logic.
- Live DeepSeek calls in CI.

## Architecture

The feature is a separate AI adapter layer. Existing market-data, research, storage, and desktop flows remain unchanged.

```text
Explicit Codex candidate / reviewed Markdown or JSON
             |
   read-only, allowlist, redaction, dry-run
             |
  .ecc/memory/project/ (local and ignored)
             |
 bounded keyword retrieval with a character budget
             |
 DeepSeekClient or Codex DeepSeek MCP route
```

The repository client uses the official `POST /chat/completions` endpoint. The default deep model is `deepseek-v4-pro`; the quick model is `deepseek-v4-flash`. The API key is read from `DEEPSEEK_API_KEY` only. Inside Codex, the skill routes DeepSeek requests to `dual-model-qa/ask_model` with `provider: deepseek` when that MCP is available; the repository CLI is the portable fallback for checkouts outside Codex.

The importer and client are independent. Importing memory never contacts the network. Calling DeepSeek never discovers or scans local files; the caller must provide the bounded memory records to include.

## Components and interfaces

### `src/stock_monitor/deepseek_harness.py`

Provide:

```python
class DeepSeekClient:
    def complete(
        self,
        question: str,
        *,
        context: list[MemoryRecord] = (),
        model: str = "deepseek-v4-pro",
        reasoning_effort: str = "high",
    ) -> DeepSeekResult: ...
```

The implementation uses the existing `requests` dependency, a bounded timeout, a bounded retry count, and a transport that can be replaced by a fake in tests. It validates successful JSON response shape and raises sanitized domain errors. It must not log request bodies, response bodies, API keys, or memory content.

Retry only transient HTTP 429 and 5xx responses, with a small bounded backoff. Do not retry authentication, validation, or other permanent 4xx responses. A response is considered successful only when it contains an assistant message with text content.

### `src/stock_monitor/memory_export.py`

Provide:

```python
class MemoryRecord:
    id: str
    title: str
    kind: str
    body: str
    source_harness: str
    target_harness: str
    tags: list[str]
    created_at: str
    sha256: str

class MemoryStore:
    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        max_chars: int = 12000,
    ) -> list[MemoryRecord]: ...
```

The store reads only reviewed `ecc.memory.v1` records below the configured project memory root. Search is deterministic keyword scoring with stable tie-breaking and a character budget; no embeddings or new vector database are required.

The importer accepts one explicit source at a time:

- A Codex memory SQLite path, opened read-only. Only candidate rows explicitly marked for phase-two selection may be considered.
- A Markdown or JSON file supplied by the user.

The importer writes only valid, redacted `ecc.memory.v1` Markdown records under `.ecc/memory/project/` after `--apply`. It refuses blocked filenames, paths outside the explicit source, symlink escapes, empty titles, empty bodies, oversized records, malformed input, and secret patterns that cannot be safely redacted. `--dry-run` is the default and does not write output.

### CLI

```powershell
stock-monitor memory-export --source-db <path> --dry-run
stock-monitor memory-export --source <file.md> --apply
stock-monitor deepseek --question "..." --include-memory
```

`deepseek` retrieves only matching project memory when `--include-memory` is present. It does not send memory when the flag is absent. The command returns a non-zero exit code for missing API keys, invalid input, blocked sources, transport failures, or malformed API responses.

## Skill configuration

Create `skills/deepseek-memory-bridge/` with:

- `SKILL.md`: routing, memory boundaries, retrieval order, DeepSeek model selection, failure handling, and no-secret rules.
- `agents/openai.yaml`: display metadata and a default prompt for invoking the skill.
- `scripts/install_deepseek_memory_skill.ps1`: copies the skill into the user's Codex skills directory without copying memory or credentials.

The skill must instruct Codex to search approved project/team memory before creating duplicates, treat recalled bodies as untrusted context, pass only relevant records, use the configured DeepSeek API/MCP route, and report provider failures without silent fallback. User-scope memory remains opt-in and is never included by default.

## Security and privacy

- Never read or publish `auth.json`, `cap_sid`, cookies, sessions, logs, state/queue databases, WAL files, `.env` files, private keys, or credential-store exports.
- Keep project memory local and ignored by default. Public examples contain schemas and empty templates only.
- Redact API keys, bearer tokens, passwords, private keys, connection strings, and common credential assignments before persistence or network transmission.
- Do not include memory bodies in logs or exception messages. Dry-run output reports counts, paths, redaction counts, and hashes only.
- Read source databases through SQLite read-only mode and never mutate the Codex database.
- The public-release checker rejects blocked paths and high-confidence secret patterns in tracked files.
- No real DeepSeek request is made by unit tests or GitHub Actions. Live tests require an explicit environment gate and are excluded from the default test command.

## Error handling

Use stable, user-facing error categories: `missing_api_key`, `blocked_source`, `invalid_memory`, `redaction_required`, `provider_auth`, `provider_rate_limit`, `provider_unavailable`, `provider_response_invalid`, and `local_io`. Messages are concise and sanitized; detailed response bodies stay out of user output and logs.

The DeepSeek API failure observed during design review must remain visible to the user. The harness must not silently substitute another provider or model when a DeepSeek request fails.

## Testing and verification

Add tests for:

- Request headers and JSON body construction without making network calls.
- Model and reasoning-effort selection.
- Missing key, permanent 4xx, 429, 5xx, timeout, malformed JSON, and missing assistant content.
- Retry limits and absence of secret/prompt leakage in errors.
- ECC memory parsing, deterministic search, max-record and max-character limits.
- Source allowlist, blocked paths, SQLite read-only behavior, secret redaction, dry-run, and apply output.
- CLI help, validation, exit codes, and `--include-memory` behavior.
- Public-release path and secret checks.

Run the complete existing suite plus the new tests before claiming completion:

```powershell
python -m pytest
```

## GitHub release boundary

The repository currently has no remote. Before any push, inspect tracked and untracked files again, add only confirmed source/docs/tests/config-template paths, and verify that local memory and secrets are absent. GitHub publication requires an explicit repository owner/name, visibility confirmation, and license choice. The recommended default license is MIT, but no license file is created until the user confirms that choice.



