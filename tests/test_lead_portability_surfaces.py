"""Semantic portability contract for the packaged coordination lead."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
PACKAGED = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "agents" / "trw-lead.md"
PROJECTED = ROOT / ".claude" / "agents" / "trw-lead.md"


@pytest.mark.parametrize("path", [PACKAGED, pytest.param(PROJECTED, marks=requires_monorepo)])
def test_lead_is_capability_conditional_and_workspace_safe(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    lowered = content.lower()
    for phrase in (
        "available harness capabilities",
        "shared workspace",
        "project-native validation",
        "substantive review",
        "does not write production code",
        "explicit authorization",
    ):
        assert phrase in lowered, f"{path}: missing {phrase!r}"
    for forbidden in (
        "sonnet",
        "commit or stash",
        "git merge",
        "git worktree remove",
        "correlation >=",
        "mypy: --strict",
        "review score >=",
        "30-50k",
        "3-4x",
    ):
        assert forbidden not in lowered, f"{path}: retains {forbidden!r}"
