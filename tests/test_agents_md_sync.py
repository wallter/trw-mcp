"""Tests for instruction file sync behavior."""

from __future__ import annotations

import json
from pathlib import Path

from unittest.mock import patch

from fastmcp import FastMCP

from tests._test_agents_md_support import _patched_learning_env
from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.learning import register_learning_tools


class TestSyncInstructionFileIfNeeded:
    """Unit tests for _sync_instruction_file_if_needed."""

    def test_returns_false_when_instruction_path_is_none(self, tmp_project: Path) -> None:
        """No instruction_path → (False, None) returned, no files created."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(None, tmp_project, "claude-code")

        assert synced is False
        assert path is None

    def test_returns_false_when_instruction_path_is_empty(self, tmp_project: Path) -> None:
        """Empty string instruction_path → (False, None) returned."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed("", tmp_project, "claude-code")

        assert synced is False
        assert path is None

    def test_codex_instruction_file_created(self, tmp_project: Path) -> None:
        """codex client with .codex/INSTRUCTIONS.md path → file is created."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(".codex/INSTRUCTIONS.md", tmp_project, "codex")

        assert synced is True
        assert path is not None
        instructions_file = tmp_project / ".codex" / "INSTRUCTIONS.md"
        assert instructions_file.exists()
        content = instructions_file.read_text(encoding="utf-8")
        assert "trw_session_start" in content or "TRW" in content

    def test_opencode_instruction_file_created_generic(self, tmp_project: Path) -> None:
        """opencode client with .opencode/INSTRUCTIONS.md path → file is created (generic model)."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(".opencode/INSTRUCTIONS.md", tmp_project, "opencode")

        assert synced is True
        assert path is not None
        instructions_file = tmp_project / ".opencode" / "INSTRUCTIONS.md"
        assert instructions_file.exists()
        content = instructions_file.read_text(encoding="utf-8")
        assert "trw_session_start" in content or "TRW" in content

    def test_opencode_instruction_uses_model_family_from_opencode_json(self, tmp_project: Path) -> None:
        """opencode sync reads model family from opencode.json and generates correct content."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        (tmp_project / "opencode.json").write_text(json.dumps({"model": "gpt-4o"}), encoding="utf-8")

        synced, path = _sync_instruction_file_if_needed(".opencode/INSTRUCTIONS.md", tmp_project, "opencode")

        assert synced is True
        assert path is not None
        instructions_file = tmp_project / ".opencode" / "INSTRUCTIONS.md"
        assert instructions_file.exists()
        content = instructions_file.read_text(encoding="utf-8")
        assert "# TRW Instructions" in content
        assert "project-native" in content
        assert "GPT" not in content

    def test_codex_path_inferred_from_instruction_path_for_auto_client(self, tmp_project: Path) -> None:
        """auto client with .codex/ instruction_path → generates codex instructions."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(".codex/INSTRUCTIONS.md", tmp_project, "auto")

        assert synced is True
        assert path is not None
        assert (tmp_project / ".codex" / "INSTRUCTIONS.md").exists()

    def test_opencode_path_inferred_from_instruction_path_for_all_client(self, tmp_project: Path) -> None:
        """all client with .opencode/ instruction_path → generates opencode instructions."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(".opencode/INSTRUCTIONS.md", tmp_project, "all")

        assert synced is True
        assert path is not None
        assert (tmp_project / ".opencode" / "INSTRUCTIONS.md").exists()


class TestSyncIncludesInstructionFile:
    """Integration tests verifying sync result includes instruction_file fields (FR06)."""

    def test_sync_result_has_instruction_file_fields(self, tmp_project: Path) -> None:
        """trw_claude_md_sync result always includes instruction_file_synced and instruction_file_path keys."""
        with _patched_learning_env(tmp_project) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert "instruction_file_synced" in result
        assert "instruction_file_path" in result

    def test_codex_sync_creates_instruction_file(self, tmp_project: Path) -> None:
        """trw_claude_md_sync with client='codex' creates .codex/INSTRUCTIONS.md."""
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_project / ".trw"),
            patch("trw_mcp.tools.learning.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_project / ".trw"),
        ):
            server = FastMCP("test")
            register_learning_tools(server)
            tools = get_tools_sync(server)
            result = tools["trw_claude_md_sync"].fn(scope="root", client="codex")

        assert result["instruction_file_synced"] is True
        assert result["instruction_file_path"] is not None
        assert (tmp_project / ".codex" / "INSTRUCTIONS.md").exists()

    def test_opencode_sync_creates_instruction_file(self, tmp_project: Path) -> None:
        """trw_claude_md_sync with client='opencode' creates .opencode/INSTRUCTIONS.md."""
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_project / ".trw"),
            patch("trw_mcp.tools.learning.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_project / ".trw"),
        ):
            server = FastMCP("test")
            register_learning_tools(server)
            tools = get_tools_sync(server)
            result = tools["trw_claude_md_sync"].fn(scope="root", client="opencode")

        assert result["instruction_file_synced"] is True
        assert result["instruction_file_path"] is not None
        assert (tmp_project / ".opencode" / "INSTRUCTIONS.md").exists()

    def test_determine_write_targets_returns_codex_instruction_path(self, tmp_project: Path) -> None:
        """_determine_write_targets with client='codex' returns .codex/INSTRUCTIONS.md."""
        from trw_mcp.state.claude_md._agents_md import _determine_write_targets

        config = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets("codex", config, tmp_project, "root")

        assert write_claude is False
        assert instruction_path == ".codex/INSTRUCTIONS.md"

    def test_determine_write_targets_returns_opencode_instruction_path(self, tmp_project: Path) -> None:
        """_determine_write_targets with client='opencode' returns .opencode/INSTRUCTIONS.md."""
        from trw_mcp.state.claude_md._agents_md import _determine_write_targets

        config = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets(
            "opencode",
            config,
            tmp_project,
            "root",
        )

        assert write_claude is False
        assert instruction_path == ".opencode/INSTRUCTIONS.md"

    def test_determine_write_targets_returns_none_for_claude_code(self, tmp_project: Path) -> None:
        """_determine_write_targets with client='claude-code' returns empty instruction_path."""
        from trw_mcp.state.claude_md._agents_md import _determine_write_targets

        config = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets(
            "claude-code",
            config,
            tmp_project,
            "root",
        )

        assert write_claude is True
        assert instruction_path == ".claude/INSTRUCTIONS.md"
