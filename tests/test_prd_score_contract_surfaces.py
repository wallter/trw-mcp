"""Semantic contracts for packaged PRD-readiness guidance."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._layout import requires_monorepo

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"

READINESS_OWNERS = (
    DATA / "skills/trw-prd-ready/SKILL.md",
    DATA / "codex/skills/trw-prd-ready/SKILL.md",
    DATA / "skills/trw-prd-groom/SKILL.md",
    DATA / "codex/skills/trw-prd-groom/SKILL.md",
    DATA / "skills/trw-exec-plan/SKILL.md",
    DATA / "codex/skills/trw-exec-plan/SKILL.md",
    DATA / "agents/trw-prd-groomer.md",
    DATA / "agents/trw-lead.md",
)

READINESS_ADAPTERS = (
    DATA / "opencode/skills/trw-prd-ready/SKILL.md",
    DATA / "opencode/commands/trw-prd-ready.md",
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
    for path in (*READINESS_OWNERS, *READINESS_ADAPTERS, *AUDIT_VARIANTS, *PRD_NEW_VARIANTS, CURSOR_COMMAND):
        match = FORBIDDEN_NUMERIC_GATE.search(_read(path))
        assert match is None, f"{path} contains deprecated readiness gate: {match.group(0) if match else ''}"


def test_readiness_owners_use_the_full_risk_scaled_result() -> None:
    """Gate owners retain full validation and distinguish diagnostic tier from legacy approval."""
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
    """Cursor does not invent a READY status of its own.

    The copilot direct/plugin lead-mirror equality this also asserted is gone
    with the mirrors: ``data/copilot/agents`` and ``data/copilot/plugin/agents``
    were shipped bytes no installer read — ``generate_copilot_agents`` writes
    from the inline ``_COPILOT_AGENT_TEMPLATES`` dict.
    """
    cursor = _read(CURSOR_COMMAND)
    assert "Sets status to READY" not in cursor
    assert "lifecycle status" in cursor


@pytest.mark.parametrize(
    "paths",
    [
        pytest.param(PRD_NEW_VARIANTS[:2], id="bundled"),
        pytest.param(
            (
                *PRD_NEW_VARIANTS[:2],
                DATA.parents[3] / ".claude/skills/trw-prd-new/SKILL.md",
                DATA.parents[3] / ".agents/skills/trw-prd-new/SKILL.md",
            ),
            id="mirrors",
            marks=requires_monorepo,
        ),
    ],
)
def test_shared_prd_new_is_an_input_preserving_alias(paths: tuple[Path, ...]) -> None:
    """Static routing contract, not execution/adherence proof."""
    bodies = []
    for path in paths:
        content = _read(path)
        bodies.append(content.split("\n# ", 1)[1])
        assert "original `$ARGUMENTS`" in content
        assert "any explicit `--embedded-plan` option" in content
        assert "do not\ncreate a PRD first and substitute its ID" in content
        assert "contract is unavailable" in content
        assert "trw_prd_create(" not in content
        assert "## Phase 1: Create" not in content
    assert len(set(bodies)) == 1


def test_opencode_adapters_forward_to_installed_gate_owner() -> None:
    """Static delegation guards; installer resolution is tested in bootstrap tests."""
    skill, command = (_read(path) for path in READINESS_ADAPTERS)
    assert "trw-prd-ready-contract.md" in skill
    for phase in ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        assert f"{phase}-contract.md" in skill
    assert "not authorize author self-review" in skill
    assert "stop and report the missing installed path" in skill
    assert ".opencode/skills/trw-prd-ready/SKILL.md" in command
    for content in (skill, command):
        assert "original `$ARGUMENTS`" in content
        assert "--embedded-plan" in content
        assert "## Phase" not in content
