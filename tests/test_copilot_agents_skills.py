"""Copilot agents and skills tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import (
    _COPILOT_AGENTS_DIR,
    _COPILOT_SKILLS_DIR,
    generate_copilot_agents,
    install_copilot_skills,
)

from ._copilot_test_support import fake_git_repo  # noqa: F401


@pytest.mark.unit
class TestCopilotAgents:
    """Test generate_copilot_agents."""

    def test_agents_dir_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_agents(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_AGENTS_DIR).is_dir()

    def test_agents_files_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_agents(fake_git_repo)
        assert not result["errors"]
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        agent_files = list(agents_dir.glob("*.agent.md"))
        assert len(agent_files) >= 3

    def test_agent_yaml_frontmatter(self, fake_git_repo: Path) -> None:
        """Verify YAML frontmatter with name, description, tools."""
        generate_copilot_agents(fake_git_repo)
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        for agent_file in agents_dir.glob("*.agent.md"):
            content = agent_file.read_text()
            assert content.startswith("---"), f"{agent_file.name} missing YAML frontmatter"
            assert "name:" in content, f"{agent_file.name} missing name field"
            assert "description:" in content, f"{agent_file.name} missing description field"
            assert "tools:" in content, f"{agent_file.name} missing tools field"

    def test_agent_tools_are_copilot_format(self, fake_git_repo: Path) -> None:
        """Verify tools list uses copilot names (not Claude names like 'Bash', 'Read')."""
        generate_copilot_agents(fake_git_repo)
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        claude_tool_names = {
            "Bash",
            "Read",
            "Edit",
            "Write",
            "Glob",
            "Grep",
            "WebSearch",
            "WebFetch",
            "Task",
        }

        for agent_file in agents_dir.glob("*.agent.md"):
            content = agent_file.read_text()
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"{agent_file.name}: malformed frontmatter"
            frontmatter = parts[1]
            for claude_name in claude_tool_names:
                assert f"  - {claude_name}\n" not in frontmatter, (
                    f"{agent_file.name} has Claude-format tool name: {claude_name}"
                )

    def test_agents_no_overwrite_existing(self, fake_git_repo: Path) -> None:
        """Existing user agents preserved without force."""
        generate_copilot_agents(fake_git_repo)

        from trw_mcp.bootstrap._copilot import _COPILOT_AGENT_TEMPLATES

        first_agent_name = next(iter(_COPILOT_AGENT_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_AGENTS_DIR / first_agent_name
        custom_path.write_text("# My custom agent\n")

        result = generate_copilot_agents(fake_git_repo)
        assert not result["errors"]

        rel_path = f"{_COPILOT_AGENTS_DIR}/{first_agent_name}"
        assert rel_path in result["preserved"]
        assert custom_path.read_text() == "# My custom agent\n"

    def test_agents_refresh_untouched_file_when_bundle_changes(self, fake_git_repo: Path) -> None:
        """The other direction: a stale-but-untouched TRW agent still refreshes.

        Before the content-aware guard, ``existed and not force`` preserved
        EVERY existing agent, so an installed project was frozen at whatever the
        first install wrote and no upstream agent fix ever reached it.
        """
        from trw_mcp.bootstrap._copilot import _COPILOT_AGENT_TEMPLATES

        generate_copilot_agents(fake_git_repo)
        first_agent_name = next(iter(_COPILOT_AGENT_TEMPLATES))
        rel_path = f"{_COPILOT_AGENTS_DIR}/{first_agent_name}"
        dest = fake_git_repo / rel_path

        stale = "# an older bundled agent\n"
        dest.write_text(stale, encoding="utf-8")
        manifest_hashes = {rel_path: hashlib.sha256(stale.encode("utf-8")).hexdigest()}

        result = generate_copilot_agents(fake_git_repo, manifest_hashes=manifest_hashes)

        assert not result["errors"]
        assert dest.read_text(encoding="utf-8") == _COPILOT_AGENT_TEMPLATES[first_agent_name]
        assert rel_path in result["updated"]

    def test_agents_force_overwrites_existing(self, fake_git_repo: Path) -> None:
        """force=True regenerates all agents."""
        generate_copilot_agents(fake_git_repo)

        from trw_mcp.bootstrap._copilot import _COPILOT_AGENT_TEMPLATES

        first_agent_name = next(iter(_COPILOT_AGENT_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_AGENTS_DIR / first_agent_name
        custom_path.write_text("# My custom agent\n")

        result = generate_copilot_agents(fake_git_repo, force=True)
        assert not result["errors"]
        assert custom_path.read_text() != "# My custom agent\n"

    def test_agents_created_count(self, fake_git_repo: Path) -> None:
        from trw_mcp.bootstrap._copilot import _COPILOT_AGENT_TEMPLATES

        result = generate_copilot_agents(fake_git_repo)
        assert len(result["created"]) == len(_COPILOT_AGENT_TEMPLATES)

    def test_agent_mcp_servers(self, fake_git_repo: Path) -> None:
        """Verify agents reference trw MCP server."""
        generate_copilot_agents(fake_git_repo)
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        for agent_file in agents_dir.glob("*.agent.md"):
            content = agent_file.read_text()
            assert "mcp-servers:" in content, f"{agent_file.name} missing mcp-servers"
            assert "trw" in content, f"{agent_file.name} missing trw server reference"


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
