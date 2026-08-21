from __future__ import annotations

import fnmatch
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BLOCKED_NAMES = {"auth.json", "cap_sid", "cookies.json", "session_index.jsonl"}
BLOCKED_COMPONENTS = {
    "auth",
    "session",
    "logs",
    "log",
    "state",
    "queue",
    "credentials",
    "secrets",
    "private",
}
BLOCKED_GLOBS = (
    ".env",
    ".env.*",
    "auth*",
    "session*",
    "log*",
    "state*",
    "queue*",
    "private_key*",
    "id_rsa*",
    "id_ed25519*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
)
BLOCKED_SUFFIXES = (".db-wal", ".db-shm", "-wal", "-shm", ".wal", ".shm")


def _secret_assignment_pattern(key: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?ix)((?<![A-Za-z0-9_-])[\"']?{key}[\"']?\s*[=:]\s*[\"']?)([^\s,;'\"}}\]]+)"
    )


SECRET_PATTERNS = (
    ("api_key", _secret_assignment_pattern(r"api[_-]?key")),
    ("access_token", _secret_assignment_pattern(r"access[_-]?token")),
    ("refresh_token", _secret_assignment_pattern(r"refresh[_-]?token")),
    ("token", _secret_assignment_pattern("token")),
    ("bearer", re.compile(r"(?i)(bearer\s+)([^\s]+)")),
    ("password", _secret_assignment_pattern("password")),
    ("sk_token", re.compile(r"(?i)(^|[^A-Za-z0-9_-])(sk-[A-Za-z0-9_-]+)", re.M)),
    (
        "private_key",
        re.compile(
            r"-----BEGIN [^-]+ PRIVATE KEY-----.*?-----END [^-]+ PRIVATE KEY-----",
            re.S,
        ),
    ),
)
_KNOWN_KEYS = {
    "format",
    "id",
    "title",
    "kind",
    "source_harness",
    "target_harness",
    "tags",
    "created_at",
    "trust",
}


class MemoryExportError(ValueError):
    pass


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    title: str
    kind: str
    body: str
    source_harness: str
    target_harness: str
    tags: tuple[str, ...]
    created_at: str
    sha256: str


@dataclass(frozen=True)
class ImportReport:
    candidates: int = 0
    written: int = 0
    redacted: int = 0
    files: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryStore:
    root: Path

    def search(self, query: str, limit: int = 8, max_chars: int = 12000) -> list[MemoryRecord]:
        query_tokens = _tokens(query)
        scored: list[tuple[int, MemoryRecord]] = []
        if not self.root.exists():
            return []
        for path in sorted(self.root.glob("*.md")):
            try:
                record = parse_memory_markdown(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, MemoryExportError):
                continue
            score = len(query_tokens & _tokens(" ".join((record.title, record.body, *record.tags))))
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].title, item[1].id))
        results: list[MemoryRecord] = []
        used = 0
        for _, record in scored:
            size = len(record.body)
            if results and used + size > max_chars:
                continue
            if not results and size > max_chars:
                continue
            results.append(record)
            used += size
            if len(results) >= limit:
                break
        return results


def parse_memory_markdown(text: str) -> MemoryRecord:
    return _record_from_markdown(text)


def redact_text(text: str) -> tuple[str, int]:
    count = 0
    redacted = text
    for name, pattern in SECRET_PATTERNS:
        def replace(match: re.Match[str], label: str = name) -> str:
            nonlocal count
            count += 1
            if label == "private_key":
                return "[REDACTED:private_key]"
            redaction_label = "token" if label == "sk_token" else label
            return f"{match.group(1)}[REDACTED:{redaction_label}]"

        redacted = pattern.sub(replace, redacted)
    return redacted, count


def export_source(
    source: Path | None,
    source_db: Path | None,
    output_root: Path,
    apply: bool,
    *,
    source_root: Path | None = None,
) -> ImportReport:
    """Import one local source, bounded by its containing directory or source_root.

    When source_root is supplied, the resolved source must remain below that
    explicit directory. Without it, the source's own resolved parent is the
    boundary, which still prevents symlink escapes while accepting explicit
    source paths outside the output tree.
    """
    if (source is None) == (source_db is None):
        raise MemoryExportError("exactly one source must be supplied")
    output_root = Path(output_root)
    input_path = Path(source if source is not None else source_db)
    boundary = Path(source_root) if source_root is not None else input_path.parent
    _validate_source(input_path, boundary)
    records = list(_records_from_file(input_path) if source is not None else _records_from_sqlite(input_path))
    if not apply:
        return ImportReport(candidates=len(records))

    prepared: list[tuple[Path, str]] = []
    redacted_count = 0
    for record in records:
        exported, count = _redact_record(record)
        redacted_count += count
        destination = output_root / f"{_filename_hash(record, exported.body)[:16]}.md"
        prepared.append((destination, _record_text(exported)))
    destinations = [destination for destination, _ in prepared]
    if len(set(destinations)) != len(destinations) or any(
        destination.exists() for destination in destinations
    ):
        raise MemoryExportError("destination_exists: export target already exists")

    output_root.mkdir(parents=True, exist_ok=True)
    gitignore = output_root / ".gitignore"
    if not gitignore.exists():
        try:
            with gitignore.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write("*\n!.gitignore\n")
        except FileExistsError:
            pass
    written: list[str] = []
    for destination, content in prepared:
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError:
            raise MemoryExportError(
                "destination_exists: export target already exists"
            ) from None
        written.append(str(destination))
    return ImportReport(
        candidates=len(records), written=len(written), redacted=redacted_count, files=tuple(written)
    )


