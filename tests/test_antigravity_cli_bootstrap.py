"""Tests for Antigravity CLI bootstrap configuration and installers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._antigravity_cli import (
    _ANTIGRAVITY_AGENTS_DIR,
    _ANTIGRAVITY_MD_PATH,
    _ANTIGRAVITY_SETTINGS_PATH,
    generate_antigravity_agents,
    generate_antigravity_instructions,
    generate_antigravity_mcp_config,
)
from trw_mcp.bootstrap._utils import detect_ide

from ._bootstrap_test_support import fake_git_repo  # noqa: F401


@pytest.mark.unit
class TestAntigravityCliAgents:
    """Test generate_antigravity_agents."""

    def test_agents_dir_created(self, fake_git_repo: Path) -> None:
        result = generate_antigravity_agents(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _ANTIGRAVITY_AGENTS_DIR).is_dir()

    def test_agents_files_created(self, fake_git_repo: Path) -> None:
        result = generate_antigravity_agents(fake_git_repo)
        assert not result["errors"]
        agents_dir = fake_git_repo / _ANTIGRAVITY_AGENTS_DIR
        agent_files = list(agents_dir.glob("trw-*.md"))
        assert len(agent_files) == 4

    def test_expected_agents_exist(self, fake_git_repo: Path) -> None:
        """All four TRW agents must be generated."""
        generate_antigravity_agents(fake_git_repo)
        agents_dir = fake_git_repo / _ANTIGRAVITY_AGENTS_DIR
        assert (agents_dir / "trw-explorer.md").exists()
        assert (agents_dir / "trw-implementer.md").exists()
        assert (agents_dir / "trw-reviewer.md").exists()
        assert (agents_dir / "trw-lead.md").exists()

    def test_agent_yaml_frontmatter(self, fake_git_repo: Path) -> None:
        """Verify YAML frontmatter with name, description, tools."""
        generate_antigravity_agents(fake_git_repo)
        agents_dir = fake_git_repo / _ANTIGRAVITY_AGENTS_DIR
        for agent_file in agents_dir.glob("trw-*.md"):
            content = agent_file.read_text(encoding="utf-8")
            assert content.startswith("---"), f"{agent_file.name} missing YAML frontmatter"
            assert "name:" in content, f"{agent_file.name} missing name field"
            assert "description:" in content, f"{agent_file.name} missing description field"
            assert "tools:" in content, f"{agent_file.name} missing tools field"

    def test_agent_tools_reference_mcp_trw(self, fake_git_repo: Path) -> None:
        """Agents should reference mcp_trw_ tools for TRW integration."""
        generate_antigravity_agents(fake_git_repo)
        agents_dir = fake_git_repo / _ANTIGRAVITY_AGENTS_DIR
        for agent_file in agents_dir.glob("trw-*.md"):
            content = agent_file.read_text(encoding="utf-8")
            assert "mcp_trw_" in content, f"{agent_file.name} missing mcp_trw_ reference"

    def test_explorer_uses_grep_search(self, fake_git_repo: Path) -> None:
        """Explorer agent must use official 'grep_search' tool name."""
        generate_antigravity_agents(fake_git_repo)
        explorer = (fake_git_repo / _ANTIGRAVITY_AGENTS_DIR / "trw-explorer.md").read_text(encoding="utf-8")
        assert "grep_search" in explorer

    def test_agents_no_overwrite_existing(self, fake_git_repo: Path) -> None:
        """Existing agent files preserved without force."""
        generate_antigravity_agents(fake_git_repo)

        custom_path = fake_git_repo / _ANTIGRAVITY_AGENTS_DIR / "trw-explorer.md"
        custom_path.write_text("# My custom agent\n", encoding="utf-8")

        result = generate_antigravity_agents(fake_git_repo)
        assert not result["errors"]

        rel_path = f"{_ANTIGRAVITY_AGENTS_DIR}/trw-explorer.md"
        assert rel_path in result["preserved"]
        assert custom_path.read_text(encoding="utf-8") == "# My custom agent\n"

    def test_agents_force_overwrites_existing(self, fake_git_repo: Path) -> None:
        """force=True regenerates all agents."""
        generate_antigravity_agents(fake_git_repo)

        custom_path = fake_git_repo / _ANTIGRAVITY_AGENTS_DIR / "trw-explorer.md"
        custom_path.write_text("# My custom agent\n", encoding="utf-8")

        result = generate_antigravity_agents(fake_git_repo, force=True)
        assert not result["errors"]
        assert custom_path.read_text(encoding="utf-8") != "# My custom agent\n"


@pytest.mark.unit
class TestAntigravityCliMcpConfigHardening:
    """Hardened settings.json deep-merge and recovery."""

    def test_writes_fresh_when_settings_missing(self, tmp_path: Path) -> None:
        result = generate_antigravity_mcp_config(tmp_path)
        settings = tmp_path / _ANTIGRAVITY_SETTINGS_PATH
        assert settings.is_file()
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        assert data["mcpServers"]["trw"]["trust"] is True
        assert result["errors"] == []

    def test_preserves_unrelated_user_keys(self, tmp_path: Path) -> None:
        settings = tmp_path / _ANTIGRAVITY_SETTINGS_PATH
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {
                        "other-server": {"command": "/usr/bin/other"},
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        generate_antigravity_mcp_config(tmp_path)

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["theme"] == "dark"
        assert "other-server" in data["mcpServers"]
        assert "trw" in data["mcpServers"]

    def test_idempotent_second_run_is_preserved(self, tmp_path: Path) -> None:
        first = generate_antigravity_mcp_config(tmp_path)
        second = generate_antigravity_mcp_config(tmp_path)

        assert any(_ANTIGRAVITY_SETTINGS_PATH in p for p in first["created"])
        assert any(_ANTIGRAVITY_SETTINGS_PATH in p for p in second.get("preserved", []))

    def test_recovers_from_invalid_json_with_backup(self, tmp_path: Path) -> None:
        settings = tmp_path / _ANTIGRAVITY_SETTINGS_PATH
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text("this is not { valid json", encoding="utf-8")

        result = generate_antigravity_mcp_config(tmp_path)
        assert result["errors"] == []
        assert any("was not valid JSON" in w for w in result.get("warnings", []))

        backup = settings.with_suffix(settings.suffix + ".bak")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "this is not { valid json"

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]


@pytest.mark.unit
class TestAntigravityCliInstructions:
    """Test generate_antigravity_instructions."""

    def test_creates_antigravity_md(self, fake_git_repo: Path) -> None:
        result = generate_antigravity_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _ANTIGRAVITY_MD_PATH).is_file()

        content = (fake_git_repo / _ANTIGRAVITY_MD_PATH).read_text(encoding="utf-8")
        assert "TRW Framework Integration" in content
        assert "<!-- trw:antigravity:start -->" in content
        assert "<!-- trw:antigravity:end -->" in content
        assert "@trw-explorer" in content

    def test_instructions_smart_merge(self, fake_git_repo: Path) -> None:
        generate_antigravity_instructions(fake_git_repo)

        custom_instructions = (
            "# My Custom Antigravity Rules\n\n"
            "<!-- trw:antigravity:start -->\n"
            "OLD trw content\n"
            "<!-- trw:antigravity:end -->\n\n"
            "Some user postamble."
        )
        (fake_git_repo / _ANTIGRAVITY_MD_PATH).write_text(custom_instructions, encoding="utf-8")

        generate_antigravity_instructions(fake_git_repo)

        content = (fake_git_repo / _ANTIGRAVITY_MD_PATH).read_text(encoding="utf-8")
        assert content.startswith("# My Custom Antigravity Rules\n")
        assert content.endswith("Some user postamble.")
        assert "OLD trw content" not in content
        assert "TRW Framework Integration" in content


@pytest.mark.unit
class TestAntigravityCliDiscovery:
    """Test detect_ide for antigravity-cli."""

    def test_detects_by_config_dir(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".antigravitycli"
        config_dir.mkdir(parents=True, exist_ok=True)
        detected = detect_ide(tmp_path)
        assert "antigravity-cli" in detected

    def test_detects_by_instructions_file(self, tmp_path: Path) -> None:
        instr_file = tmp_path / _ANTIGRAVITY_MD_PATH
        instr_file.write_text("Hello", encoding="utf-8")
        detected = detect_ide(tmp_path)
        assert "antigravity-cli" in detected
