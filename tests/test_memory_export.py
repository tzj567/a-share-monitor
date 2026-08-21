import hashlib
import sqlite3
from pathlib import Path

import pytest

from stock_monitor.memory_export import (
    ImportReport,
    MemoryExportError,
    MemoryRecord,
    MemoryStore,
    export_source,
    parse_memory_markdown,
    redact_text,
)


def _memory_markdown(body="Use the licensed provider before the public fallback."):
    return (
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
        f"{body}\n"
    )


def test_parse_and_search_ecc_memory(tmp_path):
    root = tmp_path / ".ecc" / "memory" / "project"
    root.mkdir(parents=True)
    (root / "decision.md").write_text(_memory_markdown(), encoding="utf-8")

    store = MemoryStore(root)
    result = store.search("licensed provider", limit=1)

    assert result[0].id == "mem-1"
    assert result[0].sha256 == hashlib.sha256(
        b"Use the licensed provider before the public fallback.\n"
    ).hexdigest()


def test_parser_normalizes_tags_and_rejects_wrong_format():
    record = parse_memory_markdown(_memory_markdown().replace("[data, architecture]", "data, architecture"))
    assert record.tags == ("data", "architecture")

    with pytest.raises(MemoryExportError, match="format"):
        parse_memory_markdown(_memory_markdown().replace("ecc.memory.v1", "other.v1"))


def test_search_is_deterministic_and_respects_character_limit(tmp_path):
    root = tmp_path / ".ecc" / "memory" / "project"
    root.mkdir(parents=True)
    for memory_id, title in (("mem-2", "Beta"), ("mem-1", "Alpha")):
        text = _memory_markdown("licensed provider")
        text = text.replace("mem-1", memory_id).replace("Data source decision", title)
        (root / f"{memory_id}.md").write_text(text, encoding="utf-8")

    result = MemoryStore(root).search("licensed provider", limit=8, max_chars=25)
    assert [record.id for record in result] == ["mem-1"]


def test_redaction_removes_secrets_without_logging_body():
    redacted, count = redact_text(
        "token=sk-live-example password=hunter2 Bearer abc.def.ghi"
    )
    assert count == 3
    assert "sk-live-example" not in redacted
    assert "hunter2" not in redacted
    assert "abc.def.ghi" not in redacted


def test_redaction_covers_quoted_keys_and_bare_sk_tokens():
    secrets = (
        "api-value",
        "access-value",
        "refresh-value",
        "token-value",
        "password-value",
        "sk-live-abcdefghijklmnopqrstuvwxyz",
    )
    redacted, count = redact_text(
        '"api_key": "api-value", '
        "'access_token': 'access-value', "
        '"refresh_token":"refresh-value", '
        "'token': 'token-value', "
        '"password": "password-value", '
        "bare sk-live-abcdefghijklmnopqrstuvwxyz"
    )

    assert count == len(secrets)
    assert all(secret not in redacted for secret in secrets)


def test_private_key_redaction_is_counted():
    redacted, count = redact_text(
        "-----BEGIN RSA PRIVATE KEY-----secret-----END RSA PRIVATE KEY-----"
    )
    assert count == 1
    assert "PRIVATE KEY" not in redacted


