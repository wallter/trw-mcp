"""Codex PRD review must carry a complete fallback verdict contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import PACKAGE_ROOT, requires_monorepo

ROOT = Path(__file__).resolve().parents[2]
CODEX_REVIEW_SKILLS = (
    pytest.param(ROOT / ".agents/skills/trw-prd-review/SKILL.md", marks=requires_monorepo, id="mirror"),
    pytest.param(PACKAGE_ROOT / "src/trw_mcp/data/codex/skills/trw-prd-review/SKILL.md", id="bundled"),
)


@pytest.mark.parametrize("path", CODEX_REVIEW_SKILLS)
def test_codex_prd_review_fallback_has_canonical_verdict_rules(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    for phrase in (
        "validation_partial: false",
        "valid: true",
        "quality_tier: approved",
        "no unresolved blocking finding",
        "evidence is fabricated",
        "smallest remediation or acceptance condition",
    ):
        assert phrase in content, f"{path}: missing {phrase!r}"
    assert "Uses fork mode" not in content
    assert "trw-requirement-reviewer" not in content
