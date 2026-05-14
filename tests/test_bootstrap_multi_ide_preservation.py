"""Split bootstrap multi-IDE preservation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.state.persistence import FileStateReader

from ._bootstrap_test_support import patch_update_project_internals


@pytest.mark.unit
class TestUpdateProjectMultiIDE:
    """FR15: Update-project supports multiple IDEs (PRD-CORE-074)."""

    def test_fr15_update_opencode_also_creates_agents_md(self, tmp_path: Path) -> None:
        """update_project with opencode detected also creates/updates AGENTS.md."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\n")
        (tmp_path / ".opencode").mkdir()

        with patch_update_project_internals():
            result = update_project(tmp_path)

        assert (tmp_path / "AGENTS.md").exists()
        content = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- trw:start -->" in content

    def test_fr15_update_opencode_preserves_user_modified_command(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="opencode")
        assert not init_result["errors"], init_result["errors"]

        command_path = tmp_path / ".opencode" / "commands" / "trw-deliver.md"
        command_path.write_text("user-modified-command\n", encoding="utf-8")

        result = update_project(tmp_path, ide="opencode")

        assert not result["errors"], result["errors"]
        assert command_path.read_text(encoding="utf-8") == "user-modified-command\n"
        assert ".opencode/commands/trw-deliver.md" in result["preserved"]

    def test_fr15_update_opencode_preserves_user_modified_instructions(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="opencode")
        assert not init_result["errors"], init_result["errors"]

        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.write_text("user-modified-instructions\n", encoding="utf-8")

        result = update_project(tmp_path, ide="opencode")

        assert not result["errors"], result["errors"]
        assert instructions_path.read_text(encoding="utf-8") == "user-modified-instructions\n"
        assert ".opencode/INSTRUCTIONS.md" in result["preserved"]

    def test_fr15_update_opencode_removes_stale_managed_command(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="opencode")
        assert not init_result["errors"], init_result["errors"]

        stale_path = tmp_path / ".opencode" / "commands" / "trw-stale.md"
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text("stale\n", encoding="utf-8")

        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        manifest["opencode_commands"] = [*manifest.get("opencode_commands", []), "trw-stale.md"]
        manifest.setdefault("custom_opencode_commands", [])
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        result = update_project(tmp_path, ide="opencode")

        assert not result["errors"], result["errors"]
        assert not stale_path.exists()
        assert any("removed:" in item and "trw-stale.md" in item for item in result["updated"])

    def test_fr15_update_opencode_preserves_user_modified_agent(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="opencode")
        assert not init_result["errors"], init_result["errors"]

        agent_path = tmp_path / ".opencode" / "agents" / "trw-reviewer.md"
        agent_path.write_text("user-modified-agent\n", encoding="utf-8")

        result = update_project(tmp_path, ide="opencode")

        assert not result["errors"], result["errors"]
        assert agent_path.read_text(encoding="utf-8") == "user-modified-agent\n"
        assert ".opencode/agents/trw-reviewer.md" in result["preserved"]

    def test_fr15_update_opencode_preserves_user_modified_skill(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="opencode")
        assert not init_result["errors"], init_result["errors"]

        skill_path = tmp_path / ".opencode" / "skills" / "trw-deliver" / "SKILL.md"
        skill_path.write_text("user-modified-skill\n", encoding="utf-8")

        result = update_project(tmp_path, ide="opencode")

        assert not result["errors"], result["errors"]
        assert skill_path.read_text(encoding="utf-8") == "user-modified-skill\n"
        assert ".opencode/skills/trw-deliver/SKILL.md" in result["preserved"]

    def test_fr15_update_opencode_removes_stale_managed_agent(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="opencode")
        assert not init_result["errors"], init_result["errors"]

        stale_path = tmp_path / ".opencode" / "agents" / "trw-stale-agent.md"
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text("stale\n", encoding="utf-8")

        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        manifest["opencode_agents"] = [*manifest.get("opencode_agents", []), "trw-stale-agent.md"]
        manifest.setdefault("custom_opencode_agents", [])
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        result = update_project(tmp_path, ide="opencode")

        assert not result["errors"], result["errors"]
        assert not stale_path.exists()
        assert any("removed:" in item and "trw-stale-agent.md" in item for item in result["updated"])

    def test_fr15_update_opencode_removes_stale_managed_skill(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="opencode")
        assert not init_result["errors"], init_result["errors"]

        stale_path = tmp_path / ".opencode" / "skills" / "trw-stale-skill" / "SKILL.md"
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_text("stale\n", encoding="utf-8")

        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        manifest["opencode_skills"] = [*manifest.get("opencode_skills", []), "trw-stale-skill"]
        manifest.setdefault("custom_opencode_skills", [])
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        result = update_project(tmp_path, ide="opencode")

        assert not result["errors"], result["errors"]
        assert not stale_path.parent.exists()
        assert any("removed:" in item and "trw-stale-skill" in item for item in result["updated"])

    def test_fr15_update_codex_preserves_user_modified_instructions(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="codex")
        assert not init_result["errors"], init_result["errors"]

        instructions_path = tmp_path / ".codex" / "INSTRUCTIONS.md"
        instructions_path.write_text("user-modified-codex-instructions\n", encoding="utf-8")

        result = update_project(tmp_path, ide="codex")

        assert not result["errors"], result["errors"]
        assert instructions_path.read_text(encoding="utf-8") == "user-modified-codex-instructions\n"
        assert ".codex/INSTRUCTIONS.md" in result["preserved"]