def test_export_is_dry_run_by_default_and_blocks_sensitive_paths(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text(_memory_markdown("Keep this local."), encoding="utf-8")
    output = tmp_path / ".ecc" / "memory" / "project"
    report = export_source(source, None, output, apply=False)
    assert isinstance(report, ImportReport)
    assert report.written == 0
    assert not output.exists()

    blocked = tmp_path / "auth.json"
    blocked.write_text("{}", encoding="utf-8")
    with pytest.raises(MemoryExportError, match="blocked_source"):
        export_source(blocked, None, output, apply=False)


def test_export_applies_redacted_record_and_ignores_unselected_sqlite_rows(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text(
        "---\n"
        "format: ecc.memory.v1\n"
        "id: imported-1\n"
        "title: Imported\n"
        "kind: context\n"
        "source_harness: codex\n"
        "target_harness: all\n"
        "tags: [safe]\n"
        "created_at: 2026-08-20T00:00:00Z\n"
        "---\n"
        "token=secret\n",
        encoding="utf-8",
    )
    output = tmp_path / ".ecc" / "memory" / "project"

    report = export_source(source, None, output, apply=True)

    assert report.written == 1
    assert (output / ".gitignore").read_text(encoding="utf-8") == "*\n!.gitignore\n"
    exported = next(path for path in output.glob("*.md"))
    assert "secret" not in exported.read_text(encoding="utf-8")

    database = tmp_path / "memory.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories (raw_memory TEXT, selected_for_phase2 INTEGER)")
        connection.executemany(
            "INSERT INTO memories VALUES (?, ?)",
            [(_memory_markdown("selected"), 1), ("ignored", 0), ("", 1)],
        )
        connection.commit()
    sqlite_report = export_source(None, database, tmp_path / "sqlite-output", apply=False)
    assert sqlite_report.candidates == 1
    assert sqlite_report.written == 0


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("notes.md", "Private project notes without ECC frontmatter.\n"),
        (
            "raw-transcript.md",
            "---\ntitle: Raw transcript\n---\nUser: keep this conversation private.\n",
        ),
    ],
)
def test_export_rejects_source_files_without_ecc_memory_format(tmp_path, filename, contents):
    source = tmp_path / filename
    source.write_text(contents, encoding="utf-8")

    with pytest.raises(MemoryExportError, match="format must be ecc.memory.v1"):
        export_source(source, None, tmp_path / "output", apply=False, source_root=tmp_path)


def test_export_rejects_sqlite_raw_transcript_without_ecc_memory_format(tmp_path):
    database = tmp_path / "memory.sqlite"
    raw_transcript = "---\ntitle: Transcript\n---\nUser: private transcript text.\n"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories (raw_memory TEXT, selected_for_phase2 INTEGER)")
        connection.execute("INSERT INTO memories VALUES (?, 1)", (raw_transcript,))
        connection.commit()

    with pytest.raises(MemoryExportError, match="format must be ecc.memory.v1"):
        export_source(None, database, tmp_path / "output", apply=False, source_root=tmp_path)


