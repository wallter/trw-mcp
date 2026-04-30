"""Tests for Gemini MCP config generation and merge behavior."""

from __future__ import annotations

import json

import pytest

from tests._gemini_test_support import fake_git_repo
from trw_mcp.bootstrap._gemini import _GEMINI_SETTINGS_PATH, generate_gemini_mcp_config


@pytest.mark.unit
class TestGeminiMCPConfig:
    """Test generate_gemini_mcp_config deep-merge logic."""

    def test_mcp_config_created(self, fake_git_repo) -> None:
        result = generate_gemini_mcp_config(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _GEMINI_SETTINGS_PATH).is_file()

    def test_mcp_config_has_trw_server(self, fake_git_repo) -> None:
        generate_gemini_mcp_config(fake_git_repo)
        data = json.loads((fake_git_repo / _GEMINI_SETTINGS_PATH).read_text())
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        cmd = data["mcpServers"]["trw"]["command"]
        assert cmd.endswith(("trw-mcp", "python")), f"Unexpected command: {cmd}"
        args = data["mcpServers"]["trw"]["args"]
        assert "serve" in args
        assert data["mcpServers"]["trw"]["trust"] is True

    def test_mcp_config_preserves_existing_settings(self, fake_git_repo) -> None:
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

    def test_mcp_config_preserves_other_servers(self, fake_git_repo) -> None:
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

    def test_mcp_config_creates_gemini_dir(self, fake_git_repo) -> None:
        """The .gemini directory is created if it doesn't exist."""
        result = generate_gemini_mcp_config(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / ".gemini").is_dir()

    def test_mcp_config_handles_malformed_json(self, fake_git_repo) -> None:
        """Malformed JSON in existing file is recovered: backed up + rewritten."""
        settings_path = fake_git_repo / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{broken json!!")

        result = generate_gemini_mcp_config(fake_git_repo)
        assert result["errors"] == []
        warnings = result.get("warnings", [])
        assert any("backed up" in warning for warning in warnings), warnings
        assert settings_path.with_suffix(".json.bak").exists()

    def test_mcp_config_updated_when_existing(self, fake_git_repo) -> None:
        """Re-running on existing file marks as updated."""
        settings_path = fake_git_repo / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{}")

        result = generate_gemini_mcp_config(fake_git_repo)
        assert _GEMINI_SETTINGS_PATH in result["updated"]

    def test_mcp_config_json_well_formatted(self, fake_git_repo) -> None:
        """Output JSON is indented with 2 spaces."""
        generate_gemini_mcp_config(fake_git_repo)
        raw = (fake_git_repo / _GEMINI_SETTINGS_PATH).read_text()
        assert raw.endswith("\n")
        data = json.loads(raw)
        expected = json.dumps(data, indent=2) + "\n"
        assert raw == expected
