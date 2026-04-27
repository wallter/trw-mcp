"""Tests for Gemini CLI integration.

Covers profile registration, detection, instructions generation (with smart merge),
MCP config deep-merge, agents generation, and init/update wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._gemini import (
    _GEMINI_AGENTS_DIR,
    _GEMINI_MD_PATH,
    _GEMINI_SETTINGS_PATH,
    _GEMINI_TRW_END_MARKER,
    _GEMINI_TRW_START_MARKER,
    _gemini_instructions_content,
    _smart_merge_instructions,
    generate_gemini_agents,
    generate_gemini_instructions,
    generate_gemini_mcp_config,
)
from trw_mcp.bootstrap._utils import SUPPORTED_IDES, detect_ide, resolve_ide_targets
from trw_mcp.models.config._profiles import _PROFILES, resolve_client_profile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


# =====================================================================
# 1. Profile Tests
# =====================================================================


@pytest.mark.unit
class TestGeminiProfile:
    """Verify the 'gemini' entry in the _PROFILES registry."""

    def test_gemini_profile_exists(self) -> None:
        assert "gemini" in _PROFILES

    def test_gemini_profile_is_full_mode(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.ceremony_mode == "full"

    def test_gemini_hooks_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.hooks_enabled is True

    def test_gemini_skills_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.skills_enabled is True

    def test_gemini_agents_md_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.agents_md is True

    def test_gemini_md_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.gemini_md is True

    def test_gemini_claude_md_disabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.claude_md is False

    def test_gemini_context_window(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.context_window_tokens == 1_000_000

    def test_gemini_ceremony_weights_sum_100(self) -> None:
        profile = _PROFILES["gemini"]
        w = profile.ceremony_weights
        total = w.session_start + w.deliver + w.checkpoint + w.learn + w.build_check + w.review
        assert total == 100

    def test_gemini_scoring_weights_sum_to_one(self) -> None:
        profile = _PROFILES["gemini"]
        sw = profile.scoring_weights
        total = sw.outcome + sw.plan_quality + sw.implementation + sw.ceremony + sw.knowledge
        assert abs(total - 1.0) < 0.01

    def test_gemini_instruction_path(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.write_targets.instruction_path == "GEMINI.md"

    def test_gemini_display_name(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.display_name == "Google Gemini CLI"

    def test_gemini_client_id(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.client_id == "gemini"

    def test_resolve_client_profile_gemini(self) -> None:
        profile = resolve_client_profile("gemini")
        assert profile.client_id == "gemini"
        assert profile.ceremony_mode == "full"

    def test_gemini_nudge_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.nudge_enabled is True

    def test_gemini_learning_recall_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.learning_recall_enabled is True

    def test_gemini_mcp_instructions_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.mcp_instructions_enabled is True

    def test_gemini_agent_teams_disabled(self) -> None:
        """Gemini uses native .gemini/agents/ instead of Agent Teams."""
        profile = _PROFILES["gemini"]
        assert profile.include_agent_teams is False

    def test_gemini_delegation_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.include_delegation is True

    def test_gemini_framework_ref_enabled(self) -> None:
        profile = _PROFILES["gemini"]
        assert profile.include_framework_ref is True

    def test_gemini_in_supported_ides(self) -> None:
        assert "gemini" in SUPPORTED_IDES


# =====================================================================
# 1a. Wiring Tests (required by "Adding a New Client" protocol)
# =====================================================================


@pytest.mark.unit
class TestGeminiProfileWiring:
    """Wiring tests proving profile weights produce different scoring output."""

    def test_gemini_vs_light_review_weight(self) -> None:
        """Full ceremony has non-zero review weight; light does not."""
        gemini = resolve_client_profile("gemini")
        opencode = resolve_client_profile("opencode")
        assert gemini.ceremony_weights.review > 0
        assert opencode.ceremony_weights.review == 0

    def test_gemini_vs_light_context_budget(self) -> None:
        """Gemini has far larger context budget than light profiles."""
        gemini = resolve_client_profile("gemini")
        aider = resolve_client_profile("aider")
        assert gemini.context_window_tokens > aider.context_window_tokens

    def test_gemini_vs_claude_code_context(self) -> None:
        """Gemini 1M context is larger than Claude Code 200K."""
        gemini = resolve_client_profile("gemini")
        claude = resolve_client_profile("claude-code")
        assert gemini.context_window_tokens > claude.context_window_tokens

    def test_gemini_differs_from_copilot_agent_teams(self) -> None:
        """Gemini disables Agent Teams; Copilot enables them."""
        gemini = resolve_client_profile("gemini")
        copilot = resolve_client_profile("copilot")
        assert gemini.include_agent_teams is False
        assert copilot.include_agent_teams is True


# =====================================================================
# 2. Detection Tests
# =====================================================================


@pytest.mark.unit
class TestGeminiDetection:
    """Verify IDE detection recognizes Gemini CLI artifacts."""

    def test_detect_gemini_dir(self, tmp_path: Path) -> None:
        """Detect gemini via .gemini/ directory."""
        (tmp_path / ".gemini").mkdir()
        detected = detect_ide(tmp_path)
        assert "gemini" in detected

    def test_detect_gemini_md(self, tmp_path: Path) -> None:
        """Detect gemini via GEMINI.md file."""
        (tmp_path / "GEMINI.md").write_text("# My project\n")
        detected = detect_ide(tmp_path)
        assert "gemini" in detected

    def test_no_detect_without_artifacts(self, tmp_path: Path) -> None:
        """Clean dir should not detect gemini."""
        detected = detect_ide(tmp_path)
        assert "gemini" not in detected

    def test_detect_gemini_both_signals(self, tmp_path: Path) -> None:
        """Both .gemini/ dir and GEMINI.md present — still single detection."""
        (tmp_path / ".gemini").mkdir()
        (tmp_path / "GEMINI.md").write_text("# Project\n")
        detected = detect_ide(tmp_path)
        assert detected.count("gemini") == 1

    def test_resolve_ide_targets_gemini(self, tmp_path: Path) -> None:
        """When gemini artifacts exist, resolve_ide_targets includes 'gemini'."""
        (tmp_path / ".gemini").mkdir()
        targets = resolve_ide_targets(tmp_path)
        assert "gemini" in targets

    def test_resolve_ide_targets_gemini_override(self, tmp_path: Path) -> None:
        """Explicit ide_override='gemini' returns gemini even with no artifacts."""
        targets = resolve_ide_targets(tmp_path, ide_override="gemini")
        assert targets == ["gemini"]

    def test_resolve_ide_targets_all_includes_gemini(self, tmp_path: Path) -> None:
        """Override 'all' includes gemini."""
        targets = resolve_ide_targets(tmp_path, ide_override="all")
        assert "gemini" in targets


# =====================================================================
# 3. Instructions Tests
# =====================================================================


@pytest.mark.unit
class TestGeminiInstructions:
    """Test generate_gemini_instructions and smart-merge logic."""

    def test_instructions_created(self, fake_git_repo: Path) -> None:
        result = generate_gemini_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _GEMINI_MD_PATH).is_file()
        assert _GEMINI_MD_PATH in result["created"]

    def test_instructions_contains_trw_markers(self, fake_git_repo: Path) -> None:
        generate_gemini_instructions(fake_git_repo)
        content = (fake_git_repo / _GEMINI_MD_PATH).read_text()
        assert _GEMINI_TRW_START_MARKER in content
        assert _GEMINI_TRW_END_MARKER in content

    def test_instructions_contains_ceremony_protocol(self, fake_git_repo: Path) -> None:
        generate_gemini_instructions(fake_git_repo)
        content = (fake_git_repo / _GEMINI_MD_PATH).read_text()
        assert "TRW Framework Integration" in content
        assert "Session Protocol" in content
        assert "trw_session_start" in content
        assert "trw_learn" in content
        assert "trw_checkpoint" in content
        assert "trw_deliver" in content

    def test_instructions_mentions_gemini_cli(self, fake_git_repo: Path) -> None:
        """Content should reference Gemini CLI, not Claude Code."""
        generate_gemini_instructions(fake_git_repo)
        content = (fake_git_repo / _GEMINI_MD_PATH).read_text()
        assert "Gemini CLI" in content
        assert "mcp_trw_" in content

    def test_instructions_smart_merge_preserves_user_content(self, fake_git_repo: Path) -> None:
        """Existing file with user content + TRW markers -> user content preserved."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH

        user_before = "# My Custom Project\n\nDo NOT delete this.\n\n"
        user_after = "\n\n## My Other Section\n\nKeep this too.\n"
        original_trw = f"{_GEMINI_TRW_START_MARKER}\nold content here\n{_GEMINI_TRW_END_MARKER}"
        instructions_path.write_text(user_before + original_trw + user_after)

        result = generate_gemini_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "My Custom Project" in content
        assert "Do NOT delete this." in content
        assert "My Other Section" in content
        assert "Keep this too." in content
        assert "TRW Framework Integration" in content
        assert "old content here" not in content

    def test_instructions_fresh_file_when_no_markers(self, fake_git_repo: Path) -> None:
        """Existing file without markers gets TRW section appended."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH
        instructions_path.write_text("# User instructions only\n\nNo markers here.\n")

        result = generate_gemini_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "User instructions only" in content
        assert "No markers here." in content
        assert _GEMINI_TRW_START_MARKER in content
        assert _GEMINI_TRW_END_MARKER in content

    def test_instructions_force_overwrites(self, fake_git_repo: Path) -> None:
        """force=True completely replaces the file with TRW content."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH
        instructions_path.write_text("# I will be overwritten\nUser content here.\n")

        result = generate_gemini_instructions(fake_git_repo, force=True)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "I will be overwritten" not in content
        assert _GEMINI_TRW_START_MARKER in content
        assert "TRW Framework Integration" in content

    def test_instructions_updated_when_existing(self, fake_git_repo: Path) -> None:
        """Re-running on existing file marks it as updated, not created."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH
        instructions_path.write_text("# Existing\n")

        result = generate_gemini_instructions(fake_git_repo)
        assert _GEMINI_MD_PATH in result["updated"]
        assert _GEMINI_MD_PATH not in result["created"]


# =====================================================================
# 3a. Smart-merge unit tests (pure functions)
# =====================================================================


@pytest.mark.unit
class TestGeminiSmartMerge:
    """Unit tests for the _smart_merge_instructions helper."""

    def test_merge_replaces_trw_section(self) -> None:
        existing = f"before\n{_GEMINI_TRW_START_MARKER}\nold\n{_GEMINI_TRW_END_MARKER}\nafter"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "old" not in merged
        assert "new" in merged
        assert "before" in merged
        assert "after" in merged

    def test_merge_appends_when_no_markers(self) -> None:
        existing = "user content only"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew section\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "user content only" in merged
        assert "new section" in merged

    def test_merge_empty_existing(self) -> None:
        new_content = f"{_GEMINI_TRW_START_MARKER}\nstuff\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions("", new_content)
        assert "stuff" in merged

    def test_gemini_instructions_content_has_markers(self) -> None:
        content = _gemini_instructions_content()
        assert content.startswith(_GEMINI_TRW_START_MARKER)
        assert _GEMINI_TRW_END_MARKER in content

    def test_merge_end_before_start_appends(self) -> None:
        """End marker before start marker is treated as corrupted — append instead."""
        existing = f"user\n{_GEMINI_TRW_END_MARKER}\nmiddle\n{_GEMINI_TRW_START_MARKER}"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")
        assert "user" in merged

    def test_merge_single_start_marker_appends(self) -> None:
        """Only start marker present — treated as no valid pair, append."""
        existing = f"user\n{_GEMINI_TRW_START_MARKER}\npartial"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_single_end_marker_appends(self) -> None:
        """Only end marker present — treated as no valid pair, append."""
        existing = f"user\n{_GEMINI_TRW_END_MARKER}\nstuff"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_idempotent(self, fake_git_repo: Path) -> None:
        """Running generate_gemini_instructions twice marks second as preserved."""
        result1 = generate_gemini_instructions(fake_git_repo)
        assert result1.get("created") or result1.get("updated")
        result2 = generate_gemini_instructions(fake_git_repo)
        assert result2.get("preserved")


# =====================================================================
# 4. MCP Config Tests
# =====================================================================


@pytest.mark.unit
class TestGeminiMCPConfig:
    """Test generate_gemini_mcp_config deep-merge logic."""

    def test_mcp_config_created(self, fake_git_repo: Path) -> None:
        result = generate_gemini_mcp_config(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _GEMINI_SETTINGS_PATH).is_file()

    def test_mcp_config_has_trw_server(self, fake_git_repo: Path) -> None:
        generate_gemini_mcp_config(fake_git_repo)
        data = json.loads((fake_git_repo / _GEMINI_SETTINGS_PATH).read_text())
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        # PRD-FIX-072-FR02: command is now an absolute path via shutil.which
        cmd = data["mcpServers"]["trw"]["command"]
        assert cmd.endswith(("trw-mcp", "python")), f"Unexpected command: {cmd}"
        args = data["mcpServers"]["trw"]["args"]
        # Args are either ["serve"] (direct) or ["-m", "trw_mcp", "serve"] (module fallback)
        assert "serve" in args
        assert data["mcpServers"]["trw"]["trust"] is True

    def test_mcp_config_preserves_existing_settings(self, fake_git_repo: Path) -> None:
        """Existing non-MCP settings are preserved during merge."""
        settings_path = fake_git_repo / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        existing = {"model": {"name": "gemini-2.5-pro"}, "ui": {"theme": "dark"}}
        settings_path.write_text(json.dumps(existing))

        generate_gemini_mcp_config(fake_git_repo)
        data = json.loads(settings_path.read_text())

        assert data["model"]["name"] == "gemini-2.5-pro"
        assert data["ui"]["theme"] == "dark"
        assert data["mcpServers"]["trw"]["command"].endswith(("trw-mcp", "python"))

    def test_mcp_config_preserves_other_servers(self, fake_git_repo: Path) -> None:
        """Other MCP servers are preserved during merge."""
        settings_path = fake_git_repo / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        existing = {
            "mcpServers": {
                "github": {"command": "gh-mcp", "args": ["serve"]},
            }
        }
        settings_path.write_text(json.dumps(existing))

        generate_gemini_mcp_config(fake_git_repo)
        data = json.loads(settings_path.read_text())

        assert data["mcpServers"]["github"]["command"] == "gh-mcp"
        assert data["mcpServers"]["trw"]["command"].endswith(("trw-mcp", "python"))

    def test_mcp_config_creates_gemini_dir(self, fake_git_repo: Path) -> None:
        """The .gemini directory is created if it doesn't exist."""
        result = generate_gemini_mcp_config(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / ".gemini").is_dir()

    def test_mcp_config_handles_malformed_json(self, fake_git_repo: Path) -> None:
        """Malformed JSON in existing file is recovered: backed up + rewritten.

        The bootstrap path treats malformed pre-existing settings as recoverable —
        it writes a ``.bak`` next to the original and rewrites a fresh document
        rather than crashing or aborting.  The signal is therefore a warning
        plus a backup file on disk, not an error.
        """
        settings_path = fake_git_repo / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{broken json!!")

        result = generate_gemini_mcp_config(fake_git_repo)
        # Recovery (not crash): no errors, but a warning naming the backup.
        assert result["errors"] == []
        warnings = result.get("warnings", [])
        assert any("backed up" in w for w in warnings), warnings
        assert (settings_path.with_suffix(".json.bak")).exists()

    def test_mcp_config_updated_when_existing(self, fake_git_repo: Path) -> None:
        """Re-running on existing file marks as updated."""
        settings_path = fake_git_repo / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{}")

        result = generate_gemini_mcp_config(fake_git_repo)
        assert _GEMINI_SETTINGS_PATH in result["updated"]

    def test_mcp_config_json_well_formatted(self, fake_git_repo: Path) -> None:
        """Output JSON is indented with 2 spaces."""
        generate_gemini_mcp_config(fake_git_repo)
        raw = (fake_git_repo / _GEMINI_SETTINGS_PATH).read_text()
        assert raw.endswith("\n")
        data = json.loads(raw)
        expected = json.dumps(data, indent=2) + "\n"
        assert raw == expected


# =====================================================================
# 5. Agents Tests
# =====================================================================


@pytest.mark.unit
class TestGeminiAgents:
    """Test generate_gemini_agents."""

    def test_agents_dir_created(self, fake_git_repo: Path) -> None:
        result = generate_gemini_agents(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _GEMINI_AGENTS_DIR).is_dir()

    def test_agents_files_created(self, fake_git_repo: Path) -> None:
        result = generate_gemini_agents(fake_git_repo)
        assert not result["errors"]
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        agent_files = list(agents_dir.glob("trw-*.md"))
        assert len(agent_files) == 4

    def test_expected_agents_exist(self, fake_git_repo: Path) -> None:
        """All four TRW agents must be generated."""
        generate_gemini_agents(fake_git_repo)
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        assert (agents_dir / "trw-explorer.md").exists()
        assert (agents_dir / "trw-implementer.md").exists()
        assert (agents_dir / "trw-reviewer.md").exists()
        assert (agents_dir / "trw-lead.md").exists()

    def test_agent_yaml_frontmatter(self, fake_git_repo: Path) -> None:
        """Verify YAML frontmatter with name, description, tools."""
        generate_gemini_agents(fake_git_repo)
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        for agent_file in agents_dir.glob("trw-*.md"):
            content = agent_file.read_text()
            assert content.startswith("---"), f"{agent_file.name} missing YAML frontmatter"
            assert "name:" in content, f"{agent_file.name} missing name field"
            assert "description:" in content, f"{agent_file.name} missing description field"
            assert "tools:" in content, f"{agent_file.name} missing tools field"

    def test_agent_tools_are_gemini_format(self, fake_git_repo: Path) -> None:
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

    def test_agents_reference_mcp_trw(self, fake_git_repo: Path) -> None:
        """Agents should reference mcp_trw_ tools for TRW integration."""
        generate_gemini_agents(fake_git_repo)
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        for agent_file in agents_dir.glob("trw-*.md"):
            content = agent_file.read_text()
            assert "mcp_trw_" in content, f"{agent_file.name} missing mcp_trw_ reference"

    def test_explorer_uses_grep_search(self, fake_git_repo: Path) -> None:
        """Explorer agent must use official 'grep_search' tool name (not search_file_content)."""
        generate_gemini_agents(fake_git_repo)
        explorer = (fake_git_repo / _GEMINI_AGENTS_DIR / "trw-explorer.md").read_text()
        assert "grep_search" in explorer
        assert "search_file_content" not in explorer

    def test_agents_no_overwrite_existing(self, fake_git_repo: Path) -> None:
        """Existing agent files preserved without force."""
        generate_gemini_agents(fake_git_repo)

        custom_path = fake_git_repo / _GEMINI_AGENTS_DIR / "trw-explorer.md"
        custom_path.write_text("# My custom agent\n")

        result = generate_gemini_agents(fake_git_repo)
        assert not result["errors"]

        rel_path = f"{_GEMINI_AGENTS_DIR}/trw-explorer.md"
        assert rel_path in result["preserved"]
        assert custom_path.read_text() == "# My custom agent\n"

    def test_agents_force_overwrites_existing(self, fake_git_repo: Path) -> None:
        """force=True regenerates all agents."""
        generate_gemini_agents(fake_git_repo)

        custom_path = fake_git_repo / _GEMINI_AGENTS_DIR / "trw-explorer.md"
        custom_path.write_text("# My custom agent\n")

        result = generate_gemini_agents(fake_git_repo, force=True)
        assert not result["errors"]
        assert custom_path.read_text() != "# My custom agent\n"

    def test_agents_preserves_user_agents(self, fake_git_repo: Path) -> None:
        """User-created agents (not trw-* prefix) are never touched."""
        agents_dir = fake_git_repo / _GEMINI_AGENTS_DIR
        agents_dir.mkdir(parents=True)
        user_agent = agents_dir / "my-custom-agent.md"
        user_agent.write_text("# Custom agent\n")

        generate_gemini_agents(fake_git_repo)

        assert user_agent.read_text() == "# Custom agent\n"

    def test_agents_created_count(self, fake_git_repo: Path) -> None:
        from trw_mcp.bootstrap._gemini import _GEMINI_AGENT_TEMPLATES

        result = generate_gemini_agents(fake_git_repo)
        assert len(result["created"]) == len(_GEMINI_AGENT_TEMPLATES)


# =====================================================================
# 6. Init/Update Integration Tests
# =====================================================================


@pytest.mark.unit
class TestGeminiInitProject:
    """Test init_project wiring for gemini."""

    def test_init_project_with_gemini_override(self, fake_git_repo: Path) -> None:
        """Call init_project with ide='gemini', verify gemini artifacts created."""
        from trw_mcp.bootstrap import init_project

        result = init_project(fake_git_repo, ide="gemini")
        assert not result["errors"]

        assert (fake_git_repo / "GEMINI.md").is_file()
        assert (fake_git_repo / ".gemini" / "settings.json").is_file()
        assert (fake_git_repo / ".gemini" / "agents").is_dir()

    def test_init_project_auto_detect_gemini(self, tmp_path: Path) -> None:
        """Create gemini detection artifacts first, then init_project auto-detects."""
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        (tmp_path / ".gemini").mkdir()

        result = init_project(tmp_path)
        assert not result["errors"]

        assert (tmp_path / "GEMINI.md").is_file()
        assert (tmp_path / ".gemini" / "settings.json").is_file()


@pytest.mark.unit
class TestGeminiUpdateArtifacts:
    """Test _update_gemini_artifacts integration."""

    def test_update_gemini_artifacts(self, fake_git_repo: Path) -> None:
        """Call _update_gemini_artifacts, verify it runs without errors."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        (fake_git_repo / ".gemini").mkdir()

        _update_gemini_artifacts(fake_git_repo, result)
        assert not result["errors"]
        assert len(result["created"]) >= 1

    def test_update_gemini_artifacts_skips_without_detection(self, fake_git_repo: Path) -> None:
        """Without gemini artifacts detected, _update_gemini_artifacts is a no-op."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result)
        assert not result["created"]
        assert not result["errors"]

    def test_update_gemini_artifacts_with_override(self, fake_git_repo: Path) -> None:
        """Override to gemini even without detection artifacts."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result, ide_override="gemini")
        assert not result["errors"]
        assert len(result["created"]) >= 1

    def test_update_gemini_artifacts_idempotent(self, fake_git_repo: Path) -> None:
        """Running update twice is safe — second run preserves rather than errors."""
        from trw_mcp.bootstrap._ide_targets import _update_gemini_artifacts

        (fake_git_repo / ".gemini").mkdir()

        result1: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result1)
        assert not result1["errors"]

        result2: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_gemini_artifacts(fake_git_repo, result2)
        assert not result2["errors"]
