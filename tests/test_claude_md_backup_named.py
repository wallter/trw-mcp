"""When TRW changes your CLAUDE.md, the report names where the previous version is kept (CANARY-ACCEPT dev22 P0).

S1 adds TRW's block to an existing CLAUDE.md and backs the previous bytes up first, but the backup's path was never
reported: an uncommitted CLAUDE.md changed with no copy the user could find (acceptance cm.*.CLAUDE.recoverable).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_USER = "# My project notes\n\nAlways run make check before pushing.\n"


def _named_copy(lines: list[str]) -> Path:
    named = [m.group(1) for w in lines if (m := re.search(r"CLAUDE\.md: .*kept at (\S+)", w))]
    assert len(named) == 1, lines
    return Path(named[0].rstrip(" ;,.)"))


@pytest.mark.parametrize("op", ["init", "update"])
def test_changing_claude_md_names_the_exact_previous_copy(tmp_path: Path, op: str) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    (tmp_path / ".git").mkdir()
    if op == "update":
        assert not init_project(tmp_path, ide="claude-code")["errors"]
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(_USER, encoding="utf-8")

    result = init_project(tmp_path, ide="claude-code") if op == "init" else update_project(tmp_path)

    assert claude_md.read_text(encoding="utf-8").startswith(_USER) and "@.trw/INSTRUCTIONS.md" in claude_md.read_text()
    copy = _named_copy(result["warnings"])
    assert copy.is_absolute() and copy.read_text(encoding="utf-8") == _USER


def test_an_unchanged_claude_md_names_no_copy(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    (tmp_path / ".git").mkdir()
    (tmp_path / "CLAUDE.md").write_text(_USER, encoding="utf-8")
    init_project(tmp_path, ide="claude-code")

    result = update_project(tmp_path)  # TRW's block is already current: nothing written, nothing to name

    assert not [w for w in result["warnings"] if "kept at" in w and "CLAUDE.md" in w]


@pytest.mark.parametrize(
    ("ide", "rel_path"),
    [("copilot", ".github/copilot-instructions.md"), ("antigravity-cli", "ANTIGRAVITY.md")],
    ids=["copilot", "antigravity"],
)
def test_other_clients_name_the_copy_only_when_it_held_user_text(tmp_path: Path, ide: str, rel_path: str) -> None:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_USER, encoding="utf-8")

    first = init_project(tmp_path, ide=ide, force=True)
    copy = [w for w in first["warnings"] if w.startswith(f"{rel_path}: ") and "kept at" in w]
    assert len(copy) == 1, first["warnings"]
    assert Path(copy[0].split("kept at ")[1].split(" (delete")[0]).read_text(encoding="utf-8") == _USER

    only_trw = target.read_text(encoding="utf-8").replace(_USER, "")
    target.write_text(only_trw.replace("TRW", "TRW old", 1), encoding="utf-8")  # TRW's block alone, stale
    second = init_project(tmp_path, ide=ide, force=True)
    assert "TRW old" not in target.read_text(encoding="utf-8"), "the stale block was refreshed (a backup was taken)"
    assert not [w for w in second["warnings"] if w.startswith(f"{rel_path}: ") and "kept at" in w], second


def test_update_names_the_previous_agents_md_that_held_user_text(tmp_path: Path) -> None:
    """CLAUDE-MD S3 (acceptance cm.*.AGENTS.recoverable): claude-code's AGENTS.md write dropped the named-copy line."""
    from trw_mcp.bootstrap import init_project, update_project

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    agents = tmp_path / "AGENTS.md"
    block = agents.read_text(encoding="utf-8")
    start = block.index("<!-- trw:start -->")
    previous = "# Agent guide (mine)\n\nUse the staging database.\n\n" + block[start:].replace("TRW", "TRW old", 1)
    agents.write_text(previous, encoding="utf-8")

    result = update_project(tmp_path)

    assert agents.read_text(encoding="utf-8").startswith("# Agent guide (mine)\n\nUse the staging database.\n")
    named = [w for w in result["warnings"] if w.startswith("AGENTS.md: ") and "kept at" in w]
    assert len(named) == 1, result["warnings"]
    assert Path(named[0].split("kept at ")[1].split(" (delete")[0]).read_text(encoding="utf-8") == previous
