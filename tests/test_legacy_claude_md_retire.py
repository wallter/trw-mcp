"""TRW 8.0: a TRW-only root CLAUDE.md is removed on update; user content is never touched."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._template_updater import _update_mcp_config
from trw_mcp.state.claude_md._orphan_strip import retire_legacy_claude_md

_BLOCK = (
    "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n<!-- trw:start -->\nold protocol\n<!-- trw:end -->\n"
)


@pytest.mark.parametrize(
    "content",
    [
        "@AGENTS.md\n",
        "# CLAUDE.md\n\n@AGENTS.md\n",
        _BLOCK,
        "# Project Instructions\n\n## What This Is\n\n{Describe your project here}\n\n" + _BLOCK,
        "@AGENTS.md\n@.trw/INSTRUCTIONS.md\n@.trw/COPILOT-INSTRUCTIONS.md\n",
    ],
)
def test_trw_only_claude_md_is_removed(tmp_path: Path, content: str) -> None:
    (tmp_path / "CLAUDE.md").write_text(content, encoding="utf-8")
    assert retire_legacy_claude_md(tmp_path) == "removed"
    assert not (tmp_path / "CLAUDE.md").exists()


def test_symlink_to_agents_md_is_removed_and_agents_md_kept(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# mine\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").symlink_to("AGENTS.md")
    assert retire_legacy_claude_md(tmp_path) == "removed"
    assert not (tmp_path / "CLAUDE.md").is_symlink()
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "# mine\n"


def test_user_content_is_kept_byte_identical(tmp_path: Path) -> None:
    content = "# Team notes\n\nRun `make test`.\n\n" + _BLOCK
    (tmp_path / "CLAUDE.md").write_text(content, encoding="utf-8")
    assert retire_legacy_claude_md(tmp_path) == "kept"
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == content


def test_user_authored_trw_import_is_kept(tmp_path: Path) -> None:
    content = "@AGENTS.md\n@.trw/my-team-notes.md\n"
    (tmp_path / "CLAUDE.md").write_text(content, encoding="utf-8")
    assert retire_legacy_claude_md(tmp_path) == "kept"
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == content


def test_update_keeps_pointer_when_agents_md_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _template_claude_md

    _claude_code_project(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")

    def failing_write(_target: Path, result: dict[str, list[str]]) -> None:
        result.setdefault("errors", []).append("AGENTS.md: disk full")

    monkeypatch.setattr(_template_claude_md, "write_claude_code_agents_md", failing_write)
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}

    _update_mcp_config(tmp_path, result)

    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    assert "removed" not in result


def test_no_file_is_a_no_op(tmp_path: Path) -> None:
    assert retire_legacy_claude_md(tmp_path) is None


def _claude_code_project(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("target_platforms:\n- claude-code\n", encoding="utf-8")


def test_update_writes_agents_md_then_retires_pointer(tmp_path: Path) -> None:
    _claude_code_project(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}

    _update_mcp_config(tmp_path, result)

    assert not (tmp_path / "CLAUDE.md").exists()
    assert "<!-- trw:start -->" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert str(tmp_path / "CLAUDE.md") in result["removed"]
    assert not result["errors"]


def test_update_reports_user_claude_md_and_leaves_it(tmp_path: Path) -> None:
    _claude_code_project(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Team notes\n", encoding="utf-8")
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}

    _update_mcp_config(tmp_path, result)

    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "# Team notes\n"
    assert any("CLAUDE.md" in w and "@AGENTS.md" in w for w in result["warnings"])


@pytest.mark.parametrize("newline", ["\r", "\r\n"])
def test_trw_only_claude_md_with_cr_line_endings_is_removed(tmp_path: Path, newline: str) -> None:
    """CLAUDE-MD-RETIRE-CR-ONLY: the TRW-only verdict ignores line endings; the bytes are still what is hashed."""
    content = ("# Project Instructions\n\n" + _BLOCK).replace("\n", newline)
    (tmp_path / "CLAUDE.md").write_bytes(content.encode("utf-8"))
    assert retire_legacy_claude_md(tmp_path) == "removed"
    assert not (tmp_path / "CLAUDE.md").exists()


def test_user_content_with_cr_line_endings_is_kept_byte_identical(tmp_path: Path) -> None:
    raw = ("@AGENTS.md\n\nMy own rule.\n" + _BLOCK).replace("\n", "\r").encode("utf-8")
    (tmp_path / "CLAUDE.md").write_bytes(raw)
    assert retire_legacy_claude_md(tmp_path) == "kept"
    assert (tmp_path / "CLAUDE.md").read_bytes() == raw
