"""Tests for Agent Teams integration — hooks, claude_md rendering, settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ── Shared path resolution ──────────────────────────────────────────────
# Package data lives in src/trw_mcp/data/ (canonical source).
# The monorepo also copies these to .claude/ at root. Tests prefer
# package data so they work in both monorepo and standalone contexts.

_TESTS_DIR = Path(__file__).parent
_PKG_DATA = _TESTS_DIR.parent / "src" / "trw_mcp" / "data"
_MONOREPO_CLAUDE = _TESTS_DIR.parent.parent / ".claude"


def _resolve_data_path(pkg_subdir: str, monorepo_subdir: str) -> Path:
    """Resolve a data path, preferring package data over monorepo location."""
    pkg = _PKG_DATA / pkg_subdir
    if pkg.exists():
        return pkg
    mono = _MONOREPO_CLAUDE / monorepo_subdir
    if mono.exists():
        return mono
    pytest.skip(f"{pkg_subdir} not found in package data or monorepo")

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import (
    render_agent_teams_protocol,
    render_template,
)

_CFG = TRWConfig()


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP

    from trw_mcp.tools.learning import register_learning_tools

    srv = FastMCP("test")
    register_learning_tools(srv)
    return get_tools_sync(srv)


class TestRenderAgentTeamsProtocol:
    """Tests for render_agent_teams_protocol()."""

    def test_renders_when_enabled(self) -> None:
        """Agent Teams section renders when agent_teams_enabled=True."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=True)
        ):
            result = render_agent_teams_protocol()

        assert "## TRW Agent Teams Protocol" in result
        assert "Dual-Mode Orchestration" in result
        assert "Teammate Lifecycle" in result
        assert "Quality Gate Hooks" in result
        assert "File Ownership" in result
        assert "Teammate Roles" in result

    def test_empty_when_disabled(self) -> None:
        """Agent Teams section is empty when agent_teams_enabled=False."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=False)
        ):
            result = render_agent_teams_protocol()

        assert result == ""

    def test_contains_all_five_roles(self) -> None:
        """All five teammate roles appear in the rendered table."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=True)
        ):
            result = render_agent_teams_protocol()

        assert "trw-lead" in result
        assert "trw-implementer" in result
        assert "trw-tester" in result
        assert "trw-reviewer" in result
        assert "trw-researcher" in result

    def test_contains_hook_names(self) -> None:
        """TeammateIdle and TaskCompleted hooks are documented."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=True)
        ):
            result = render_agent_teams_protocol()

        assert "TeammateIdle" in result
        assert "TaskCompleted" in result

    def test_contains_dual_mode_table(self) -> None:
        """Dual-mode table lists both Subagents and Agent Teams."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=True)
        ):
            result = render_agent_teams_protocol()

        assert "Subagents" in result
        assert "Agent Teams" in result
        assert "TeamCreate" in result

    def test_lifecycle_steps_ordered(self) -> None:
        """Lifecycle steps appear in correct order (1-6)."""
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=True)
        ):
            result = render_agent_teams_protocol()

        positions = []
        for i in range(1, 7):
            pos = result.find(f"{i}.")
            assert pos != -1, f"Step {i} must exist"
            positions.append(pos)
        # Verify strict ordering: each step appears after the previous
        for i in range(1, len(positions)):
            assert positions[i] > positions[i - 1], (
                f"Step {i + 1} (pos {positions[i]}) must appear after step {i} (pos {positions[i - 1]})"
            )


