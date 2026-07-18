"""Semantic contracts for packaged PRD-readiness guidance."""

from __future__ import annotations

import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"

READINESS_OWNERS = (
    DATA / "skills/trw-prd-ready/SKILL.md",
    DATA / "codex/skills/trw-prd-ready/SKILL.md",
    DATA / "opencode/skills/trw-prd-ready/SKILL.md",
    DATA / "opencode/commands/trw-prd-ready.md",
    DATA / "skills/trw-prd-groom/SKILL.md",
    DATA / "codex/skills/trw-prd-groom/SKILL.md",
    DATA / "skills/trw-exec-plan/SKILL.md",
    DATA / "codex/skills/trw-exec-plan/SKILL.md",
    DATA / "agents/trw-prd-groomer.md",
    DATA / "agents/trw-lead.md",
    DATA / "copilot/agents/trw-lead.agent.md",
    DATA / "copilot/plugin/agents/trw-lead.agent.md",
)

AUDIT_VARIANTS = (
    DATA / "skills/trw-audit/SKILL.md",
    DATA / "codex/skills/trw-audit/SKILL.md",
    DATA / "copilot/skills/trw-audit/SKILL.md",
)

PRD_NEW_VARIANTS = (
    DATA / "skills/trw-prd-new/SKILL.md",
    DATA / "codex/skills/trw-prd-new/SKILL.md",
    DATA / "copilot/skills/trw-prd-new/SKILL.md",
    DATA / "copilot/plugin/skills/trw-prd-new/SKILL.md",
)

CURSOR_COMMAND = DATA / "cursor_ide/commands/trw-prd-ready.md"

FORBIDDEN_NUMERIC_GATE = re.compile(
    r"\b0\.85\b|"
    r"(?:total_score|score)\s*(?:>=|<=|<|>)\s*(?:85|65|45)|"
    r"(?:completeness_score|completeness)\s*(?:>=|<=|<|>)",
    re.IGNORECASE,
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing packaged readiness surface: {path}"
    return path.read_text(encoding="utf-8")


def test_prd_surfaces_do_not_hardcode_deprecated_readiness_gates() -> None:
    """No packaged consumer may substitute a fixed score for the risk-scaled result."""
    for path in (*READINESS_OWNERS, *AUDIT_VARIANTS, *PRD_NEW_VARIANTS, CURSOR_COMMAND):
        match = FORBIDDEN_NUMERIC_GATE.search(_read(path))
        assert match is None, f"{path} contains deprecated readiness gate: {match.group(0) if match else ''}"


def test_readiness_owners_use_the_full_risk_scaled_result() -> None:
    """Every surface that owns a gate requires full, valid, approved validation."""
    required = ("validation_partial", "valid", "quality_tier", "approved", "total_score")
    for path in READINESS_OWNERS:
        content = _read(path)
        for field in required:
            assert field in content, f"{path} omits readiness field {field}"


def test_audit_records_weak_spec_quality_without_score_aborting() -> None:
    """Adversarial audit remains usable when the specification itself is weak."""
    for path in AUDIT_VARIANTS:
        content = _read(path)
        for field in ("validation_partial", "valid", "quality_tier", "total_score"):
            assert field in content, f"{path} omits diagnostic field {field}"
        assert "Do not abort an adversarial audit solely" in content


def test_prd_new_delegates_or_supplies_a_resolvable_readiness_flow() -> None:
    """Shared/Codex delegate to ready; Copilot owns an inline flow because ready is not packaged."""
    assert "/trw-prd-ready`'s risk-scaled readiness contract" in _read(PRD_NEW_VARIANTS[0])
    assert "/trw-prd-ready`'s risk-scaled readiness contract" in _read(PRD_NEW_VARIANTS[1])
    for path in PRD_NEW_VARIANTS[2:]:
        content = _read(path)
        assert "Run the readiness pipeline inline" in content
        assert "validation_partial: false" in content

    assert _read(PRD_NEW_VARIANTS[2]) == _read(PRD_NEW_VARIANTS[3])


def test_client_mirrors_preserve_semantics_and_lifecycle_vocabulary() -> None:
    """Copilot direct/plugin lead mirrors match and Cursor does not invent READY status."""
    assert _read(READINESS_OWNERS[-2]) == _read(READINESS_OWNERS[-1])
    cursor = _read(CURSOR_COMMAND)
    assert "Sets status to READY" not in cursor
    assert "lifecycle status" in cursor
