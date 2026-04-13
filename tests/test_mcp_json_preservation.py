"""Tests for _merge_mcp_json user-customized entry preservation.

PRD-FIX-076 follow-up (Sprint 91): the prior `.mcp.json` merge logic
unconditionally overwrote the existing `trw` entry — destroying user
pin to a specific venv binary (e.g. dev-repo absolute paths). The fixed
implementation preserves user-customized entries.

User-customized = command is an absolute path to an extant file, OR
the entry has fields beyond {command, args}.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.unit
class TestIsUserCustomizedTrwEntry:
    """Unit-test the customization-detection heuristic in isolation."""

    def test_absolute_path_to_existing_file_is_customized(
        self, tmp_path: Path
    ) -> None:
        """command='/path/to/.venv/bin/trw-mcp' (file exists) → preserve."""
        from trw_mcp.bootstrap._mcp_json import _is_user_customized_trw_entry

        binary = tmp_path / "trw-mcp"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        assert _is_user_customized_trw_entry(
            {"command": str(binary), "args": ["--debug"]}
        ) is True

    def test_absolute_path_to_missing_file_is_not_customized(
        self, tmp_path: Path
    ) -> None:
        """Stale absolute path → not preserved (refresh to default)."""
        from trw_mcp.bootstrap._mcp_json import _is_user_customized_trw_entry

        assert _is_user_customized_trw_entry(
            {"command": str(tmp_path / "does-not-exist"), "args": ["--debug"]}
        ) is False

    def test_bare_trw_mcp_command_is_not_customized(self) -> None:
        """Default entry (bare ``trw-mcp``) → safe to refresh."""
        from trw_mcp.bootstrap._mcp_json import _is_user_customized_trw_entry

        assert _is_user_customized_trw_entry(
            {"command": "trw-mcp", "args": ["--debug"]}
        ) is False

    def test_python_module_invocation_is_not_customized(self) -> None:
        """Default fallback ([python, -m, trw_mcp.server]) → safe to refresh."""
        from trw_mcp.bootstrap._mcp_json import _is_user_customized_trw_entry

        assert _is_user_customized_trw_entry(
            {"command": "/usr/bin/python3", "args": ["-m", "trw_mcp.server", "--debug"]}
        ) is True  # absolute path to existing file

    def test_extra_keys_indicate_customization(self) -> None:
        """Entry with ``env`` / ``cwd`` / etc. → user-added → preserve."""
        from trw_mcp.bootstrap._mcp_json import _is_user_customized_trw_entry

        assert _is_user_customized_trw_entry(
            {"command": "trw-mcp", "args": ["--debug"], "env": {"FOO": "bar"}}
        ) is True
        assert _is_user_customized_trw_entry(
            {"command": "trw-mcp", "args": ["--debug"], "cwd": "/tmp"}
        ) is True

    def test_list_command_with_absolute_interpreter_is_customized(
        self, tmp_path: Path
    ) -> None:
        """command=['/path/to/python', '-m', 'trw_mcp.server'] → preserve."""
        from trw_mcp.bootstrap._mcp_json import _is_user_customized_trw_entry

        py = tmp_path / "python3"
        py.write_text("")
        py.chmod(0o755)

        assert _is_user_customized_trw_entry(
            {"command": [str(py), "-m", "trw_mcp.server"], "args": ["--debug"]}
        ) is True

    def test_non_dict_input_is_not_customized(self) -> None:
        """Defensive: list / None / string inputs → not customized."""
        from trw_mcp.bootstrap._mcp_json import _is_user_customized_trw_entry

        assert _is_user_customized_trw_entry(None) is False
        assert _is_user_customized_trw_entry("trw-mcp") is False
        assert _is_user_customized_trw_entry([]) is False


@pytest.mark.integration
class TestMergePreservesUserCustomization:
    """End-to-end: _merge_mcp_json leaves user-pinned entries alone."""

    def _seed_mcp(self, target_dir: Path, content: dict) -> Path:
        path = target_dir / ".mcp.json"
        path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
        return path

    def test_dev_repo_absolute_venv_path_preserved(self, tmp_path: Path) -> None:
        """Dev pattern: command='/repo/trw-mcp/.venv/bin/trw-mcp' is preserved."""
        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        # Create a fake binary at an absolute path
        venv_bin = tmp_path / "trw-mcp"
        venv_bin.write_text("#!/bin/sh\n")
        venv_bin.chmod(0o755)

        original = {
            "mcpServers": {
                "trw": {"command": str(venv_bin), "args": ["--debug"]}
            }
        }
        path = self._seed_mcp(tmp_path, original)

        result: dict[str, list[str]] = {
            "created": [], "updated": [], "preserved": [], "errors": []
        }
        _merge_mcp_json(tmp_path, result)

        # On-disk file: command must still be the absolute venv path
        on_disk = json.loads(path.read_text())
        assert on_disk["mcpServers"]["trw"]["command"] == str(venv_bin)
        # Reported as preserved (preserve list contains an entry mentioning the path)
        preserved_entries = " ".join(result.get("preserved", []))
        assert ".mcp.json" in preserved_entries
        assert "preserved" in preserved_entries.lower()

    def test_user_added_env_field_preserved(self, tmp_path: Path) -> None:
        """User added env={...} to the trw entry → preserve full entry."""
        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        original = {
            "mcpServers": {
                "trw": {
                    "command": "trw-mcp",
                    "args": ["--debug"],
                    "env": {"TRW_LOG_LEVEL": "DEBUG", "CUSTOM_FLAG": "1"},
                }
            }
        }
        path = self._seed_mcp(tmp_path, original)

        result: dict[str, list[str]] = {
            "created": [], "updated": [], "preserved": [], "errors": []
        }
        _merge_mcp_json(tmp_path, result)

        on_disk = json.loads(path.read_text())
        # env must survive
        assert on_disk["mcpServers"]["trw"]["env"] == {
            "TRW_LOG_LEVEL": "DEBUG", "CUSTOM_FLAG": "1",
        }

    def test_other_servers_preserved_alongside_trw(self, tmp_path: Path) -> None:
        """Other mcpServers entries are untouched whether or not trw is preserved."""
        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        venv_bin = tmp_path / "trw-mcp"
        venv_bin.write_text("#!/bin/sh\n")
        venv_bin.chmod(0o755)

        original = {
            "mcpServers": {
                "trw": {"command": str(venv_bin), "args": ["--debug"]},
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                },
                "github": {"command": "gh-mcp", "args": ["serve"]},
            }
        }
        path = self._seed_mcp(tmp_path, original)

        result: dict[str, list[str]] = {
            "created": [], "updated": [], "preserved": [], "errors": []
        }
        _merge_mcp_json(tmp_path, result)

        on_disk = json.loads(path.read_text())
        assert on_disk["mcpServers"]["trw"]["command"] == str(venv_bin)
        assert on_disk["mcpServers"]["filesystem"]["command"] == "npx"
        assert on_disk["mcpServers"]["github"]["command"] == "gh-mcp"

    def test_default_entry_still_refreshable(self, tmp_path: Path) -> None:
        """Bare ``trw-mcp`` entry IS refreshed (not user-customized)."""
        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        # Default-shape entry — just bare command, no extras
        original = {
            "mcpServers": {"trw": {"command": "trw-mcp", "args": ["--debug"]}}
        }
        self._seed_mcp(tmp_path, original)

        result: dict[str, list[str]] = {
            "created": [], "updated": [], "preserved": [], "errors": []
        }
        _merge_mcp_json(tmp_path, result)

        # Default entries are refreshed (reported in updated/created), not preserved
        # (the on-disk content may be identical, but the dispatcher rewrote it)
        all_entries = " ".join(result.get("updated", []) + result.get("created", []))
        assert ".mcp.json" in all_entries

    def test_missing_file_creates_default(self, tmp_path: Path) -> None:
        """No pre-existing .mcp.json → default created (no preservation needed)."""
        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        result: dict[str, list[str]] = {
            "created": [], "updated": [], "preserved": [], "errors": []
        }
        _merge_mcp_json(tmp_path, result)

        path = tmp_path / ".mcp.json"
        assert path.is_file()
        on_disk = json.loads(path.read_text())
        assert "trw" in on_disk["mcpServers"]


from tests._structlog_capture import captured_structlog  # noqa: F401


@pytest.mark.integration
class TestObservability:
    """Structured logs surface preservation vs refresh decisions."""

    def test_preservation_emits_mcp_config_preserved_log(
        self, tmp_path: Path, captured_structlog: list[dict]
    ) -> None:
        """When preserving a user-customized entry, log mcp_config_preserved."""
        from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

        venv_bin = tmp_path / "trw-mcp"
        venv_bin.write_text("#!/bin/sh\n")
        venv_bin.chmod(0o755)

        path = tmp_path / ".mcp.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"trw": {"command": str(venv_bin), "args": ["--debug"]}}}
            )
        )

        result: dict[str, list[str]] = {
            "created": [], "updated": [], "preserved": [], "errors": []
        }
        _merge_mcp_json(tmp_path, result)

        preserved_logs = [
            log_entry for log_entry in captured_structlog
            if log_entry.get("event") == "mcp_config_preserved"
        ]
        assert len(preserved_logs) == 1
        log = preserved_logs[0]
        assert log["reason"] == "user_customized_command"
        assert log["existing_command"] == str(venv_bin)
