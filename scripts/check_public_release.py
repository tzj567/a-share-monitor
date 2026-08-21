from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


BLOCKED_NAMES = {"auth.json", "cap_sid", "cookies.json", "session_index.jsonl"}
BLOCKED_COMPONENTS = {
    "auth",
    "credential",
    "credentials",
    "log",
    "logs",
    "private",
    "queue",
    "secret",
    "secrets",
    "session",
    "sessions",
    "state",
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
PLACEHOLDER_HINTS = (
    "replace-with",
    "example",
    "sample",
    "placeholder",
    "change-this",
    "changeme",
    "dummy",
    "fake",
    "test",
    "secret",
    "redacted",
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?im)\b(?P<name>deepseek_api_key|api[_-]?key|access[_-]?token|refresh[_-]?token|token|password)\b\s*[:=]\s*(?P<quote>['\"]?)(?P<value>[^\s'\";,]+)(?P=quote)"
)
AUTHORIZATION_PATTERN = re.compile(r"(?im)^\s*authorization\s*:\s*bearer\s+(?P<value>\S+)")
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-]+ PRIVATE KEY-----(?P<body>.*?)-----END [^-]+ PRIVATE KEY-----",
    re.S,
)


def check_paths(root: Path, relative_paths: Iterable[str]) -> list[str]:
    root = Path(root)
    findings: list[str] = []
    for relative_path in relative_paths:
        relative = Path(relative_path)
        normalized = relative.as_posix().lower()
        name = relative.name.lower()
        if _is_blocked_path(normalized, name):
            findings.append(f"blocked path: {relative.as_posix()}")
            continue
        file_path = root / relative
        findings.extend(_scan_file(file_path, relative.as_posix()))
    return findings


def check_tracked_files(root: Path) -> list[str]:
    root = Path(root)
    tracked = _git_ls_files(root)
    if tracked is None:
        tracked = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        ]
    return check_paths(root, tracked)


def _is_blocked_path(normalized: str, name: str) -> bool:
    parts = [part for part in normalized.split("/") if part]
    if parts and parts[0] == ".superpowers":
        return True
    if len(parts) >= 3 and parts[:3] == [".ecc", "memory", "project"]:
        if parts == [".ecc", "memory", "project", ".gitignore"]:
            return False
        return True
    if any(part in BLOCKED_COMPONENTS for part in parts[:-1]):
        return True
    if normalized.endswith(".example") or normalized.endswith(".sample"):
        return False
    if name in BLOCKED_NAMES:
        return True
    if any(fnmatch.fnmatch(name, pattern) for pattern in BLOCKED_GLOBS):
        return True
    return any(name.endswith(suffix) for suffix in BLOCKED_SUFFIXES)


def _scan_file(file_path: Path, display_path: str) -> list[str]:
    try:
        raw = file_path.read_bytes()
    except OSError:
        return [f"unreadable file: {display_path}"]
    if b"\x00" in raw:
        return []
    text = raw.decode("utf-8", errors="ignore")
    findings: list[str] = []
    for match in ASSIGNMENT_PATTERN.finditer(text):
        name = match.group("name").lower()
        value = match.group("value")
        if _looks_like_secret(value, name=name):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"secret pattern ({name}) in {display_path}:{line}")
    for match in AUTHORIZATION_PATTERN.finditer(text):
        value = match.group("value")
        if _looks_like_secret(value, name="authorization"):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"secret pattern (authorization-bearer) in {display_path}:{line}")
    for match in PRIVATE_KEY_PATTERN.finditer(text):
        body = re.sub(r"\s+", "", match.group("body"))
        if len(body) >= 64 and "secret" not in body.lower():
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"secret pattern (private-key) in {display_path}:{line}")
    return findings


def _looks_like_secret(value: str, *, name: str) -> bool:
    cleaned = value.strip().strip("'\"")
    lowered = cleaned.lower()
    if not cleaned:
        return False
    if any(char in cleaned for char in "(){}[]$"):
        return False
    if any(hint in lowered for hint in PLACEHOLDER_HINTS):
        return False
    if name == "deepseek_api_key":
        return len(cleaned) >= 12
    if cleaned.startswith("sk-") and len(cleaned) >= 16:
        return True
    if cleaned.count(".") == 2 and len(cleaned) >= 32:
        return True
    if not any(char.isdigit() for char in cleaned) and not any(char.isupper() for char in cleaned):
        return False
    classes = sum(
        (
            any(char.islower() for char in cleaned),
            any(char.isupper() for char in cleaned),
            any(char.isdigit() for char in cleaned),
            any(char in "-_~+/=" for char in cleaned),
        )
    )
    return len(cleaned) >= 20 and classes >= 2


def _git_ls_files(root: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
            text=False,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    data = result.stdout.decode("utf-8", errors="ignore")
    return [item for item in data.split("\x00") if item]


def main(argv: list[str] | None = None) -> int:
    _ = argv
    root = Path.cwd()
    findings = check_tracked_files(root)
    if not findings:
        print("public release check passed")
        return 0
    for item in findings:
        print(item, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