def test_repeated_apply_refuses_to_overwrite_exported_memory(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text(_memory_markdown("stable body"), encoding="utf-8")
    output = tmp_path / "output"
    first_report = export_source(source, None, output, apply=True, source_root=tmp_path)
    destination = Path(first_report.files[0])
    original = destination.read_text(encoding="utf-8")

    with pytest.raises(MemoryExportError) as caught:
        export_source(source, None, output, apply=True, source_root=tmp_path)

    assert str(caught.value) == "destination_exists: export target already exists"
    assert destination.read_text(encoding="utf-8") == original


def test_apply_refuses_preexisting_target_without_modifying_it(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text(_memory_markdown("stable body"), encoding="utf-8")
    staging_report = export_source(
        source,
        None,
        tmp_path / "staging",
        apply=True,
        source_root=tmp_path,
    )
    destination_name = Path(staging_report.files[0]).name
    output = tmp_path / "output"
    output.mkdir()
    destination = output / destination_name
    destination.write_text("preexisting sentinel\n", encoding="utf-8")

    with pytest.raises(MemoryExportError) as caught:
        export_source(source, None, output, apply=True, source_root=tmp_path)

    assert str(caught.value) == "destination_exists: export target already exists"
    assert destination.read_text(encoding="utf-8") == "preexisting sentinel\n"


def test_export_requires_exactly_one_source_and_rejects_path_escape(tmp_path):
    output = tmp_path / "output"
    source = tmp_path / "notes.md"
    source.write_text(_memory_markdown(), encoding="utf-8")

    with pytest.raises(MemoryExportError, match="exactly one"):
        export_source(None, None, output, apply=False)
    with pytest.raises(MemoryExportError, match="exactly one"):
        export_source(source, source, output, apply=False)

    outside = tmp_path.parent.parent / "outside.md"
    outside.write_text(_memory_markdown(), encoding="utf-8")
    with pytest.raises(MemoryExportError, match="blocked_source"):
        export_source(outside, None, tmp_path / "parent" / "output", apply=False, source_root=tmp_path)


@pytest.mark.parametrize("filename", [".env", ".env.production", "logs_sensitive.sqlite", "state_sensitive.sqlite", "id_rsa.pem"])
def test_export_blocks_sensitive_source_names_before_read(tmp_path, filename):
    source = tmp_path / filename
    source.write_text(_memory_markdown(), encoding="utf-8")

    with pytest.raises(MemoryExportError, match="blocked_source"):
        export_source(source, None, tmp_path / "output", apply=False, source_root=tmp_path)


def test_export_keeps_distinct_records_with_equal_bodies(tmp_path):
    database = tmp_path / "memory.sqlite"
    first = _memory_markdown("same body").replace("mem-1", "mem-1").replace("Data source decision", "First")
    second = _memory_markdown("same body").replace("mem-1", "mem-2").replace("Data source decision", "Second")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories (raw_memory TEXT, selected_for_phase2 INTEGER)")
        connection.executemany("INSERT INTO memories VALUES (?, 1)", [(first,), (second,)])
        connection.commit()

    output = tmp_path / "output"
    report = export_source(source_db=database, source=None, output_root=output, apply=True, source_root=tmp_path)

    assert report.written == 2
    assert len(list(output.glob("*.md"))) == 2


def test_search_character_budget_counts_characters_for_multibyte_body(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "unicode.md").write_text(_memory_markdown("你"), encoding="utf-8")

    result = MemoryStore(root).search("data", max_chars=2)

    assert [record.body for record in result] == ["你\n"]


def test_export_redacts_secrets_in_frontmatter_metadata(tmp_path):
    source = tmp_path / "metadata.md"
    source.write_text(
        _memory_markdown("safe body")
        .replace("title: Data source decision", "title: token=title-secret")
        .replace("tags: [data, architecture]", "tags: [password=tag-secret]")
        .replace("source_harness: codex", "source_harness: Bearer source-secret"),
        encoding="utf-8",
    )

    output = tmp_path / "output"
    export_source(source, None, output, apply=True, source_root=tmp_path)
    exported = next(output.glob("*.md"))
    content = exported.read_text(encoding="utf-8")

    assert "title-secret" not in content
    assert "tag-secret" not in content
    assert "source-secret" not in content


def test_export_blocks_sensitive_path_components_but_allows_ordinary_nested_paths(tmp_path):
    sensitive = tmp_path / "session" / "notes.md"
    sensitive.parent.mkdir()
    sensitive.write_text(_memory_markdown(), encoding="utf-8")
    with pytest.raises(MemoryExportError, match="blocked_source"):
        export_source(sensitive, None, tmp_path / "output", apply=False, source_root=tmp_path)

    ordinary = tmp_path / "ordinary" / "nested" / "notes.md"
    ordinary.parent.mkdir(parents=True)
    ordinary.write_text(_memory_markdown(), encoding="utf-8")
    report = export_source(ordinary, None, tmp_path / "ordinary-output", apply=False, source_root=tmp_path)
    assert report.candidates == 1


def test_export_keeps_records_distinct_when_only_secret_metadata_differs(tmp_path):
    database = tmp_path / "memory.sqlite"
    first = _memory_markdown("same body").replace("title: Data source decision", "title: token=secret-one")
    second = _memory_markdown("same body").replace("title: Data source decision", "title: token=secret-two")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memories (raw_memory TEXT, selected_for_phase2 INTEGER)")
        connection.executemany("INSERT INTO memories VALUES (?, 1)", [(first,), (second,)])
        connection.commit()

    output = tmp_path / "output"
    report = export_source(source_db=database, source=None, output_root=output, apply=True, source_root=tmp_path)

    assert report.written == 2
    assert len(list(output.glob("*.md"))) == 2
