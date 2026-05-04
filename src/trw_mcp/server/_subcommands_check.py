"""check-instructions CLI subcommand handler — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat
with ``test_instruction_manifest_cli.py`` which imports both helpers.

Two helpers:
- ``_check_instructions_core`` — core logic for check-instructions (testable)
- ``_run_check_instructions`` — CLI handler that wraps the core logic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _check_instructions_core(target: Path) -> tuple[int, dict[str, list[str]]]:
    """Core logic for check-instructions, separated for testability.

    Returns:
        Tuple of (exit_code, mismatches_dict).
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.claude_md._tool_manifest import (
        resolve_exposed_tools,
        validate_instruction_manifest,
    )

    config = TRWConfig()
    exposed = resolve_exposed_tools(
        mode=config.effective_tool_exposure_mode,
        custom_list=config.tool_exposure_list,
    )

    files_to_check = ["AGENTS.md", "CLAUDE.md"]
    all_mismatches: dict[str, list[str]] = {}
    files_scanned = 0

    for filename in files_to_check:
        filepath = target / filename
        if not filepath.exists():
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("check_instructions_read_error", path=str(filepath))
            continue
        files_scanned += 1
        mismatches = validate_instruction_manifest(content, exposed)
        if mismatches:
            all_mismatches[filename] = mismatches

    logger.info(
        "check_instructions_complete",
        target=str(target),
        files_scanned=files_scanned,
        exposed_count=len(exposed),
        mismatch_files=len(all_mismatches),
    )

    exit_code = 1 if all_mismatches else 0
    return exit_code, all_mismatches


def _run_check_instructions(args: argparse.Namespace) -> None:
    """Handle the ``check-instructions`` subcommand (PRD-CORE-135-FR02).

    Scans instruction files (AGENTS.md, CLAUDE.md) for trw_* tool mentions
    and compares against the effective tool exposure list from config.
    Exits with code 1 if mismatches found, 0 if clean.
    """
    target = Path(getattr(args, "target_dir", ".")).resolve()
    exit_code, all_mismatches = _check_instructions_core(target)

    if not all_mismatches:
        print("OK: all instruction files reference only exposed tools")
        sys.exit(0)

    for filename, tools in all_mismatches.items():
        print(f"{filename}: mentions unexposed tools: {', '.join(tools)}")

    total = sum(len(v) for v in all_mismatches.values())
    print(f"\nTotal: {total} unexposed tool reference(s) in {len(all_mismatches)} file(s)")
    sys.exit(exit_code)
