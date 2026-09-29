"""Copilot agents and skills tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import (
    _COPILOT_SKILLS_DIR,
    install_copilot_skills,
)

from ._copilot_test_support import fake_git_repo  # noqa: F401


@pytest.mark.unit
class TestCopilotAgents:
    """Copilot agent generation, after PRD-CORE-252-FR04.

    The old ``TestCopilotAgents`` exercised ``generate_copilot_agents`` and the
    ``_COPILOT_AGENT_TEMPLATES`` dictionary, both retired: four hand-written
    stubs became the whole bundled specialist set, materialized for Copilot.
    Two of its assertions were real contract and are kept here on the new path —
    no Claude-format host tool name may reach a Copilot frontmatter, and the
    ``trw`` MCP server grant must be present. Its other assertions
    (force-overwrite, created-count against the template dict, refresh-when-
    stale) are covered generically in
    ``tests/test_install_agents_destinations.py``.
    """

    #: Host tool names from the bundle's authoring dialect. None may survive
    #: translation: Copilot names its own tools, and TRW has no evidenced
    #: mapping, so the grant is dropped rather than mistranslated.
    _CLAUDE_TOOL_NAMES = ("Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebSearch", "WebFetch", "Task")

    def _install(self, repo: Path) -> dict[str, list[str]]:
        from trw_mcp.bootstrap._init_project_skills import _install_agents

        result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
        _install_agents(repo, force=False, result=result, clients=["copilot"])
        return result


@pytest.mark.unit
class TestCopilotSkills:
    """Test install_copilot_skills."""

    def test_skills_installed(self, fake_git_repo: Path) -> None:
        result = install_copilot_skills(fake_git_repo)
        assert not result["errors"]
        skills_dir = fake_git_repo / _COPILOT_SKILLS_DIR
        assert skills_dir.is_dir()
        skill_dirs = [directory for directory in skills_dir.iterdir() if directory.is_dir()]
        assert len(skill_dirs) >= 1

    def test_skill_has_skill_md(self, fake_git_repo: Path) -> None:
        install_copilot_skills(fake_git_repo)
        skills_dir = fake_git_repo / _COPILOT_SKILLS_DIR
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                assert (skill_dir / "SKILL.md").is_file(), f"Skill {skill_dir.name} missing SKILL.md"

    def test_skills_created_list(self, fake_git_repo: Path) -> None:
        result = install_copilot_skills(fake_git_repo)
        assert len(result["created"]) >= 1
        for path in result["created"]:
            assert path.startswith(_COPILOT_SKILLS_DIR)

    def test_skills_rerun_reclassifies_as_update_not_create(self, fake_git_repo: Path) -> None:
        """Running twice — the second run reports updates, not creations.

        Classification only. This says nothing about whether user content
        survives; that is ``test_skills_preserve_user_edited_file`` below. The
        two used to be conflated under the name ``test_skills_no_overwrite_existing``,
        which asserted ``updated >= 1`` — i.e. it asserted that re-writes DO
        happen — while its name promised the opposite.
        """
        result1 = install_copilot_skills(fake_git_repo)
        assert not result1["errors"]
        assert len(result1["created"]) >= 1

        result2 = install_copilot_skills(fake_git_repo)
        assert not result2["errors"]
        assert len(result2["updated"]) >= 1
        assert len(result2["created"]) == 0

    def test_skills_preserve_user_edited_file(self, fake_git_repo: Path) -> None:
        """A hand-edited installed skill file survives a re-run (CONSTITUTION HB-2)."""
        install_copilot_skills(fake_git_repo)
        edited = fake_git_repo / _COPILOT_SKILLS_DIR / "trw-deliver" / "SKILL.md"
        assert edited.is_file()
        edited.write_text("# my hand-edited deliver skill\n", encoding="utf-8")

        result = install_copilot_skills(fake_git_repo)

        assert not result["errors"]
        assert edited.read_text(encoding="utf-8") == "# my hand-edited deliver skill\n"
        assert f"{_COPILOT_SKILLS_DIR}/trw-deliver/SKILL.md" in result["preserved"]

    def test_skills_refresh_untouched_file_when_bundle_changes(self, fake_git_repo: Path) -> None:
        """The other direction: a stale-but-untouched file still gets the new content.

        Guards against a "fix" that merely freezes every file — which passes the
        preservation test above while silently breaking updates forever.
        """
        install_copilot_skills(fake_git_repo)
        rel = f"{_COPILOT_SKILLS_DIR}/trw-deliver/SKILL.md"
        dest = fake_git_repo / rel

        # Simulate a project installed from an OLDER bundle: on-disk content is
        # what TRW itself last wrote (recorded in the manifest), not the current
        # bundle. The user has touched nothing.
        stale = "# an older bundled trw-deliver\n"
        dest.write_text(stale, encoding="utf-8")
        manifest_hashes = {rel: hashlib.sha256(stale.encode("utf-8")).hexdigest()}

        result = install_copilot_skills(fake_git_repo, manifest_hashes=manifest_hashes)

        assert not result["errors"]
        assert dest.read_text(encoding="utf-8") != stale
        assert rel in result["updated"]

    def test_skills_force_overwrites_user_edit(self, fake_git_repo: Path) -> None:
        """``force=True`` is the documented escape hatch and discards local edits."""
        install_copilot_skills(fake_git_repo)
        edited = fake_git_repo / _COPILOT_SKILLS_DIR / "trw-deliver" / "SKILL.md"
        edited.write_text("# my hand-edited deliver skill\n", encoding="utf-8")

        result = install_copilot_skills(fake_git_repo, force=True)

        assert not result["errors"]
        assert edited.read_text(encoding="utf-8") != "# my hand-edited deliver skill\n"
        assert len(result["updated"]) >= 1
