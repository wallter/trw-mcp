"""``trw-mcp uninstall`` and a legacy root ``CLAUDE.md`` (8.0 writes ``AGENTS.md`` instead).

``update-project`` deletes a CLAUDE.md that holds only TRW content; uninstall
must not leave that file behind, and must take TRW's marker block out of a
CLAUDE.md the user also wrote in. User content is never deleted. Driven through
the uninstall subcommand handler, whole-project and ``--ide claude-code`` alike.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.server._subcommands import _run_uninstall

_BLOCK = "<!-- trw:start -->\nTRW protocol text\n<!-- trw:end -->\n"
_SCOPES = [pytest.param(None, id="whole-project"), pytest.param("claude-code", id="ide-claude-code")]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A real 8.0 claude-code install (it writes AGENTS.md), onto which each test puts a legacy CLAUDE.md."""
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="claude-code")
    assert not result["errors"], result["errors"]
    return tmp_path


def _uninstall(root: Path, *, ide: str | None, dry_run: bool = False) -> None:
    args = {"target_dir": str(root), "dry_run": dry_run, "yes": True, "delete_memory": False, "keep_memory": False}
    if ide:
        args["ide"] = ide
    _run_uninstall(argparse.Namespace(**args))


@pytest.mark.parametrize("ide", _SCOPES)
@pytest.mark.parametrize(
    "content",
    [
        pytest.param(f"# CLAUDE.md\n\n@AGENTS.md\n\n{_BLOCK}", id="pointer-and-block"),
        pytest.param(_BLOCK, id="block-only"),
        pytest.param("@AGENTS.md\n", id="pointer-only"),
    ],
)
def test_a_trw_only_legacy_claude_md_is_removed(project: Path, ide: str | None, content: str) -> None:
    claude_md = project / "CLAUDE.md"
    claude_md.write_text(content, encoding="utf-8")

    _uninstall(project, ide=ide)

    assert not claude_md.exists()


@pytest.mark.parametrize("ide", _SCOPES)
def test_a_mixed_claude_md_loses_only_the_trw_block(project: Path, ide: str | None) -> None:
    claude_md = project / "CLAUDE.md"
    claude_md.write_text(f"# My project\n\nRun make test.\n\n{_BLOCK}\nKeep this note.\n", encoding="utf-8")

    _uninstall(project, ide=ide)

    text = claude_md.read_text(encoding="utf-8")
    assert "Run make test." in text and "Keep this note." in text
    assert "trw:start" not in text and "TRW protocol text" not in text


@pytest.mark.parametrize("ide", _SCOPES)
def test_a_user_claude_md_without_trw_content_is_byte_identical(project: Path, ide: str | None) -> None:
    claude_md = project / "CLAUDE.md"
    original = "# Mine\n\nNothing from TRW here.\n"
    claude_md.write_text(original, encoding="utf-8")

    _uninstall(project, ide=ide)

    assert claude_md.read_text(encoding="utf-8") == original


def test_a_dry_run_lists_the_legacy_file_and_changes_nothing(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    claude_md = project / "CLAUDE.md"
    claude_md.write_text(_BLOCK, encoding="utf-8")

    _uninstall(project, ide=None, dry_run=True)

    assert claude_md.read_text(encoding="utf-8") == _BLOCK
    assert "CLAUDE.md" in capsys.readouterr().out
