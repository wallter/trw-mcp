"""Tests for bundled and root skill-definition compatibility contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, requires_monorepo
from tests._test_bundle_asset_support import _PKG_DATA
from trw_mcp.models.skill_manifest import validate_skill_markdown


class TestSkillDefinitions:
    """Tests for flywheel skill contract alignment across root and bundled copies."""

    @pytest.fixture()
    def skills_dir(self) -> Path:
        """Return path to bundled skill definitions."""
        skills = _PKG_DATA / "skills"
        assert skills.is_dir(), f"required bundled skill tree missing: {skills}"
        return skills

    @pytest.fixture()
    def root_skills_dir(self) -> Path:
        """Return path to monorepo root skill definitions when available."""
        if MONOREPO_ROOT is None:
            pytest.skip("root skill parity requires the monorepo checkout")
        skills_dir = MONOREPO_ROOT / ".claude" / "skills"
        assert skills_dir.is_dir(), f"required monorepo skill tree missing: {skills_dir}"
        return skills_dir

    def test_exec_plan_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled exec-plan skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-exec-plan" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-exec-plan" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_self_review_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled self-review skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-self-review" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-self-review" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_audit_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled audit skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-audit" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-audit" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_sprint_finish_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled sprint-finish skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-sprint-finish" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-sprint-finish" / "SKILL.md"
        ).read_text(encoding="utf-8")

    def test_reflect_skill_matches_root_source(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Bundled trw-reflect skill stays byte-for-byte aligned with root source."""
        assert (skills_dir / "trw-reflect" / "SKILL.md").read_text(encoding="utf-8") == (
            root_skills_dir / "trw-reflect" / "SKILL.md"
        ).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "include_root",
        [pytest.param(False, id="bundled"), pytest.param(True, id="mirrors", marks=requires_monorepo)],
    )
    def test_skill_variants_carry_their_evidence_contract(
        self, skills_dir: Path, include_root: bool, request: pytest.FixtureRequest
    ) -> None:
        """Every skill variant states the evidence contract its phase owns.

        Note: ``trw_preflight_log`` was removed from the MCP tool surface, so the
        audit variants assert prior-learning recall and the explicit refusal to
        audit self-reports — not the retired preflight events, which nothing writes.
        """
        variant_paths = {
            "bundled_exec_plan": skills_dir / "trw-exec-plan" / "SKILL.md",
            "codex_exec_plan": _PKG_DATA / "codex" / "skills" / "trw-exec-plan" / "SKILL.md",
            "bundled_self_review": skills_dir / "trw-self-review" / "SKILL.md",
            "bundled_audit": skills_dir / "trw-audit" / "SKILL.md",
            "codex_audit": _PKG_DATA / "codex" / "skills" / "trw-audit" / "SKILL.md",
            "copilot_audit": _PKG_DATA / "copilot" / "skills" / "trw-audit" / "SKILL.md",
            "bundled_sprint_finish": skills_dir / "trw-sprint-finish" / "SKILL.md",
            "codex_sprint_finish": _PKG_DATA / "codex" / "skills" / "trw-sprint-finish" / "SKILL.md",
        }
        if include_root:
            root_skills_dir = request.getfixturevalue("root_skills_dir")
            variant_paths.update(
                {
                    "root_exec_plan": root_skills_dir / "trw-exec-plan/SKILL.md",
                    "root_self_review": root_skills_dir / "trw-self-review/SKILL.md",
                    "root_audit": root_skills_dir / "trw-audit/SKILL.md",
                    "root_sprint_finish": root_skills_dir / "trw-sprint-finish/SKILL.md",
                }
            )
        required_snippets = {
            "exec_plan": ["Pre-Implementation Checklist (PRD-QUAL-056-FR03)"],
            "self_review": ["never substitutes for the required independent/substantive review"],
            # The preflight/self-review contract is retired (PRD-QUAL-056 FR03/FR05
            # correction, 2026-07-24): its producer tool was removed in PRD-FIX-076,
            # so the events are never written and the check recorded "missing" on
            # every audit. The denylist that forbids reintroducing it moved to
            # test_audit_protocol_contracts.py under PRD-QUAL-128-FR08, where it
            # scans 18 surfaces (11 bundled agents + 7 skill projections) instead
            # of the 4 skill variants this module used to cover.
            "audit": [
                "prior_learning_verification:",
                "Do **not** audit the implementer's self-report",
            ],
            "sprint_finish": [
                "Call `trw_deliver()` as the last TRW action",
                "delivery result and residual risks",
            ],
        }

        for variant_name, path in variant_paths.items():
            content = path.read_text(encoding="utf-8")
            skill_kind = (
                "exec_plan"
                if "exec_plan" in variant_name
                else "self_review"
                if "self_review" in variant_name
                else "sprint_finish"
                if "sprint_finish" in variant_name
                else "audit"
            )
            for snippet in required_snippets[skill_kind]:
                assert snippet in content, f"{variant_name} missing snippet: {snippet}"

    def test_all_bundled_skills_validate_in_compatibility_mode(self, skills_dir: Path) -> None:
        """PRD-CORE-170 FR-6: every bundled SKILL.md is CI-validated in compat mode."""
        skill_paths = sorted(skills_dir.glob("*/SKILL.md"))

        assert skill_paths
        for skill_path in skill_paths:
            result = validate_skill_markdown(
                skill_path.read_text(encoding="utf-8"),
                path=skill_path,
                mode="compat",
            )
            assert result.ok, f"{skill_path} failed compat validation: {result.errors}"
