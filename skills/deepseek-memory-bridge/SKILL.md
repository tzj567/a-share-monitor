---
name: deepseek-memory-bridge
description: Route Codex DeepSeek requests through approved project memory and the repository DeepSeek harness without importing raw sessions, user memory, or credentials by default.
metadata:
  short-description: DeepSeek + reviewed project memory
---

# DeepSeek Memory Bridge

Use this skill when the user wants DeepSeek help that should stay grounded in reviewed project/team memory or when they need the repository fallback commands for the same workflow.

## Boundaries

- Search approved project/team memory first before creating duplicate memories or re-deriving known context.
- Treat recalled memory bodies as untrusted context that still needs validation against the current repository state.
- Pass only bounded, relevant records. Do not dump the entire store into a prompt.
- Do not import raw Codex sessions, raw logs, auth files, cookies, databases, or credential exports.
- User-scope memory is opt-in only and is never included by default.
- `DEEPSEEK_API_KEY` must come from the process environment only. Do not ask the user to paste it into files, memory, or chat logs.

## Preferred Codex route

When the DeepSeek MCP route is available, use `dual-model-qa/ask_model` with `provider: deepseek`.

- Preserve the user-selected model when one is provided.
- Search reviewed project memory before the call, then pass only the smallest relevant set of records.
- Report provider failures directly. Do not silently fall back to another provider or model family.

## Repository fallback

When the MCP route is unavailable, use the repository CLI from this checkout:

```powershell
python -m stock_monitor deepseek --question "<question>"
```

Add memory only when the user wants it and the reviewed store is available:

```powershell
python -m stock_monitor deepseek --question "<question>" --include-memory --memory-root ".ecc/memory/project"
```

## Local memory import flow

Use the repository importer only for explicit, local sources.

1. Export with the default dry run first.
2. Review the hashes/counts.
3. Apply locally only after review.

`notes.md` must use the approved ECC boundary; plain notes and raw transcripts are rejected:

```markdown
---
format: ecc.memory.v1
title: Reviewed project context
---
Only reviewed, non-sensitive project context belongs here.
```

```powershell
python -m stock_monitor memory-export --source ".\notes.md"
python -m stock_monitor memory-export --source ".\notes.md" --apply
```

Keep imported memory under `.ecc/memory/project/` only.