class TestAgentTeamsTemplateIntegration:
    """Tests for Agent Teams section in the CLAUDE.md template pipeline."""

    def test_template_placeholder_replaced(self) -> None:
        """{{agent_teams_section}} placeholder is correctly replaced."""
        template = "before\n{{agent_teams_section}}after"
        context = {"agent_teams_section": "TEAMS_CONTENT\n"}
        result = render_template(template, context)
        assert "TEAMS_CONTENT" in result
        assert "{{agent_teams_section}}" not in result

    def test_template_placeholder_empty_when_disabled(self) -> None:
        """Disabled agent_teams produces empty replacement, no blank sections."""
        template = "before\n{{agent_teams_section}}after"
        context = {"agent_teams_section": ""}
        result = render_template(template, context)
        assert "before\n" in result
        assert "after" in result

    def test_bundled_template_has_placeholder(self) -> None:
        """Bundled claude_md.md template includes agent_teams_section placeholder."""
        data_dir = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "templates"
        bundled = data_dir / "claude_md.md"
        assert bundled.exists(), "Bundled template must exist"
        content = bundled.read_text(encoding="utf-8")
        assert "{{agent_teams_section}}" in content

    def test_full_sync_includes_agent_teams(self, tmp_path: Path) -> None:
        """trw_claude_md_sync completes successfully when agent_teams_enabled=True.

        PRD-CORE-061: Agent Teams content is suppressed from CLAUDE.md (moved to
        /trw-ceremony-guide skill), but the sync should still succeed.
        """
        trw_dir = tmp_path / _CFG.trw_dir
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / _CFG.learnings_dir / _CFG.entries_dir).mkdir(parents=True, exist_ok=True)

        tools = _get_tools()
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=True)
        ):
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["status"] == "synced"

        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content

    def test_full_sync_excludes_agent_teams_when_disabled(self, tmp_path: Path) -> None:
        """trw_claude_md_sync omits Agent Teams section when disabled."""
        trw_dir = tmp_path / _CFG.trw_dir
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / _CFG.learnings_dir / _CFG.entries_dir).mkdir(parents=True, exist_ok=True)

        tools = _get_tools()
        with patch(
            "trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig(agent_teams_enabled=False)
        ):
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["status"] == "synced"

        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "Agent Teams Protocol" not in content


class TestAgentTeamsConfig:
    """Tests for agent_teams_enabled config field."""

    def test_default_enabled(self) -> None:
        """agent_teams_enabled defaults to True."""
        config = TRWConfig()
        assert config.agent_teams_enabled is True

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_AGENT_TEAMS_ENABLED env var overrides default."""
        monkeypatch.setenv("TRW_AGENT_TEAMS_ENABLED", "false")
        config = TRWConfig()
        assert config.agent_teams_enabled is False


class TestHookScripts:
    """Tests for Agent Teams hook script structure (static analysis)."""

    @pytest.fixture()
    def hooks_dir(self) -> Path:
        """Return path to bundled hook scripts."""
        return _resolve_data_path("hooks", "hooks")

    @pytest.mark.parametrize(
        "script_name",
        ["teammate-idle.sh", "task-completed.sh"],
    )
    def test_hook_exists(self, hooks_dir: Path, script_name: str) -> None:
        """Hook script file exists."""
        assert (hooks_dir / script_name).exists(), f"{script_name} must exist"

    @pytest.mark.parametrize(
        "script_name",
        ["teammate-idle.sh", "task-completed.sh"],
    )
    def test_hook_is_posix_shell(self, hooks_dir: Path, script_name: str) -> None:
        """Hook script starts with #!/bin/sh (POSIX)."""
        content = (hooks_dir / script_name).read_text(encoding="utf-8")
        assert content.startswith("#!/bin/sh")

    @pytest.mark.parametrize(
        "script_name",
        ["teammate-idle.sh", "task-completed.sh"],
    )
    def test_hook_fail_open(self, hooks_dir: Path, script_name: str) -> None:
        """Hook script has fail-open trap (exit 0 on unexpected error)."""
        content = (hooks_dir / script_name).read_text(encoding="utf-8")
        # Hooks use conditional fail-open: intentional exits (exit 2 for blocking)
        # are allowed, but unexpected errors silently exit 0.
        assert "exit 0" in content and "trap" in content

    @pytest.mark.parametrize(
        "script_name",
        ["teammate-idle.sh", "task-completed.sh"],
    )
    def test_hook_sources_lib(self, hooks_dir: Path, script_name: str) -> None:
        """Hook script sources lib-trw.sh."""
        content = (hooks_dir / script_name).read_text(encoding="utf-8")
        assert "lib-trw.sh" in content

    def test_teammate_idle_extracts_teammate_name(self, hooks_dir: Path) -> None:
        """teammate-idle.sh extracts teammate_name from JSON payload."""
        content = (hooks_dir / "teammate-idle.sh").read_text(encoding="utf-8")
        assert "teammate_name" in content

    def test_task_completed_extracts_task_subject(self, hooks_dir: Path) -> None:
        """task-completed.sh extracts task_subject from JSON payload."""
        content = (hooks_dir / "task-completed.sh").read_text(encoding="utf-8")
        assert "task_subject" in content

    def test_teammate_idle_prd_reference(self, hooks_dir: Path) -> None:
        """teammate-idle.sh references PRD-INFRA-010."""
        content = (hooks_dir / "teammate-idle.sh").read_text(encoding="utf-8")
        assert "PRD-INFRA-010" in content

    def test_task_completed_prd_reference(self, hooks_dir: Path) -> None:
        """task-completed.sh references PRD-INFRA-004."""
        content = (hooks_dir / "task-completed.sh").read_text(encoding="utf-8")
        assert "PRD-INFRA-004" in content


