"""Misc CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat
with test imports (``test_config_reference.py``).

Two handlers:
- ``_run_config_reference`` — print config env vars (markdown table)
- ``_run_local`` — offline ceremony fallback (PRD-FIX-073) — init/checkpoint
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _run_config_reference(args: argparse.Namespace) -> None:
    """Handle the ``config-reference`` subcommand -- print config env vars."""
    from trw_mcp.models.config._main_fields import _TRWConfigFields

    print("# TRW Configuration Reference\n")
    print("All values can be set via environment variables with `TRW_` prefix.\n")
    print("| Environment Variable | Type | Default | Description |")
    print("|---------------------|------|---------|-------------|")

    for name, field_info in _TRWConfigFields.model_fields.items():
        env_var = f"TRW_{name.upper()}"
        annotation = field_info.annotation
        field_type = str(annotation).replace("typing.", "").replace("<class '", "").replace("'>", "")
        default = field_info.default if field_info.default is not None else ""
        # Truncate long defaults
        default_str = str(default)
        if len(default_str) > 40:
            default_str = default_str[:37] + "..."
        desc = field_info.description or ""
        print(f"| `{env_var}` | {field_type} | `{default_str}` | {desc} |")


def _run_local(args: argparse.Namespace) -> None:
    """Handle the ``local`` subcommand — offline ceremony fallback (PRD-FIX-073)."""
    from trw_mcp.services.orchestration_service import (
        mark_local_delivered,
        read_local_status,
        scaffold_run_directory,
        write_checkpoint,
        write_local_learning,
    )

    local_cmd = getattr(args, "local_command", None)

    if local_cmd == "init":
        task_name = getattr(args, "task", None)
        if not task_name:
            print("Error: --task is required for 'local init'")
            sys.exit(1)
        init_result = scaffold_run_directory(task_name)
        print(f"Run initialized: {init_result['run_id']}")
        print(f"  Path: {init_result['run_path']}")
    elif local_cmd == "checkpoint":
        message = getattr(args, "message", "") or ""
        run_path_str = getattr(args, "run_path", None)
        run_path = Path(run_path_str) if run_path_str else None
        try:
            cp_result = write_checkpoint(message, run_path=run_path)
            print(f"Checkpoint created at {cp_result['timestamp']}")
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    elif local_cmd == "status":
        run_path_str = getattr(args, "run_path", None)
        run_path = Path(run_path_str) if run_path_str else None
        try:
            status = read_local_status(run_path=run_path)
            print(f"Run: {status['run_id']}")
            print(f"  Task: {status['task']}")
            print(f"  Status: {status['status']}")
            print(f"  Phase: {status['phase']}")
            print(f"  Checkpoints: {status['checkpoints']}")
            print(f"  Events: {status['events']}")
            print(f"  Path: {status['run_path']}")
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    elif local_cmd == "learn":
        tags = list(getattr(args, "tag", []) or [])
        try:
            result = write_local_learning(
                summary=str(getattr(args, "summary", "")),
                detail=str(getattr(args, "detail", "")),
                tags=tags,
            )
            print(f"Learning {result.get('status', 'saved')}: {result.get('id', result.get('learning_id', 'unknown'))}")
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    elif local_cmd == "deliver":
        run_path_str = getattr(args, "run_path", None)
        run_path = Path(run_path_str) if run_path_str else None
        try:
            status = mark_local_delivered(str(getattr(args, "message", "") or "local delivery"), run_path=run_path)
            print(f"Run delivered: {status['run_id']}")
            print(f"  Path: {status['run_path']}")
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    else:
        print("Usage: trw-mcp local {init|checkpoint|status|learn|deliver}")
        print()
        print("Commands:")
        print("  init        Create a run directory (--task NAME required)")
        print("  checkpoint  Save progress (--message MSG)")
        print("  status      Show active local run status")
        print("  learn       Persist a learning (--summary, --detail)")
        print("  deliver     Mark active run delivered (--message MSG)")
        sys.exit(0)

    sys.exit(0)
