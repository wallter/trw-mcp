"""Tests for Antigravity CLI bootstrap configuration and installers.

``TestAntigravityCliAgents`` was deleted by PRD-CORE-252-FR04. It exercised the
retired ``generate_antigravity_agents`` stub generator and asserted two things
that were drift, not contract: that every agent file contained ``mcp_trw_``
(a namespace this client's own profile does not declare) and that the explorer
stub named ``grep_search``. Antigravity now receives the bundled specialists in
``.agents/agents`` — see ``tests/test_install_agents_destinations.py`` and
``tests/test_agent_materialization_per_client.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._antigravity_cli import (
    _ANTIGRAVITY_MD_PATH,
    _antigravity_global_mcp_config_path,
    generate_antigravity_instructions,
    generate_antigravity_mcp_config,
)
from trw_mcp.bootstrap._utils import detect_ide

from ._bootstrap_test_support import fake_git_repo  # noqa: F401

# The suite-wide ``_isolate_home_dir`` autouse fixture (conftest.py) points
# ``$HOME`` at a per-test tmp dir, so this resolves under that tmp dir — never
# the operator's real ``~/.gemini/config/mcp_config.json`` (PRD-FIX-133).
_GLOBAL_MCP_REL = ".gemini/config/mcp_config.json"


@pytest.mark.unit
class TestAntigravityCliMcpConfigHardening:
    """Hardened GLOBAL mcp_config.json deep-merge and recovery (PRD-FIX-133)."""

    def test_writes_fresh_when_settings_missing(self, tmp_path: Path) -> None:
        result = generate_antigravity_mcp_config(tmp_path)
        settings = _antigravity_global_mcp_config_path()
        assert settings.is_file()
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        assert "trust" not in data["mcpServers"]["trw"]  # not part of agy's schema
        assert result["errors"] == []
        # The project directory itself receives no antigravity MCP write.
        assert not (tmp_path / ".antigravitycli" / "settings.json").exists()

    def test_write_notes_global_scope(self, tmp_path: Path) -> None:
        """FR03: every write names the file as global/cross-project."""
        result = generate_antigravity_mcp_config(tmp_path)
        warnings = result.get("warnings", [])
        assert any("global" in w.lower() and "mcp_config.json" in w for w in warnings)

    def test_preserves_unrelated_user_keys(self, tmp_path: Path) -> None:
        settings = _antigravity_global_mcp_config_path()
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

        assert any(_GLOBAL_MCP_REL in p for p in first["created"])
        assert any(_GLOBAL_MCP_REL in p for p in second.get("preserved", []))

    def test_recovers_from_invalid_json_with_backup(self, tmp_path: Path) -> None:
        settings = _antigravity_global_mcp_config_path()
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

    def test_recovers_from_non_utf8_with_backup(self, tmp_path: Path) -> None:
        """Non-UTF-8 settings must not crash (regression: UnicodeDecodeError)."""
        settings = _antigravity_global_mcp_config_path()
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_bytes(b"\xff\xfe{\x00garbage")

        result = generate_antigravity_mcp_config(tmp_path)  # must not raise
        assert result["errors"] == []
        assert any("backed up" in w for w in result.get("warnings", []))

        backup = settings.with_suffix(settings.suffix + ".bak")
        assert backup.exists()
        assert backup.read_bytes() == b"\xff\xfe{\x00garbage"

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]

    def test_recovers_from_non_object_top_level(self, tmp_path: Path) -> None:
        """A top-level JSON array is recovered + backed up, not propagated."""
        settings = _antigravity_global_mcp_config_path()
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        result = generate_antigravity_mcp_config(tmp_path)
        assert result["errors"] == []
        assert any("top-level was not a JSON object" in w for w in result.get("warnings", []))
        assert settings.with_suffix(settings.suffix + ".bak").exists()

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
