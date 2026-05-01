"""Tests for bundled and root skill-definition compatibility contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_bundle_asset_support import _MONOREPO_CLAUDE, _PKG_DATA, _resolve_data_path


class TestSkillDefinitions:
    """Tests for flywheel skill contract alignment across root and bundled copies."""

    @pytest.fixture()
    def skills_dir(self) -> Path:
        """Return path to bundled skill definitions."""
        return _resolve_data_path("skills", "skills")

    @pytest.fixture()
    def root_skills_dir(self) -> Path:
        """Return path to monorepo root skill definitions when available."""
        skills_dir = _MONOREPO_CLAUDE / "skills"
        if not skills_dir.exists():
            pytest.skip("root .claude/skills not available in this environment")
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

    def test_skill_variants_include_preflight_logging_contract(self, skills_dir: Path, root_skills_dir: Path) -> None:
        """Root and bundled skill variants retain the pre-implementation checklist/self-review contract.

        Note: trw_preflight_log was removed from the MCP tool surface (14-tool reduction).
        Tests verify the checklist concept and self-review structure remain, not the removed tool call.
        """
        variant_paths = {
            "root_exec_plan": root_skills_dir / "trw-exec-plan" / "SKILL.md",
            "bundled_exec_plan": skills_dir / "trw-exec-plan" / "SKILL.md",
            "codex_exec_plan": _PKG_DATA / "codex" / "skills" / "trw-exec-plan" / "SKILL.md",
            "root_self_review": root_skills_dir / "trw-self-review" / "SKILL.md",
            "bundled_self_review": skills_dir / "trw-self-review" / "SKILL.md",
            "root_audit": root_skills_dir / "trw-audit" / "SKILL.md",
            "bundled_audit": skills_dir / "trw-audit" / "SKILL.md",
            "codex_audit": _PKG_DATA / "codex" / "skills" / "trw-audit" / "SKILL.md",
            "copilot_audit": _PKG_DATA / "copilot" / "skills" / "trw-audit" / "SKILL.md",
            "root_sprint_finish": root_skills_dir / "trw-sprint-finish" / "SKILL.md",
            "bundled_sprint_finish": skills_dir / "trw-sprint-finish" / "SKILL.md",
            "codex_sprint_finish": _PKG_DATA / "codex" / "skills" / "trw-sprint-finish" / "SKILL.md",
        }
        required_snippets = {
            "exec_plan": ["Pre-Implementation Checklist (PRD-QUAL-056-FR03)"],
            "self_review": ["Pre-Audit Self-Review Skill (PRD-QUAL-056-FR05)"],
            "audit": [
                "Check `events.jsonl` for `pre_implementation_checklist_complete` and `pre_audit_self_review`",
                "preflight_verification:",
                "self_review_alignment: matches|underreported|missing",
                "prior_learning_verification:",
            ],
            "sprint_finish": ["Delivery ceremony", "Learnings promoted"],
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