class TestSettingsJson:
    """Tests for .claude/settings.json hook registrations.

    These tests validate the monorepo's settings.json. When running in the
    standalone public repo (no .claude/settings.json at repo root), tests
    are skipped — the equivalent validation happens in test_bootstrap.py
    via init-project.
    """

    @pytest.fixture()
    def settings_path(self) -> Path:
        """Return path to settings.json (monorepo only — skips in standalone)."""
        path = _MONOREPO_CLAUDE / "settings.json"
        if not path.exists():
            pytest.skip("settings.json not present (standalone repo — tested via init-project)")
        return path

    def test_settings_exists(self, settings_path: Path) -> None:
        """settings.json exists."""
        assert settings_path.exists()

    def test_settings_valid_json(self, settings_path: Path) -> None:
        """settings.json is valid JSON."""
        import json

        content = settings_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_teammate_idle_hook_registered(self, settings_path: Path) -> None:
        """TeammateIdle hook is registered in settings.json."""
        import json

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        assert "TeammateIdle" in hooks
        entries = hooks["TeammateIdle"]
        assert len(entries) >= 1
        assert "teammate-idle.sh" in entries[0]["hooks"][0]["command"]

    def test_task_completed_hook_registered(self, settings_path: Path) -> None:
        """TaskCompleted hook is registered in settings.json."""
        import json

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        assert "TaskCompleted" in hooks
        entries = hooks["TaskCompleted"]
        assert len(entries) >= 1
        assert "task-completed.sh" in entries[0]["hooks"][0]["command"]

    def test_agent_teams_env_var(self, settings_path: Path) -> None:
        """CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 is set in env."""
        import json

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        env = data.get("env", {})
        assert env.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") == "1"


