"""Tests for PRD template hardening template files."""

from __future__ import annotations

from pathlib import Path


def test_canonical_prd_template_includes_control_points_and_completion_evidence() -> None:
    template_path = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "prd_template.md"
    content = template_path.read_text(encoding="utf-8")

    assert "### Primary Control Points" in content
    assert "### Behavior Switch Matrix" in content
    assert "### Migration Tests" in content
    assert "### Regression Tests" in content
    assert "### Negative / Fallback Tests" in content
    assert "### Completion Evidence (Definition of Done)" in content
    assert "### Migration / Backward Compatibility" in content


def test_repo_prd_template_matches_hardened_structure() -> None:
    template_path = Path(__file__).resolve().parents[2] / "docs" / "requirements-aare-f" / "prds" / "TEMPLATE.md"
    content = template_path.read_text(encoding="utf-8")

    assert "### Primary Control Points" in content
    assert "### Behavior Switch Matrix" in content
    assert "### Completion Evidence (Definition of Done)" in content
