"""Split bootstrap multi-IDE detection/update tests."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest

from trw_mcp.bootstrap import init_project, update_project

from ._bootstrap_test_support import patch_update_project_internals

@pytest.mark.unit
class TestUpdateProjectMultiIDE:
    """FR15: Update-project supports multiple IDEs (PRD-CORE-074)."""

    def test_fr15_update_detects_opencode_by_dir(self, tmp_path: Path) -> None:
        """With .opencode/ present, update generates opencode.json."""
        # Set up a minimal existing TRW project
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n")
        (tmp_path / ".opencode").mkdir()

        # Patch heavy update internals so we focus on opencode branch
        with patch_update_project_internals():
            result = update_project(tmp_path)

        # opencode.json should be created (detected via .opencode/ dir)
        assert (tmp_path / "opencode.json").exists()
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert "mcp" in config
        assert "trw" in config["mcp"]

    def test_fr15_update_detects_opencode_by_json(self, tmp_path: Path) -> None:
        """With opencode.json present, update performs smart-merge."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n")
        # Create existing opencode.json (triggers detection)
        (tmp_path / "opencode.json").write_text(json.dumps({"model": "custom-model", "mcp": {}}))

        with patch_update_project_internals():
            result = update_project(tmp_path)

        config = json.loads((tmp_path / "opencode.json").read_text())
        # Preserved user key
        assert config.get("model") == "custom-model"
        # TRW entry injected
        assert "trw" in config["mcp"]

    def test_fr15_update_detects_codex_by_dir(self, tmp_path: Path) -> None:
        """With .codex/ present, update generates Codex artifacts."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n", encoding="utf-8")
        (tmp_path / ".codex").mkdir()

        with patch_update_project_internals():
            update_project(tmp_path)

        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        assert config["features"]["codex_hooks"] is False
        assert config["mcp_servers"]["trw"]["enabled"] is True
        assert not (tmp_path / ".codex" / "hooks.json").exists()
        assert (tmp_path / ".codex" / "agents" / "trw-reviewer.toml").exists()
        assert (tmp_path / ".agents" / "skills" / "trw-deliver" / "SKILL.md").exists()
        assert (tmp_path / "AGENTS.md").exists()

    def test_fr15_update_codex_generates_hooks_when_opted_in(self, tmp_path: Path) -> None:
        """update_project honors explicit Codex hook opt-in instead of forcing hooks."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n", encoding="utf-8")
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text("[features]\ncodex_hooks = true\n", encoding="utf-8")

        with patch_update_project_internals():
            update_project(tmp_path)

        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        assert config["features"]["codex_hooks"] is True
        assert (tmp_path / ".codex" / "hooks.json").exists()

    def test_fr15_update_codex_preserves_customized_agents_and_skills(self, tmp_path: Path) -> None:
        """update_project preserves Codex agent and skill edits in protected paths."""
        (tmp_path / ".git").mkdir()
        init_project(tmp_path, ide="codex")

        agent_path = tmp_path / ".codex" / "agents" / "trw-explorer.toml"
        skill_path = tmp_path / ".agents" / "skills" / "trw-deliver" / "SKILL.md"
        agent_path.write_text("custom agent", encoding="utf-8")
        skill_path.write_text("custom skill", encoding="utf-8")

        with patch_update_project_internals():
            result = update_project(tmp_path, ide="codex")

        assert ".codex/agents/trw-explorer.toml" in result["preserved"]
        assert ".agents/skills/trw-deliver/SKILL.md" in result["preserved"]
        assert agent_path.read_text(encoding="utf-8") == "custom agent"
        assert skill_path.read_text(encoding="utf-8") == "custom skill"

    def test_fr15_update_no_opencode_skips(self, tmp_path: Path) -> None:
        """Without opencode indicators, update does not create opencode.json."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n")
        # Only Claude Code present
        (tmp_path / ".claude").mkdir()

        with patch_update_project_internals():
            result = update_project(tmp_path)

        assert not (tmp_path / "opencode.json").exists()

    def test_fr15_update_ide_override_opencode(self, tmp_path: Path) -> None:
        """update_project(ide='opencode') creates opencode.json even without detection."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n")
        # No .opencode/ dir, but explicit override

        with patch_update_project_internals():
            result = update_project(tmp_path, ide="opencode")

        assert (tmp_path / "opencode.json").exists()
