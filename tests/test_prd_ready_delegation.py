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


def test_ready_delegates_groom_and_review_without_copying_their_workflows(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._codex import install_codex_skills

    assert not install_codex_skills(tmp_path)["errors"]
    for skill_root in DELEGATED_ROOTS:
        ready = (skill_root / "trw-prd-ready" / "SKILL.md").read_text(encoding="utf-8")
        groom_phase = ready.split("### Phase 2: GROOM", 1)[1].split("### Phase 3: REVIEW", 1)[0]
        review_phase = ready.split("### Phase 3: REVIEW", 1)[1].split("### Phase 4: EXEC PLAN", 1)[0]

        codex = skill_root in (DATA / "codex" / "skills", ROOT / ".agents" / "skills")
        for name, phase in (("trw-prd-groom", groom_phase), ("trw-prd-review", review_phase)):
            if codex:
                assert f"{name}-contract.md" in phase
                resource = tmp_path / ".agents/skills/trw-prd-ready" / f"{name}-contract.md"
                assert resource.read_bytes() == (skill_root / name / "SKILL.md").read_bytes()
            else:
                assert f"packaged internal `{name}` contract" in phase
                assert (skill_root / name / "SKILL.md").is_file()
        assert "call full `trw_prd_validate(prd_path)`" in groom_phase
        assert "reviewer's specific findings as refinement context" in groom_phase
        groom = (skill_root / "trw-prd-groom" / "SKILL.md").read_text(encoding="utf-8")
        assert "supplies review findings as refinement context" in groom
        assert "address the supplied refinement findings" in groom
        assert "Use EARS patterns only where" in groom
        assert "ALWAYS use EARS" not in ready + groom
        assert "author-independent helper/human" in review_phase
        assert "no inline author self-review fallback" in review_phase
        assert "plan presence, uniqueness, task/requirement coverage and proof" in review_phase
        # Full procedures remain owned by their phase resources. Word counts
        # cannot distinguish duplicated work from required admission safeguards.
        assert "## Workflow" in groom and "## Workflow" not in groom_phase
        assert "## High-Signal Review Focus" not in review_phase
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


def test_clients_without_internal_phases_retain_self_contained_workflow(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._opencode import install_opencode_skills

    assert not install_opencode_skills(tmp_path)["errors"]
    installed = tmp_path / ".opencode/skills/trw-prd-ready"
    adapter = (installed / "SKILL.md").read_text()
    assert "Read the selected phase contract" in adapter
    assert "does not authorize author self-review" in " ".join(adapter.split())
    for name in ("trw-prd-ready", "trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        resource = installed / f"{name}-contract.md"
        assert resource.name in adapter
        assert resource.read_bytes() == (DATA / "skills" / name / "SKILL.md").read_bytes()
    # Cursor remains a separate self-contained projection, not silently excluded.
    cursor = (ROOT / ".cursor/skills/trw-prd-ready/SKILL.md").read_text()
    for phrase in ("Use EARS patterns only when", "READY", "NEEDS WORK", "BLOCK"):
        assert phrase in cursor
