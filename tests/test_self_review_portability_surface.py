"""Keep packaged self-review portable and applicability-driven."""

from __future__ import annotations

from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "skills" / "trw-self-review" / "SKILL.md"


def test_self_review_uses_requirement_matched_evidence() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert "test, analysis, inspection, or demonstration" in content
    assert "classify each row as `pass`, `fail`, or `n/a`" in content.lower()
    assert "Do not impose Python, CLI, detector" in content
    assert "project config or an accepted requirement" in content


def test_self_review_preserves_integration_and_safety_reachability() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert "real production consumer or an explicitly declared future seam" in content
    assert "enumerate every source-to-sink path" in content
    assert "correct but bypassed/unconsumed gate is blocking" in content


def test_self_review_covers_functionality_and_tests_without_folklore() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert "surrounding files, and tests together" in content
    assert "Trace usages before deletion" in content
    assert "stale test scaffolding" in content
    assert "DISTILLERY-DEFECT-LEDGER" not in content
    assert "60%+" not in content
    assert "zero adversarial cost" not in content
    assert "55 audit-fix" not in content
    assert "structlog" not in content
    assert "SQLite" not in content
    assert "python -m" not in content
    assert "TRW_<PRD>_LIVE" not in content
    assert "CLAUDE.md" not in content
