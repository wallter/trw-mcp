"""Tests for the ``trw-mcp auth`` CLI subcommand integration.

Covers FR10 (standalone auth command) and the server CLI dispatch.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest


class TestAuthSubcommandParser:
    """Verify the auth subcommand is registered in the CLI parser."""

    def test_auth_subcommand_exists(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["auth", "login"])
        assert args.command == "auth"
        assert args.auth_command == "login"

    def test_auth_logout(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["auth", "logout"])
        assert args.command == "auth"
        assert args.auth_command == "logout"

    def test_auth_status(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["auth", "status"])
        assert args.command == "auth"
        assert args.auth_command == "status"

    def test_auth_no_subcommand(self) -> None:
        from trw_mcp.server._cli import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["auth"])
        assert args.command == "auth"
        assert args.auth_command is None


class TestAuthSubcommandHandler:
    """Verify the auth handler dispatches correctly."""

    def test_handler_registered(self) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        assert "auth" in SUBCOMMAND_HANDLERS

    def test_login_dispatches(self) -> None:
        from trw_mcp.server._subcommands import _run_auth

        args = argparse.Namespace(auth_command="login")
        with (
            patch("trw_mcp.cli.auth.run_auth_login", return_value=0) as mock_login,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_auth(args)
        assert exc_info.value.code == 0
        mock_login.assert_called_once()

    def test_logout_dispatches(self) -> None:
        from trw_mcp.server._subcommands import _run_auth

        args = argparse.Namespace(auth_command="logout")
        with (
            patch("trw_mcp.cli.auth.run_auth_logout", return_value=0) as mock_logout,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_auth(args)
        assert exc_info.value.code == 0
        mock_logout.assert_called_once()

    def test_status_dispatches(self) -> None:
        from trw_mcp.server._subcommands import _run_auth

        args = argparse.Namespace(auth_command="status")
        with (
            patch("trw_mcp.cli.auth.run_auth_status", return_value=0) as mock_status,
            pytest.raises(SystemExit) as exc_info,
        ):
            _run_auth(args)
        assert exc_info.value.code == 0
        mock_status.assert_called_once()

    def test_no_subcommand_shows_help(self) -> None:
        from trw_mcp.server._subcommands import _run_auth

        args = argparse.Namespace(auth_command=None)
        with pytest.raises(SystemExit) as exc_info:
            _run_auth(args)
        assert exc_info.value.code == 0


class TestExistingSubcommandsUnchanged:
    """Verify existing subcommands still work after adding auth."""

    def test_init_project_still_registered(self) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        assert "init-project" in SUBCOMMAND_HANDLERS

    def test_update_project_still_registered(self) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        assert "update-project" in SUBCOMMAND_HANDLERS

    def test_audit_still_registered(self) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        assert "audit" in SUBCOMMAND_HANDLERS

    def test_export_still_registered(self) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        assert "export" in SUBCOMMAND_HANDLERS

    def test_build_release_still_registered(self) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        assert "build-release" in SUBCOMMAND_HANDLERS
