"""Regression contracts for slim, portable PRD-ready orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "trw-mcp" / "src" / "trw_mcp" / "data"

if not (ROOT / "scripts").is_dir():
    pytest.skip("monorepo-only PRD skill projection invariant", allow_module_level=True)

DELEGATED_ROOTS = (
    DATA / "skills",
    DATA / "codex" / "skills",
    ROOT / ".claude" / "skills",
    ROOT / ".agents" / "skills",
)


def test_ready_delegates_groom_and_review_without_copying_their_workflows() -> None:
    for skill_root in DELEGATED_ROOTS:
        ready = (skill_root / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
        groom_phase = ready.split("### Phase 2: GROOM", 1)[1].split("### Phase 3: REVIEW", 1)[0]
        review_phase = ready.split("### Phase 3: REVIEW", 1)[1].split("### Phase 4: EXEC PLAN", 1)[0]

        assert (skill_root / "trw-prd-groom" / "SKILL.md").is_file()
        assert (skill_root / "trw-prd-review" / "SKILL.md").is_file()
        assert "packaged internal `trw-prd-groom` contract" in groom_phase
        assert "packaged internal `trw-prd-review` contract" in review_phase
        assert "call full `trw_prd_validate(prd_path)`" in groom_phase
        assert "reviewer's specific findings as refinement context" in groom_phase
        groom = (skill_root / "trw-prd-groom" / "SKILL.md").read_text(encoding="utf-8")
        assert "supplies review findings as refinement context" in groom
        assert "address the supplied refinement findings" in groom
        assert "Use EARS patterns only where" in groom
        assert "ALWAYS use EARS" not in ready + groom
        assert "same-context review" in review_phase
        assert len(groom_phase.split()) < 150
        assert len(review_phase.split()) < 190
        assert "## Rationalization Watchlist" not in ready


def test_ready_keeps_orchestration_owned_review_routing() -> None:
    for skill_root in DELEGATED_ROOTS:
        ready = (skill_root / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "validation_partial: false",
            "quality_tier: approved",
            "If < 2 refinements done",
            "reviewer's specific findings",
            "**BLOCK** | STOP immediately",
        ):
            assert phrase in ready


def test_ready_reports_optional_artifacts_and_mcp_failure_portably() -> None:
    for skill_root in DELEGATED_ROOTS:
        ready = (skill_root / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
        assert "Test Skeletons: `{path}` (include only when created)" in ready
        assert "client's supported MCP flow" in ready
        assert "[Errno 2]" not in ready
        assert "run `/mcp`" not in ready


def test_clients_without_internal_phases_retain_self_contained_workflow() -> None:
    cases = (
        (
            DATA / "opencode" / "skills" / "trw-prd-ready" / "SKILL.md",
            ("singular/testable requirements", "READY | NEEDS WORK | BLOCK"),
        ),
        (
            ROOT / ".cursor" / "skills" / "trw-prd-ready" / "SKILL.md",
            ("Use EARS patterns only when", "READY", "NEEDS WORK", "BLOCK"),
        ),
    )
    for ready_path, required_phrases in cases:
        ready = ready_path.read_text(encoding="utf-8")
        for phrase in required_phrases:
            assert phrase in ready