def _record_from_markdown(text: str) -> MemoryRecord:
    fields, body = _frontmatter(text)
    if fields.get("format") != "ecc.memory.v1":
        raise MemoryExportError("format must be ecc.memory.v1")
    body = body if body.endswith("\n") else body + "\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return MemoryRecord(
        id=fields.get("id", f"mem-{digest[:16]}"),
        title=fields.get("title", "Untitled memory"),
        kind=fields.get("kind", "context"),
        body=body,
        source_harness=fields.get("source_harness", "unknown"),
        target_harness=fields.get("target_harness", "all"),
        tags=_normalize_tags(fields.get("tags", "")),
        created_at=fields.get("created_at", "unknown"),
        sha256=digest,
    )


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        raise MemoryExportError("unterminated frontmatter")
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() in _KNOWN_KEYS:
            fields[key.strip()] = value.strip()
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    return fields, body


def _normalize_tags(value: str) -> tuple[str, ...]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return tuple(tag.strip().strip("'\"") for tag in value.split(",") if tag.strip())


def _record_text(record: MemoryRecord) -> str:
    tags = ", ".join(record.tags)
    return (
        "---\n"
        "format: ecc.memory.v1\n"
        f"id: {record.id}\n"
        f"title: {record.title}\n"
        f"kind: {record.kind}\n"
        f"source_harness: {record.source_harness}\n"
        f"target_harness: {record.target_harness}\n"
        f"tags: [{tags}]\n"
        f"created_at: {record.created_at}\n"
        "trust: unreviewed\n"
        "---\n"
        f"{record.body if record.body.endswith(chr(10)) else record.body + chr(10)}"
    )


def _redact_record(record: MemoryRecord) -> tuple[MemoryRecord, int]:
    body, count = redact_text(record.body)
    metadata = []
    for value in (
        record.id,
        record.title,
        record.kind,
        record.source_harness,
        record.target_harness,
        record.created_at,
    ):
        redacted, metadata_count = redact_text(value)
        metadata.append(redacted)
        count += metadata_count
    tags: list[str] = []
    for tag in record.tags:
        redacted, metadata_count = redact_text(tag)
        tags.append(redacted)
        count += metadata_count
    return MemoryRecord(
        id=metadata[0],
        title=metadata[1],
        kind=metadata[2],
        body=body,
        source_harness=metadata[3],
        target_harness=metadata[4],
        tags=tuple(tags),
        created_at=metadata[5],
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    ), count


def _filename_hash(record: MemoryRecord, redacted_body: str) -> str:
    identity = "\x1f".join(
        (
            record.id,
            record.title,
            record.kind,
            record.source_harness,
            record.target_harness,
            ",".join(record.tags),
            record.created_at,
            redacted_body,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", value.lower()))


def _is_blocked(path: Path) -> bool:
    name = path.name.lower()
    return (
        any(part.lower() in BLOCKED_COMPONENTS for part in path.parts)
        or
        name in BLOCKED_NAMES
        or any(fnmatch.fnmatch(name, pattern) for pattern in BLOCKED_GLOBS)
        or any(name.endswith(suffix) for suffix in BLOCKED_SUFFIXES)
    )


def _validate_source(path: Path, boundary: Path) -> None:
    resolved = path.resolve()
    allowed_parent = boundary.resolve()
    try:
        resolved.relative_to(allowed_parent)
    except ValueError as exc:
        raise MemoryExportError("blocked_source: path escapes supplied parent") from exc
    if _is_blocked(resolved):
        raise MemoryExportError("blocked_source: sensitive path")
    if not resolved.is_file():
        raise MemoryExportError("blocked_source: source is not a file")


def _records_from_file(path: Path) -> Iterable[MemoryRecord]:
    text = path.read_text(encoding="utf-8")
    yield _record_from_markdown(text)


def _records_from_sqlite(path: Path) -> Iterable[MemoryRecord]:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        for (table,) in tables:
            columns = {
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")')
            }
            if {"raw_memory", "selected_for_phase2"} - columns:
                continue
            query = f'SELECT raw_memory FROM "{table.replace(chr(34), chr(34) * 2)}" WHERE selected_for_phase2 = 1 AND raw_memory IS NOT NULL AND TRIM(raw_memory) <> \"\"'
            for (raw_memory,) in connection.execute(query):
                yield _record_from_markdown(raw_memory)