class TestAgentDefinitions:
    """Tests for .claude/agents/ teammate definitions.

    Adding a new agent? Update these locations in order:
    1. Create `.claude/agents/{name}.md` (YAML frontmatter + markdown body)
    2. Copy to `trw-mcp/src/trw_mcp/data/agents/{name}.md` (bundled for pip install)
       — or run `scripts/sync-data.sh` which copies .claude/agents/ -> data/agents/
    3. Add to parametrized lists below (test_agent_file_exists, test_agent_model_assignment,
       test_agent_no_stray_tags, test_agent_has_required_frontmatter) + role-specific tests
    4. Add to `TestAgents.EXPECTED_AGENTS` in `test_bootstrap.py`
    5. Update agent count in `test_manifest_lists_all_bundled_artifacts` in `test_bootstrap.py`
    6. Add to `render_agent_teams_protocol()` table in `state/claude_md.py`
    7. Add to FRAMEWORK.md agents table (root + `.trw/frameworks/` copy)
    """

    @pytest.fixture()
    def agents_dir(self) -> Path:
        """Return path to bundled agent definitions."""
        return _resolve_data_path("agents", "agents")

    @pytest.mark.parametrize(
        "agent_name",
        [
            "trw-lead.md",
            "trw-implementer.md",
            "trw-tester.md",
            "trw-reviewer.md",
            "trw-researcher.md",
        ],
    )
    def test_agent_file_exists(self, agents_dir: Path, agent_name: str) -> None:
        """Agent definition file exists."""
        assert (agents_dir / agent_name).exists(), f"{agent_name} must exist"

    @pytest.mark.parametrize(
        ("agent_name", "expected_model"),
        [
            ("trw-lead.md", "claude-opus-4-6"),
            ("trw-implementer.md", "claude-opus-4-6"),
            ("trw-tester.md", "claude-sonnet-4-6"),
            ("trw-reviewer.md", "claude-sonnet-4-6"),
            ("trw-researcher.md", "claude-sonnet-4-6"),
        ],
    )
    def test_agent_model_assignment(self, agents_dir: Path, agent_name: str, expected_model: str) -> None:
        """Agent definition specifies correct model."""
        import yaml

        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        assert meta["model"] == expected_model

    @pytest.mark.parametrize(
        "agent_name",
        ["trw-reviewer.md", "trw-researcher.md"],
    )
    def test_readonly_agents_no_write(self, agents_dir: Path, agent_name: str) -> None:
        """Reviewer and researcher agents have Write, Edit, and Bash in disallowedTools."""
        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        # Parse YAML frontmatter to check disallowedTools
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        disallowed = meta.get("disallowedTools", [])
        allowed = meta.get("allowedTools", [])
        assert "Write" in disallowed, f"{agent_name}: Write must be disallowed"
        assert "Edit" in disallowed, f"{agent_name}: Edit must be disallowed"
        assert "Bash" in disallowed, f"{agent_name}: Bash must be disallowed (write bypass)"
        assert "Bash" not in allowed, f"{agent_name}: Bash must not be in allowedTools"

    @pytest.mark.parametrize(
        "agent_name",
        ["trw-lead.md", "trw-implementer.md", "trw-tester.md"],
    )
    def test_implementation_agents_have_edit(self, agents_dir: Path, agent_name: str) -> None:
        """Lead, implementer, and tester agents have Edit and Write in allowedTools."""
        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        allowed = meta.get("allowedTools", [])
        assert "Edit" in allowed, f"{agent_name}: Edit must be in allowedTools"
        assert "Write" in allowed, f"{agent_name}: Write must be in allowedTools"

    def test_lead_has_team_management_tools(self, agents_dir: Path) -> None:
        """trw-lead has TaskCreate, TaskUpdate, TeamCreate, SendMessage tools."""
        content = (agents_dir / "trw-lead.md").read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        allowed = meta.get("allowedTools", [])
        for tool in ["TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TeamCreate", "TeamDelete", "SendMessage"]:
            assert tool in allowed, f"trw-lead: {tool} must be in allowedTools"

    def test_lead_has_all_trw_mcp_tools(self, agents_dir: Path) -> None:
        """trw-lead has access to all TRW MCP orchestration tools."""
        content = (agents_dir / "trw-lead.md").read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        allowed = meta.get("allowedTools", [])
        for tool in [
            "mcp__trw__trw_session_start",
            "mcp__trw__trw_init",
            "mcp__trw__trw_status",
            "mcp__trw__trw_checkpoint",
            "mcp__trw__trw_deliver",
            "mcp__trw__trw_learn",
            "mcp__trw__trw_recall",
            "mcp__trw__trw_build_check",
            "mcp__trw__trw_prd_create",
            "mcp__trw__trw_prd_validate",
        ]:
            assert tool in allowed, f"trw-lead: {tool} must be in allowedTools"

    def test_lead_has_skill_tool(self, agents_dir: Path) -> None:
        """trw-lead has Skill tool for invoking skills at phase boundaries."""
        content = (agents_dir / "trw-lead.md").read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        allowed = meta.get("allowedTools", [])
        assert "Skill" in allowed, "trw-lead: Skill must be in allowedTools"

    @pytest.mark.parametrize(
        "agent_name",
        [
            "trw-lead.md",
            "trw-implementer.md",
            "trw-tester.md",
            "trw-reviewer.md",
            "trw-researcher.md",
        ],
    )
    def test_agent_no_stray_tags(self, agents_dir: Path, agent_name: str) -> None:
        """Agent definitions must not contain stray XML closing tags."""
        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        # Check for orphan </output> tags outside of code blocks
        lines = content.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "</output>":
                raise AssertionError(f"{agent_name} line {i + 1}: stray </output> tag")

    @pytest.mark.parametrize(
        "agent_name",
        [
            "trw-lead.md",
            "trw-implementer.md",
            "trw-tester.md",
            "trw-reviewer.md",
            "trw-researcher.md",
        ],
    )
    def test_agent_has_required_frontmatter(self, agents_dir: Path, agent_name: str) -> None:
        """Agent definitions must have name, description, model in frontmatter."""
        content = (agents_dir / agent_name).read_text(encoding="utf-8")
        import yaml

        _, frontmatter, _ = content.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        assert "name" in meta, f"{agent_name}: missing 'name'"
        assert "description" in meta, f"{agent_name}: missing 'description'"
        assert "model" in meta, f"{agent_name}: missing 'model'"
        valid_models = (
            "opus",
            "sonnet",
            "haiku",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        )
        assert meta["model"] in valid_models, f"{agent_name}: model must be one of {valid_models}, got {meta['model']}"
