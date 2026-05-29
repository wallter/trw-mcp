"""Lifecycle CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat
with test imports (``test_uninstall.py``, ``test_cli_auth_subcommand.py``).

Two handlers:
- ``_run_uninstall`` — remove TRW files from a project (uninstall subcommand)
- ``_run_auth`` — login/logout/status auth subcommand dispatch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _run_uninstall(args: argparse.Namespace) -> None:
    """Handle the ``uninstall`` subcommand -- remove TRW files from a project."""
    import shutil

    target = Path(getattr(args, "target_dir", ".")).resolve()
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)

    # Files and directories created by init-project
    paths_to_remove: list[Path] = [
        target / ".trw",
        target / ".mcp.json",
        target / ".claude" / "skills",
        target / ".claude" / "agents",
        target / ".claude" / "hooks",
    ]

    # Find what exists
    existing = [p for p in paths_to_remove if p.exists()]

    if not existing:
        print("  No TRW files found in this project.")
        return

    print(f"\n  TRW files found in {target}:\n")
    for p in existing:
        kind = "dir " if p.is_dir() else "file"
        size = ""
        if p.is_dir():
            count = sum(1 for _ in p.rglob("*") if _.is_file())
            size = f" ({count} files)"
        print(f"    {kind}  {p.relative_to(target)}{size}")

    if dry_run:
        print("\n  --dry-run: no files removed.")
        return

    if not yes:
        print()
        confirm = input("  Remove these files? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("  Aborted.")
            return

    # Remove
    removed = 0
    for p in existing:
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
            print(f"  Removed: {p.relative_to(target)}")
        except OSError as exc:
            print(f"  Error removing {p.relative_to(target)}: {exc}")

    print(f"\n  Done. Removed {removed} item(s).")


def _run_auth(args: argparse.Namespace) -> None:
    """Handle the ``auth`` subcommand (login/logout/status)."""
    from trw_mcp.cli.auth import run_auth_login, run_auth_logout, run_auth_status

    config_path = Path.cwd() / ".trw" / "config.yaml"
    api_url = getattr(args, "api_url", None) or "https://api.trwframework.com"

    auth_cmd = getattr(args, "auth_command", None)
    if auth_cmd == "login":
        sys.exit(run_auth_login(api_url, config_path))
    elif auth_cmd == "logout":
        sys.exit(run_auth_logout(config_path))
    elif auth_cmd == "status":
        sys.exit(run_auth_status(config_path, api_url))
    else:
        # No auth subcommand: show help
        print("Usage: trw-mcp auth {login|logout|status}")
        print()
        print("Commands:")
        print("  login   Authenticate via device authorization flow")
        print("  logout  Remove stored API key")
        print("  status  Show current authentication status")
        sys.exit(0)
