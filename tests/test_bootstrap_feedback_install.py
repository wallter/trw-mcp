"""Tests for the /trw-feedback bundled-skill bootstrap install (PRD-INFRA-132 FR05).

Asserts that ``_install_skills`` copies the ``trw-feedback`` SKILL.md from
the bundled data dir to ``.claude/skills/trw-feedback/SKILL.md`` and that the
copied file passes ``validate_skill_markdown``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._init_project_skills import _install_skills, _validate_skill
from trw_mcp.models.skill_manifest import validate_skill_markdown


@pytest.fixture()
def empty_target(tmp_path: Path) -> Path:
    """Return a target dir with the .claude/skills subtree pre-created."""
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    return tmp_path


def _empty_result() -> dict[str, list[str]]:
    return {"created": [], "skipped": [], "errors": []}


class TestFeedbackSkillInstall:
    """FR05: the bundled /trw-feedback skill is installed by ``_install_skills``."""

    def test_skill_md_is_copied(self, empty_target: Path) -> None:
        result = _empty_result()
        _install_skills(empty_target, force=False, result=result)

        dest = empty_target / ".claude" / "skills" / "trw-feedback" / "SKILL.md"
        assert dest.exists(), "trw-feedback/SKILL.md must be installed by _install_skills"
        # Sanity-check: the file is non-empty and starts with a YAML frontmatter
        # block (mirrors how every bundled skill is shaped).
        content = dest.read_text(encoding="utf-8")
        assert content.startswith("---"), "SKILL.md must lead with a YAML frontmatter block"

    def test_installed_skill_passes_frontmatter_validation(self, empty_target: Path) -> None:
        """The copied SKILL.md must satisfy the public skill-manifest contract."""
        result = _empty_result()
        _install_skills(empty_target, force=False, result=result)

        dest = empty_target / ".claude" / "skills" / "trw-feedback" / "SKILL.md"
        validation = validate_skill_markdown(dest.read_text(encoding="utf-8"), path=dest, mode="compat")
        assert validation.ok, f"installed SKILL.md failed validation: {[e.reason for e in validation.errors]}"
        assert validation.manifest is not None
        assert validation.manifest.name == "trw-feedback"
        # Description is required by the bundled-skill schema.
        assert validation.manifest.description.strip(), "description must be non-empty"

    def test_bundled_skill_dir_is_valid_at_source(self) -> None:
        """The bundle itself must pass ``_validate_skill`` — guards against
        a malformed SKILL.md slipping through and being silently skipped."""
        from trw_mcp.bootstrap._init_project import _DATA_DIR

        skill_dir = _DATA_DIR / "skills" / "trw-feedback"
        assert skill_dir.is_dir(), "bundled trw-feedback skill directory must exist"
        ok, reason = _validate_skill(skill_dir)
        assert ok, f"bundled trw-feedback skill failed validation: {reason}"

    def test_idempotent_second_run_skips(self, empty_target: Path) -> None:
        """Running the installer twice without ``force`` must not re-create."""
        first = _empty_result()
        _install_skills(empty_target, force=False, result=first)

        second = _empty_result()
        _install_skills(empty_target, force=False, result=second)

        feedback_creates = [p for p in second["created"] if "trw-feedback" in p]
        assert not feedback_creates, "second install must skip existing trw-feedback files"
