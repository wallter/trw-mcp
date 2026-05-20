"""Tests for Gemini agent generation."""

from __future__ import annotations

import pytest

from trw_mcp.bootstrap._gemini import _GEMINI_AGENTS_DIR, generate_gemini_agents
from ._gemini_test_support import fake_git_repo  # noqa: F401


@pytest.mark.unit
class TestGeminiAgents:
    """Test generate_gemini_agents."""

    def test_agents_dir_created(self, fake_git_repo) -> None:
        result = generate_gemini_agents(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _GEMINI_AGENTS_DIR).is_dir()

    def test_agents_files_created(self, fake_git_repo) -> None:
        result = generate_gemini_agents(fake_git_repo)
        assert not result["errors"]
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        agent_files = list(agents_dir.glob("trw-*.md"))
        assert len(agent_files) == 4

    def test_expected_agents_exist(self, fake_git_repo) -> None:
        """All four TRW agents must be generated."""
        generate_gemini_agents(fake_git_repo)
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        assert (agents_dir / "trw-explorer.md").exists()
        assert (agents_dir / "trw-implementer.md").exists()
        assert (agents_dir / "trw-reviewer.md").exists()
        assert (agents_dir / "trw-lead.md").exists()

    def test_agent_yaml_frontmatter(self, fake_git_repo) -> None:
        """Verify YAML frontmatter with name, description, tools."""
        generate_gemini_agents(fake_git_repo)
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        for agent_file in agents_dir.glob("trw-*.md"):
            content = agent_file.read_text()
            assert content.startswith("---"), f"{agent_file.name} missing YAML frontmatter"
            assert "name:" in content, f"{agent_file.name} missing name field"
            assert "description:" in content, f"{agent_file.name} missing description field"
            assert "tools:" in content, f"{agent_file.name} missing tools field"

    def test_agent_tools_are_gemini_format(self, fake_git_repo) -> None:
        """Verify tools list uses Gemini names (not Claude names like 'Bash', 'Read')."""
        generate_gemini_agents(fake_git_repo)
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        claude_tool_names = {"Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebSearch", "WebFetch"}

        for agent_file in agents_dir.glob("trw-*.md"):
            content = agent_file.read_text()
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"{agent_file.name}: malformed frontmatter"
            frontmatter = parts[1]
            for claude_name in claude_tool_names:
                assert f"  - {claude_name}\n" not in frontmatter, (
                    f"{agent_file.name} has Claude-format tool name: {claude_name}"
                )

    def test_agents_reference_mcp_trw(self, fake_git_repo) -> None:
        """Agents should reference mcp_trw_ tools for TRW integration."""
        generate_gemini_agents(fake_git_repo)
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        for agent_file in agents_dir.glob("trw-*.md"):
            content = agent_file.read_text()
            assert "mcp_trw_" in content, f"{agent_file.name} missing mcp_trw_ reference"

    def test_explorer_uses_grep_search(self, fake_git_repo) -> None:
        """Explorer agent must use official 'grep_search' tool name (not search_file_content)."""
        generate_gemini_agents(fake_git_repo)
        explorer = (fake_git_repo / _GEMINI_AGENTS_DIR / "trw-explorer.md").read_text()
        assert "grep_search" in explorer
        assert "search_file_content" not in explorer

    def test_agents_no_overwrite_existing(self, fake_git_repo) -> None:
        """Existing agent files preserved without force."""
        generate_gemini_agents(fake_git_repo)

        custom_path = fake_git_repo / _GEMINI_AGENTS_DIR / "trw-explorer.md"
        custom_path.write_text("# My custom agent\n")

        result = generate_gemini_agents(fake_git_repo)
        assert not result["errors"]

        rel_path = f"{_GEMINI_AGENTS_DIR}/trw-explorer.md"
        assert rel_path in result["preserved"]
        assert custom_path.read_text() == "# My custom agent\n"

    def test_agents_force_overwrites_existing(self, fake_git_repo) -> None:
        """force=True regenerates all agents."""
        generate_gemini_agents(fake_git_repo)

        custom_path = fake_git_repo / _GEMINI_AGENTS_DIR / "trw-explorer.md"
        custom_path.write_text("# My custom agent\n")

        result = generate_gemini_agents(fake_git_repo, force=True)
        assert not result["errors"]
        assert custom_path.read_text() != "# My custom agent\n"

    def test_agents_preserves_user_agents(self, fake_git_repo) -> None:
        """User-created agents (not trw-* prefix) are never touched."""
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        agents_dir.mkdir(parents=True)
        user_agent = agents_dir / "my-custom-agent.md"
        user_agent.write_text("# Custom agent\n")

        generate_gemini_agents(fake_git_repo)

        assert user_agent.read_text() == "# Custom agent\n"

    def test_agents_created_count(self, fake_git_repo) -> None:
        from trw_mcp.bootstrap._gemini import _GEMINI_AGENT_TEMPLATES

        result = generate_gemini_agents(fake_git_repo)
        assert len(result["created"]) == len(_GEMINI_AGENT_TEMPLATES)
