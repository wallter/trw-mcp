"""Predecessor cleanup must cover every managed client skill root."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import _migrate_prefix_predecessors

SKILL_ROOTS = (
    ".claude/skills",
    ".agents/skills",
    ".cursor/skills",
    ".github/skills",
    ".opencode/skills",
)


@pytest.mark.parametrize("relative_root", SKILL_ROOTS)
def test_retired_skill_removed_from_every_managed_client(tmp_path: Path, relative_root: str) -> None:
    retired = tmp_path / relative_root / "trw-review-pr"
    retired.mkdir(parents=True)
    (retired / "SKILL.md").write_text("retired", encoding="utf-8")
    result: dict[str, list[str]] = {"updated": [], "errors": []}

    _migrate_prefix_predecessors(tmp_path, result)

    assert not retired.exists()
    assert result["updated"] == [f"migrated:{retired}"]


@pytest.mark.parametrize("relative_root", SKILL_ROOTS)
def test_active_predecessor_waits_for_successor(tmp_path: Path, relative_root: str) -> None:
    predecessor = tmp_path / relative_root / "commit"
    predecessor.mkdir(parents=True)
    (predecessor / "SKILL.md").write_text("legacy", encoding="utf-8")
    result: dict[str, list[str]] = {"updated": [], "errors": []}

    _migrate_prefix_predecessors(tmp_path, result)

    assert predecessor.exists()
    assert result["updated"] == []


@pytest.mark.parametrize("relative_root", SKILL_ROOTS[1:])
def test_non_claude_custom_skill_survives_matching_successor(tmp_path: Path, relative_root: str) -> None:
    skills_root = tmp_path / relative_root
    custom = skills_root / "commit"
    successor = skills_root / "trw-commit"
    custom.mkdir(parents=True)
    successor.mkdir()
    (custom / "SKILL.md").write_text("custom", encoding="utf-8")
    (successor / "SKILL.md").write_text("managed", encoding="utf-8")
    result: dict[str, list[str]] = {"updated": [], "errors": []}

    _migrate_prefix_predecessors(tmp_path, result)

    assert custom.exists()
    assert successor.exists()
    assert result["updated"] == []
