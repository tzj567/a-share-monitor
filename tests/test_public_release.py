from pathlib import Path
import subprocess
import pytest

from scripts.check_public_release import check_paths, check_tracked_files


def test_public_release_checker_rejects_private_paths(tmp_path):
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    assert any("auth.json" in item for item in check_paths(tmp_path, ["auth.json", "README.md"]))


def test_skill_metadata_has_required_frontmatter():
    skill = Path("skills/deepseek-memory-bridge/SKILL.md").read_text(encoding="utf-8")
    assert "name: deepseek-memory-bridge" in skill
    assert "DEEPSEEK_API_KEY" in skill
    assert "dual-model-qa/ask_model" in skill


def test_public_release_checker_rejects_project_memory_paths(tmp_path):
    memory_file = tmp_path / ".ecc" / "memory" / "project" / "reviewed.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("safe", encoding="utf-8")

    findings = check_paths(tmp_path, [".ecc/memory/project/reviewed.md"])

    assert any(".ecc/memory/project/reviewed.md" in item for item in findings)


def test_public_release_checker_rejects_superpowers_audit_artifacts(tmp_path):
    report = tmp_path / ".superpowers" / "sdd" / "feature" / "task-1-report.md"
    report.parent.mkdir(parents=True)
    report.write_text("internal audit", encoding="utf-8")

    findings = check_paths(tmp_path, [".superpowers/sdd/feature/task-1-report.md"])

    assert findings == ["blocked path: .superpowers/sdd/feature/task-1-report.md"]


def test_public_release_checker_allows_project_memory_gitignore(tmp_path):
    keep_file = tmp_path / ".ecc" / "memory" / "project" / ".gitignore"
    keep_file.parent.mkdir(parents=True)
    keep_file.write_text("*\n!.gitignore\n", encoding="utf-8")

    findings = check_paths(tmp_path, [".ecc/memory/project/.gitignore"])

    assert findings == []


def test_public_release_checker_detects_high_confidence_secret_without_echoing_value(tmp_path):
    secret_file = tmp_path / "config.env"
    secret_value = "sk-live-1234567890abcdefghijklmnop"
    secret_file.write_text(f"DEEPSEEK_API_KEY={secret_value}\n", encoding="utf-8")

    findings = check_paths(tmp_path, ["config.env"])

    assert any("secret pattern (deepseek_api_key)" in item for item in findings)
    assert all(secret_value not in item for item in findings)


def test_public_release_checker_allows_example_env_and_code_assignments(tmp_path):
    example_env = tmp_path / ".env.advanced.example"
    example_env.write_text("DEEPSEEK_API_KEY=replace-with-your-key\n", encoding="utf-8")
    source_file = tmp_path / "provider.py"
    source_file.write_text(
        "refresh_token = refresh_token.strip()\n"
        "token = self._cached_access_token or self._access_token()\n",
        encoding="utf-8",
    )

    findings = check_paths(tmp_path, [".env.advanced.example", "provider.py"])

    assert findings == []


@pytest.mark.parametrize(
    ("relative_path", "blocked"),
    [
        ("credentials/note.md", True),
        ("session/note.md", True),
        ("logs/trace.txt", True),
        ("auth/note.md", True),
        ("private/info.txt", True),
        ("safe/nested/info.txt", False),
    ],
)
def test_public_release_checker_handles_sensitive_nested_directories(tmp_path, relative_path, blocked):
    target = tmp_path / Path(relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("safe", encoding="utf-8")

    findings = check_paths(tmp_path, [relative_path])

    if blocked:
        assert any(relative_path in item for item in findings)
    else:
        assert findings == []


def test_check_tracked_files_uses_git_ls_files(tmp_path):
    tracked = tmp_path / "tracked.env"
    tracked.write_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payloadsignature\n", encoding="utf-8")
    untracked = tmp_path / "ignored.env"
    untracked_secret = "sk-live-" + "abcdefghijklmnopqrstuvwxyz"
    untracked.write_text(f"DEEPSEEK_API_KEY={untracked_secret}\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "tracked.env"], cwd=tmp_path, check=True, capture_output=True)

    findings = check_tracked_files(tmp_path)

    assert any("tracked.env" in item for item in findings)
    assert all("ignored.env" not in item for item in findings)
