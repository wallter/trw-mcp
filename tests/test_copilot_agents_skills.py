"""Copilot agents and skills tests."""

from __future__ import annotations

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

    def test_skills_no_overwrite_existing(self, fake_git_repo: Path) -> None:
        """Running twice — second run updates rather than re-creates."""
        result1 = install_copilot_skills(fake_git_repo)
        assert not result1["errors"]
        created_count = len(result1["created"])
        assert created_count >= 1

        result2 = install_copilot_skills(fake_git_repo)
        assert not result2["errors"]
        assert len(result2["updated"]) >= 1
        assert len(result2["created"]) == 0

    def test_skills_force_still_works(self, fake_git_repo: Path) -> None:
        install_copilot_skills(fake_git_repo)
        result = install_copilot_skills(fake_git_repo, force=True)
        assert not result["errors"]
        assert len(result["created"]) >= 1
