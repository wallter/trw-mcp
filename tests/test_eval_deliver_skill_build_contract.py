"""Eval-local delivery skills must treat build_check as a reporter."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVAL_DELIVER_SKILLS = (
    ROOT / "trw-eval/trw-mcp-local/src/trw_mcp/data/skills/trw-deliver/SKILL.md",
    ROOT / "trw-eval/trw-mcp-local/src/trw_mcp/data/codex/skills/trw-deliver/SKILL.md",
)

# `trw-eval/` is a proprietary subtree absent from the public trw-mcp mirror.
# This is a monorepo-only contract test — skip (not FileNotFoundError) when the
# eval subtree isn't checked out. (release-verify 2026-07-17 P1 ip-deps)
_eval_subtree_present = all(p.exists() for p in EVAL_DELIVER_SKILLS)


@pytest.mark.skipif(
    not _eval_subtree_present,
    reason="trw-eval subtree not present (public mirror / eval not checked out)",
)
def test_eval_deliver_skills_run_validation_before_reporting_it() -> None:
    for path in EVAL_DELIVER_SKILLS:
        content = path.read_text(encoding="utf-8")
        assert "Run the applicable project-native" in content
        assert "trw_build_check(tests_passed=<bool>" in content
        assert "does not execute commands" in content
        assert "Raw delivery remains subject to current framework gates" in content
        assert "Instruction sync is a separate explicit lifecycle operation" in content
        assert 'trw_build_check(scope="full")' not in content
        assert "to run pytest + mypy" not in content
        assert "without build verification, call `trw_deliver()` directly" not in content
        assert "Agent Team" not in content
