"""Split bootstrap branch coverage for MCP and metadata helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap import (
    _generate_mcp_json,
    _get_bundled_names,
    _merge_mcp_json,
    _trw_mcp_server_entry,
    _verify_installation,
    _write_installer_metadata,
)

from ._bootstrap_test_support import fake_git_repo, initialized_repo


@pytest.mark.unit
class TestMergeMcpJson:
    """Cover _merge_mcp_json edge cases."""

    def test_corrupt_mcp_json_treated_as_empty(self, tmp_path: Path) -> None:
        """Corrupt .mcp.json is treated as empty dict, trw entry added."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text("INVALID JSON {{{{", encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]

    def test_mcp_json_mcpservers_not_dict(self, tmp_path: Path) -> None:
        """mcpServers is not a dict → replaced with dict containing trw."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": "invalid"}), encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]

    def test_existing_trw_key_reported_as_key(self, tmp_path: Path) -> None:
        """Existing 'trw' key is updated — result key matches result dict."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"trw": {"command": "old", "args": []}}}), encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _merge_mcp_json(tmp_path, result)
        assert any(".mcp.json" in u for u in result["updated"])

    def test_write_error_existing_mcp_json(self, tmp_path: Path) -> None:
        """OSError writing merged .mcp.json → error."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "errors": []}
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _merge_mcp_json(tmp_path, result)

        assert any("Failed to write" in e for e in result["errors"])

    def test_write_error_new_mcp_json(self, tmp_path: Path) -> None:
        """OSError creating new .mcp.json → error."""
        result: dict[str, list[str]] = {"created": [], "errors": []}
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _merge_mcp_json(tmp_path, result)

        assert any("Failed to write" in e for e in result["errors"])

    def test_created_key_used_when_no_updated(self, tmp_path: Path) -> None:
        """When result has 'created' but not 'updated', 'created' key is used."""
        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)
        assert any(".mcp.json" in c for c in result["created"])


@pytest.mark.unit
class TestWriteInstallerMetadata:
    """Cover _write_installer_metadata error path."""

    def test_oserror_adds_to_errors(self, tmp_path: Path) -> None:
        """OSError writing metadata adds to errors."""
        result: dict[str, list[str]] = {"created": [], "errors": []}

        with patch("trw_mcp.state.persistence.FileStateWriter.write_yaml", side_effect=OSError("disk full")):
            _write_installer_metadata(tmp_path, "init-project", result)

        assert any("Failed to write" in e for e in result["errors"])

    def test_writes_metadata_on_init(self, fake_git_repo: Path) -> None:
        """installer-meta.yaml is created with correct action."""
        result: dict[str, list[str]] = {"created": [], "errors": []}
        _write_installer_metadata(fake_git_repo, "init-project", result)

        assert any("installer-meta" in c for c in result["created"])


@pytest.mark.unit
class TestVerifyInstallation:
    """Cover all _verify_installation branches."""

    def test_non_executable_hook_warns(self, tmp_path: Path) -> None:
        """Non-executable hook → warning."""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "test-hook.sh"
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
        hook.chmod(0o644)

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("not executable" in w for w in result["warnings"])

    def test_missing_mcp_json_warns(self, tmp_path: Path) -> None:
        """Missing .mcp.json → warning."""
        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any(".mcp.json not found" in w for w in result["warnings"])

    def test_mcp_json_missing_trw_entry_warns(self, tmp_path: Path) -> None:
        """mcp.json without 'trw' key → warning."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"other": {}}}), encoding="utf-8")

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("missing 'trw'" in w for w in result["warnings"])

    def test_invalid_mcp_json_warns(self, tmp_path: Path) -> None:
        """Invalid JSON in .mcp.json → warning."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text("INVALID", encoding="utf-8")

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("not valid JSON" in w for w in result["warnings"])

    def test_claude_md_missing_markers_warns(self, tmp_path: Path) -> None:
        """CLAUDE.md without TRW markers → warning."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"trw": {}}}), encoding="utf-8")
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n", encoding="utf-8")

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("missing TRW" in w for w in result["warnings"])

    def test_healthy_install_no_warnings(self, initialized_repo: Path) -> None:
        """Healthy install produces no verification warnings."""
        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(initialized_repo, result)

        health_warnings = [
            w for w in result["warnings"] if "not executable" in w or "missing" in w.lower() or "not valid" in w
        ]
        assert len(health_warnings) == 0


@pytest.mark.unit
class TestGenerateMcpJson:
    """Cover _generate_mcp_json legacy helper."""

    def test_returns_valid_json(self) -> None:
        """Returns valid JSON string with trw entry."""
        result_str = _generate_mcp_json()
        data = json.loads(result_str)
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        assert "command" in data["mcpServers"]["trw"]

    def test_ends_with_newline(self) -> None:
        """Generated JSON ends with newline."""
        result_str = _generate_mcp_json()
        assert result_str.endswith("\n")


@pytest.mark.unit
class TestTrwMcpServerEntry:
    """Cover _trw_mcp_server_entry."""

    def test_returns_entry_with_command(self) -> None:
        """Returns dict with command and args."""
        entry = _trw_mcp_server_entry()
        assert "command" in entry
        assert "args" in entry

    def test_falls_back_to_sys_executable_when_no_which(self) -> None:
        """Falls back to sys.executable -m when trw-mcp not in PATH."""
        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            entry = _trw_mcp_server_entry()
        assert entry["command"] == sys.executable
        assert "-m" in entry["args"]  # type: ignore[operator]


@pytest.mark.unit
class TestGetBundledNames:
    """Cover _get_bundled_names."""

    def test_returns_expected_categories(self) -> None:
        """Returns dict with skills, agents, hooks keys."""
        names = _get_bundled_names()
        assert "skills" in names
        assert "agents" in names
        assert "hooks" in names

    def test_returns_lists(self) -> None:
        """All values are lists."""
        names = _get_bundled_names()
        assert isinstance(names["skills"], list)
        assert isinstance(names["agents"], list)
        assert isinstance(names["hooks"], list)


@pytest.mark.unit
class TestTrwMcpServerEntrySystemCmd:
    """Portable command generation — always bare names, never absolute paths."""

    def test_returns_bare_trw_mcp_when_on_path(self) -> None:
        """When shutil.which finds trw-mcp, return portable bare command."""
        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/usr/local/bin/trw-mcp"
            entry = _trw_mcp_server_entry()

        assert entry["command"] == "trw-mcp"
        assert not str(entry["command"]).startswith("/")

    def test_bare_command_over_python_m_fallback(self) -> None:
        """Bare trw-mcp takes priority over python -m fallback."""
        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/opt/homebrew/bin/trw-mcp"
            entry = _trw_mcp_server_entry()

        assert entry["command"] == "trw-mcp"
        assert "trw_mcp.server" not in str(entry["command"])
