"""CLI tests for instruction manifest checking commands."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestCheckInstructionsCLI:
    """_run_check_instructions CLI handler produces correct exit codes."""

    def test_clean_exit_code_zero(self, tmp_path: Path) -> None:
        """Exit 0 when all instruction files are clean."""
        import argparse
        from unittest.mock import patch

        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_session_start() and trw_learn().\n")

        args = argparse.Namespace(target_dir=str(tmp_path))
        mock_config = type(
            "MockConfig",
            (),
            {
                "effective_tool_exposure_mode": "all",
                "tool_exposure_list": [],
            },
        )()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _run_check_instructions

            with pytest.raises(SystemExit) as exc_info:
                _run_check_instructions(args)
            assert exc_info.value.code == 0

    def test_mismatch_exit_code_one(self, tmp_path: Path) -> None:
        """Exit 1 when instruction files reference unexposed tools."""
        import argparse
        from unittest.mock import patch

        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        args = argparse.Namespace(target_dir=str(tmp_path))
        mock_config = type(
            "MockConfig",
            (),
            {
                "effective_tool_exposure_mode": "core",
                "tool_exposure_list": [],
            },
        )()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _run_check_instructions

            with pytest.raises(SystemExit) as exc_info:
                _run_check_instructions(args)
            assert exc_info.value.code == 1

    def test_no_instruction_files_exit_zero(self, tmp_path: Path) -> None:
        """Exit 0 when no instruction files are present."""
        import argparse
        from unittest.mock import patch

        args = argparse.Namespace(target_dir=str(tmp_path))
        mock_config = type(
            "MockConfig",
            (),
            {
                "effective_tool_exposure_mode": "all",
                "tool_exposure_list": [],
            },
        )()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _run_check_instructions

            with pytest.raises(SystemExit) as exc_info:
                _run_check_instructions(args)
            assert exc_info.value.code == 0


class TestCheckInstructionsCore:
    """Test the separated core logic directly (no sys.exit)."""

    def test_returns_zero_no_files(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        mock_config = type(
            "MockConfig",
            (),
            {
                "effective_tool_exposure_mode": "all",
                "tool_exposure_list": [],
            },
        )()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _check_instructions_core

            exit_code, mismatches = _check_instructions_core(tmp_path)
            assert exit_code == 0
            assert mismatches == {}

    def test_returns_one_on_mismatch(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() here.\n")

        mock_config = type(
            "MockConfig",
            (),
            {
                "effective_tool_exposure_mode": "core",
                "tool_exposure_list": [],
            },
        )()

        with patch("trw_mcp.models.config.TRWConfig", return_value=mock_config):
            from trw_mcp.server._subcommands import _check_instructions_core

            exit_code, mismatches = _check_instructions_core(tmp_path)
            assert exit_code == 1
            assert "AGENTS.md" in mismatches
            assert "trw_build_check" in mismatches["AGENTS.md"]
