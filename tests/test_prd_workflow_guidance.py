"""Regression coverage for PRD-QUAL-059 workflow guidance hardening."""

from __future__ import annotations

from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent
_REPO_ROOT = _TESTS_DIR.parent.parent
_PKG_DATA = _TESTS_DIR.parent / "src" / "trw_mcp" / "data"

_GUIDANCE_EXPECTATIONS = {
    _PKG_DATA / "skills" / "trw-prd-ready" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "codex" / "skills" / "trw-prd-ready" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "opencode" / "skills" / "trw-prd-ready" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "skills" / "trw-exec-plan" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "codex" / "skills" / "trw-exec-plan" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "codex" / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "copilot" / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "agents" / "trw-prd-groomer.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "agents" / "trw-lead.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "copilot" / "agents" / "trw-lead.agent.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "copilot" / "plugin" / "agents" / "trw-lead.agent.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "skills" / "trw-prd-ready" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "skills" / "trw-exec-plan" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".github" / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "agents" / "trw-prd-groomer.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "agents" / "trw-lead.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
}


@pytest.mark.unit
@pytest.mark.parametrize(("path", "required_snippets"), _GUIDANCE_EXPECTATIONS.items(), ids=lambda item: str(item))
def test_prd_workflow_guidance_mentions_hardened_readiness_semantics(
    path: Path,
    required_snippets: list[str],
) -> None:
    content = path.read_text(encoding="utf-8").lower()
    for snippet in required_snippets:
        assert snippet in content, f"{path} is missing '{snippet}'"
