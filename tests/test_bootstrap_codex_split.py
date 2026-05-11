"""Split bootstrap Codex tests."""

from __future__ import annotations

import json
from pathlib import Path

import tomllib

from trw_mcp.bootstrap._codex import (
    generate_codex_agents,
    generate_codex_config,
    generate_codex_hooks,
    install_codex_skills,
    merge_codex_config,
)


class TestCodexBootstrap:
    """Codex bootstrap configuration and smart-merge behavior."""

    def test_codex_config_created(self, tmp_path: Path) -> None:
        result = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in result["created"]
        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        assert config["features"]["hooks"] is False
        assert "codex_hooks" not in config["features"]
        assert config["mcp_servers"]["trw"]["enabled"] is True
        assert "url" not in config["mcp_servers"]["trw"]
        assert config["mcp_servers"]["openaiDeveloperDocs"]["enabled"] is True
        assert "trw_session_start" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_build_check" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_checkpoint" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert config["model_instructions_file"] == "INSTRUCTIONS.md"
        assert ".codex/INSTRUCTIONS.md" not in config.get("project_doc_fallback_filenames", [])
        assert all(not entry["path"].endswith("/SKILL.md") for entry in config["skills"]["config"])
        assert any(entry["path"] == ".agents/skills/trw-deliver" for entry in config["skills"]["config"])

    def test_codex_hooks_json_created(self, tmp_path: Path) -> None:
        result = generate_codex_hooks(tmp_path)
        assert ".codex/hooks.json" in result["created"]
        hooks = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        assert "UserPromptSubmit" in hooks["hooks"]
        assert "PreToolUse" in hooks["hooks"]
        assert "PostToolUse" in hooks["hooks"]
        assert "Stop" in hooks["hooks"]

    def test_codex_hooks_merge_preserves_user_handlers(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "description": "user custom",
                                "hooks": [{"type": "command", "command": "echo custom"}],
                            }
                        ],
                        "Notification": [
                            {
                                "description": "notify",
                                "hooks": [{"type": "command", "command": "echo notify"}],
                            }
                        ],
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        result = generate_codex_hooks(tmp_path)
        assert ".codex/hooks.json" in result["updated"]
        hooks = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
        assert hooks["hooks"]["Notification"][0]["description"] == "notify"
        assert hooks["hooks"]["UserPromptSubmit"][0]["description"] == "user custom"
        assert any(
            entry.get("description", "").startswith("TRW managed:") for entry in hooks["hooks"]["UserPromptSubmit"]
        )

    def test_codex_agents_created(self, tmp_path: Path) -> None:
        result = generate_codex_agents(tmp_path)
        assert ".codex/agents/trw-explorer.toml" in result["created"]
        assert ".codex/agents/trw-implementer.toml" in result["created"]
        explorer = (tmp_path / ".codex" / "agents" / "trw-explorer.toml").read_text(encoding="utf-8")
        assert 'model = "gpt-5.4-mini"' in explorer

    def test_codex_agents_preserve_existing_edits_without_force(self, tmp_path: Path) -> None:
        generate_codex_agents(tmp_path)
        explorer_path = tmp_path / ".codex" / "agents" / "trw-explorer.toml"
        explorer_path.write_text("customized explorer", encoding="utf-8")

        result = generate_codex_agents(tmp_path)

        assert ".codex/agents/trw-explorer.toml" in result["preserved"]
        assert explorer_path.read_text(encoding="utf-8") == "customized explorer"

    def test_codex_skills_installed(self, tmp_path: Path) -> None:
        result = install_codex_skills(tmp_path)
        assert any(path.endswith("/SKILL.md") for path in result["created"])
        skill_path = tmp_path / ".agents" / "skills" / "trw-deliver" / "SKILL.md"
        assert skill_path.exists()
        content = skill_path.read_text(encoding="utf-8")
        assert "# TRW Deliver" in content
        assert "trw_deliver()" in content
        assert "allowed-tools:" not in content
        assert "disable-model-invocation:" not in content
        assert "user-invocable:" not in content
        assert "model: claude-" not in content

    def test_codex_skills_preserve_existing_edits_without_force(self, tmp_path: Path) -> None:
        install_codex_skills(tmp_path)
        skill_path = tmp_path / ".agents" / "skills" / "trw-deliver" / "SKILL.md"
        skill_path.write_text("customized skill", encoding="utf-8")

        result = install_codex_skills(tmp_path)

        assert ".agents/skills/trw-deliver/SKILL.md" in result["preserved"]
        assert skill_path.read_text(encoding="utf-8") == "customized skill"

    def test_codex_merge_preserves_user_settings(self) -> None:
        merged = merge_codex_config(
            {
                "model": "gpt-5.4-mini",
                "model_reasoning_effort": "high",
                "sandbox_mode": "read-only",
                "approval_policy": "never",
                "features": {"some_feature": False},
                "mcp_servers": {"custom": {"command": "custom-mcp", "enabled": False}},
            }
        )
        assert merged["model"] == "gpt-5.4-mini"
        assert merged["model_reasoning_effort"] == "high"
        assert merged["sandbox_mode"] == "read-only"
        assert merged["approval_policy"] == "never"
        assert merged["features"]["hooks"] is False
        assert "codex_hooks" not in merged["features"]
        assert merged["features"]["some_feature"] is False
        assert "custom" in merged["mcp_servers"]
        assert merged["mcp_servers"]["custom"]["enabled"] is False
        assert merged["mcp_servers"]["trw"]["enabled"] is True
        assert "trw_session_start" in merged["mcp_servers"]["trw"]["enabled_tools"]

    def test_codex_merge_preserves_explicit_hook_opt_in(self) -> None:
        merged = merge_codex_config({"features": {"codex_hooks": True}})

        assert merged["features"]["hooks"] is True
        assert "codex_hooks" not in merged["features"]

    def test_codex_merge_preserves_current_hook_opt_in(self) -> None:
        merged = merge_codex_config({"features": {"hooks": True}})

        assert merged["features"]["hooks"] is True
        assert "codex_hooks" not in merged["features"]

    def test_codex_config_prefers_project_venv_command(self, tmp_path: Path) -> None:
        project_command = tmp_path / ".venv" / "bin" / "trw-mcp"
        project_command.parent.mkdir(parents=True)
        project_command.write_text("#!/bin/sh\n", encoding="utf-8")

        result = generate_codex_config(tmp_path)

        assert ".codex/config.toml" in result["created"]
        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        assert config["mcp_servers"]["trw"]["command"] == ".venv/bin/trw-mcp"
        assert config["mcp_servers"]["trw"]["args"] == ["--debug"]
        assert "url" not in config["mcp_servers"]["trw"]

    def test_codex_config_smart_merge_existing_file(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text(
            """
model = "gpt-5.4-mini"
project_doc_fallback_filenames = ["README.md"]

[features]
legacy_toggle = false

[mcp_servers.custom]
command = "custom-server"
enabled = false

[mcp_servers.trw]
enabled_tools = ["legacy_helper"]
disabled_tools = ["trw_old_tool"]

[mcp_servers.trw.tools.custom_helper]
enabled = true

[skills]
config = [
  { path = ".agents/skills/trw-deliver/SKILL.md", enabled = true },
]
""".strip()
            + "\n",
            encoding="utf-8",
        )

        result = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in result["updated"]
        config = tomllib.loads((codex_dir / "config.toml").read_text(encoding="utf-8"))
        assert config["model"] == "gpt-5.4-mini"
        assert config["features"]["legacy_toggle"] is False
        assert config["features"]["hooks"] is False
        assert "codex_hooks" not in config["features"]
        assert config["mcp_servers"]["custom"]["enabled"] is False
        assert config["mcp_servers"]["trw"]["enabled"] is True
        assert "trw_session_start" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "legacy_helper" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "custom_helper" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_old_tool" in config["mcp_servers"]["trw"]["disabled_tools"]
        assert "README.md" in config["project_doc_fallback_filenames"]
        assert ".codex/INSTRUCTIONS.md" not in config["project_doc_fallback_filenames"]
        assert config["model_instructions_file"] == "INSTRUCTIONS.md"
        skill_paths = [entry["path"] for entry in config["skills"]["config"]]
        assert ".agents/skills/trw-deliver" in skill_paths
        assert ".agents/skills/trw-deliver/SKILL.md" not in skill_paths

    def test_codex_config_reinstall_is_idempotent(self, tmp_path: Path) -> None:
        first = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in first["created"]

        second = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in second["updated"]

        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        fallback_files = config.get("project_doc_fallback_filenames", [])
        assert ".codex/INSTRUCTIONS.md" not in fallback_files
        assert config["model_instructions_file"] == "INSTRUCTIONS.md"
        assert "codex_hooks" not in config["features"]

        trw_tools = config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_session_start" in trw_tools
        assert trw_tools.count("trw_session_start") == 1

        skill_paths = [entry["path"] for entry in config["skills"]["config"]]
        assert len(skill_paths) == len(set(skill_paths))
