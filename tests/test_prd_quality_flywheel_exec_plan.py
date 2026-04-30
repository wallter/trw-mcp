"""Exec plan flywheel coverage tests."""

from __future__ import annotations

from pathlib import Path


def test_exec_plan_includes_verification_commands() -> None:
    skill_path = (
        Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "skills" / "trw-exec-plan" / "SKILL.md"
    )
    content = skill_path.read_text(encoding="utf-8")

    assert "Pre-Implementation Checklist (PRD-QUAL-056-FR03)" in content
    assert "Pre-Implementation Checklist" in content
    assert "PASSED" in content
