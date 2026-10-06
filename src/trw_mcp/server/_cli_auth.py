"""The ``trw-mcp auth`` subcommand: its parser and its dispatch.

Belongs to the ``_cli_argparse.py`` (parser) and ``_subcommands_lifecycle.py`` (``_run_auth``)
facades; extracted to keep both under the 350 effective-LOC gate. ``login --machine`` /
``--no-machine``, ``logout --machine`` and ``promote`` manage the computer-wide sign-in
(``~/.trw/credentials.yaml``, see ``models/config/_credentials.py``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def add_auth_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``auth`` and its subcommands on *subparsers*."""
    auth_parser = subparsers.add_parser(
        "auth",
        help="Manage platform authentication",
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_command")
    _API_URL_HELP = "Override API URL (default: from config or https://api.trwframework.com)"
    login_parser = auth_sub.add_parser("login", help="Authenticate via device authorization flow")
    login_parser.add_argument("--api-url", default=None, help=_API_URL_HELP)
    login_machine = login_parser.add_mutually_exclusive_group()
    login_machine.add_argument(
        "--machine",
        dest="machine",
        action="store_const",
        const=True,
        default=None,
        help="Save the key for every project on this computer (~/.trw/credentials.yaml, 0600) without asking",
    )
    login_machine.add_argument(
        "--no-machine",
        dest="machine",
        action="store_const",
        const=False,
        help="Keep the key in this project's .trw/credentials.yaml only, without asking",
    )
    logout_parser = auth_sub.add_parser("logout", help="Remove stored API key")
    logout_parser.add_argument("--api-url", default=None, help=_API_URL_HELP)
    logout_parser.add_argument(
        "--machine",
        action="store_true",
        help="Remove the computer-wide key (~/.trw/credentials.yaml) instead of this project's",
    )
    auth_sub.add_parser(
        "promote",
        help="Copy this project's key to ~/.trw/credentials.yaml so every project on this computer uses it",
    )
    status_parser = auth_sub.add_parser("status", help="Show current authentication status")
    status_parser.add_argument("--api-url", default=None, help=_API_URL_HELP)


def run_auth(args: argparse.Namespace) -> None:
    """Handle the ``auth`` subcommand (login/logout/status/promote)."""
    from trw_mcp.cli._auth_machine import run_auth_promote, run_machine_logout
    from trw_mcp.cli.auth import run_auth_login, run_auth_logout, run_auth_status

    config_path = Path.cwd() / ".trw" / "config.yaml"
    api_url = getattr(args, "api_url", None) or "https://api.trwframework.com"

    auth_cmd = getattr(args, "auth_command", None)
    if auth_cmd == "login":
        sys.exit(run_auth_login(api_url, config_path, getattr(args, "machine", None)))
    elif auth_cmd == "logout":
        sys.exit(run_machine_logout() if getattr(args, "machine", False) else run_auth_logout(config_path))
    elif auth_cmd == "promote":
        sys.exit(run_auth_promote(config_path))
    elif auth_cmd == "status":
        sys.exit(run_auth_status(config_path, api_url))
    else:
        # No auth subcommand: show help
        print("Usage: trw-mcp auth {login|logout|status|promote}")
        print()
        print("Commands:")
        print("  login    Authenticate via device authorization flow")
        print("  logout   Remove stored API key (--machine: the computer-wide one)")
        print("  status   Show current authentication status")
        print("  promote  Use this project's key for every project on this computer")
        sys.exit(0)
