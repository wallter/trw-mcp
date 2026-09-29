"""Tests for instruction file sync behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastmcp import FastMCP

from tests._test_agents_md_support import _patched_learning_env
from tests._tools_learning_shared import instructions_sync_fn
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.learning import register_learning_tools


class TestSyncIncludesInstructionFile:
    """Integration tests verifying sync result includes instruction_file fields (FR06)."""

    def test_sync_result_has_instruction_file_fields(self, tmp_project: Path) -> None:
        """instructions sync result always includes instruction_file_synced and instruction_file_path keys."""
        with _patched_learning_env(tmp_project):
            result = instructions_sync_fn(scope="root")

        assert "instruction_file_synced" in result
        assert "instruction_file_path" in result

    def test_codex_sync_creates_instruction_file(self, tmp_project: Path) -> None:
        """instructions sync with client='codex' creates .codex/INSTRUCTIONS.md."""
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_project / ".trw"),
            patch("trw_mcp.tools.learning.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_project / ".trw"),
        ):
            server = FastMCP("test")
            register_learning_tools(server)
            result = instructions_sync_fn(scope="root", client="codex", _config=TRWConfig())

        assert result["instruction_file_synced"] is True
        assert result["instruction_file_path"] is not None
        assert (tmp_project / ".codex" / "INSTRUCTIONS.md").exists()

    def test_opencode_sync_creates_instruction_file(self, tmp_project: Path) -> None:
        """instructions sync with client='opencode' creates .opencode/INSTRUCTIONS.md."""
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_project / ".trw"),
            patch("trw_mcp.tools.learning.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_project / ".trw"),
        ):
            server = FastMCP("test")
            register_learning_tools(server)
            result = instructions_sync_fn(scope="root", client="opencode", _config=TRWConfig())

        assert result["instruction_file_synced"] is True
        assert result["instruction_file_path"] is not None
        assert (tmp_project / ".opencode" / "INSTRUCTIONS.md").exists()
