"""Tests for the config-reference CLI subcommand.

PRD-QUAL-052 Finding #24: discoverable config reference via CLI.
"""

from __future__ import annotations

import argparse

import pytest


@pytest.mark.unit
class TestConfigReference:
    """Test the config-reference subcommand output."""

    def test_config_reference_output_contains_env_vars(self, capsys: pytest.CaptureFixture[str]) -> None:
        from trw_mcp.server._subcommands import _run_config_reference

        _run_config_reference(argparse.Namespace())
        output = capsys.readouterr().out

        assert "TRW_" in output
        assert "BUILD_CHECK_ENABLED" in output

    def test_config_reference_output_has_table_headers(self, capsys: pytest.CaptureFixture[str]) -> None:
        from trw_mcp.server._subcommands import _run_config_reference

        _run_config_reference(argparse.Namespace())
        output = capsys.readouterr().out

        assert "Environment Variable" in output
        assert "Type" in output
        assert "Default" in output
        assert "Description" in output

    def test_config_reference_output_has_title(self, capsys: pytest.CaptureFixture[str]) -> None:
        from trw_mcp.server._subcommands import _run_config_reference

        _run_config_reference(argparse.Namespace())
        output = capsys.readouterr().out

        assert "TRW Configuration Reference" in output

    def test_config_reference_lists_known_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        from trw_mcp.server._subcommands import _run_config_reference

        _run_config_reference(argparse.Namespace())
        output = capsys.readouterr().out

        # Spot-check a variety of known config fields
        assert "TRW_PARALLELISM_MAX" in output
        assert "TRW_DEBUG" in output
        assert "TRW_TELEMETRY" in output
        assert "TRW_FRAMEWORK_VERSION" in output

    def test_config_reference_registered_in_handlers(self) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        assert "config-reference" in SUBCOMMAND_HANDLERS

    def test_config_reference_subparser_exists(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        parser = _build_arg_parser()
        # Parse the config-reference subcommand -- should not raise
        args = parser.parse_args(["config-reference"])
        assert args.command == "config-reference"
