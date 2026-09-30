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
from typing import cast

import structlog

logger = structlog.get_logger(__name__)


_TEXT_SUFFIXES = frozenset({".md", ".mdc"})


def _inside(path: Path, root: Path) -> bool | None:
    """Whether *path* resolves inside *root*; ``None`` when it cannot be resolved (a symlink loop raises
    ``RuntimeError`` or ``OSError`` depending on the Python version -- codex r3 KI)."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):  # trw-fail-silent-allow: None is the answer "cannot resolve"; callers log it
        return None


def _trw_written(target: Path) -> frozenset[str] | None:
    """Repo-relative paths the target's ``managed-artifacts.yaml`` records as TRW-written, or ``None`` (no manifest).

    Codex r1 KI (E2E-CHECK-INSTRUCTIONS): a user's own ``.cursor/rules/*.mdc`` is not TRW's instruction text.
    """
    from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _manifest_key_path, _read_manifest

    manifest = target / ".trw" / "managed-artifacts.yaml"
    inside = _inside(manifest, target)
    if inside is None:
        logger.warning("check_instructions_manifest_unresolvable", path=str(manifest))
    if not inside:  # codex r2/r3 KI: an outside or unresolvable manifest is "ownership unknown" (scan all)
        return None
    hashes = _manifest_content_hashes(_read_manifest(target))
    # Codex r2 KI: a malformed or legacy manifest reads as an EMPTY map; that is "ownership unknown" (scan all),
    # never "TRW owns nothing" -- the latter would silently skip every rule file (the INC-069 false pass).
    return frozenset(_manifest_key_path(key) for key in hashes) if hashes else None


def _instruction_files(target: Path) -> list[Path]:
    """Every instruction file present under *target*, per the client-profile registry.

    A registry DIRECTORY (``.cursor/rules``) contributes only the files the target's manifest records as
    TRW-written; with no manifest, ownership is unknown and every text file is scanned (a false positive beats
    the false pass of E2E-INC-069). Nothing is read through a symlink that resolves outside *target* (codex r1 KI).
    """
    from trw_mcp.client_profiles.catalog import instruction_surface_relpaths

    root = target.resolve()
    written = _trw_written(target)
    files: list[Path] = []
    for relpath in instruction_surface_relpaths():
        path = target / relpath
        if path.is_dir():
            files.extend(
                candidate
                for candidate in sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in _TEXT_SUFFIXES)
                if written is None or candidate.relative_to(target).as_posix() in written
            )
        elif path.is_file():
            files.append(path)
    kept: list[Path] = []
    for f in files:
        inside = _inside(f, root)
        if inside is None:
            logger.warning("check_instructions_carrier_unresolvable", path=str(f))
        elif inside:
            kept.append(f)
    return kept


def _check_instructions_core(target: Path) -> tuple[int, dict[str, list[str]]]:
    """Core logic for check-instructions, separated for testability.

    Judges every instruction file the registry lists against the tool surface the
    project's own config resolves to (the surface ``trw_status(detail="surface")``
    reports), not this process's cwd config.

    Returns:
        Tuple of (exit_code, mismatches_dict). Exit 2 when *target* is not a directory.
    """
    from trw_mcp.models.config._loader import config_for_trw_dir
    from trw_mcp.profile.explain import tool_surface_summary
    from trw_mcp.state.claude_md._tool_manifest import validate_instruction_manifest

    if not target.is_dir():
        return 2, {}

    surface = tool_surface_summary(config_for_trw_dir(target / ".trw"))
    exposed = frozenset(cast("list[str]", surface["tools"]))

    all_mismatches: dict[str, list[str]] = {}
    files_scanned = 0
    for filepath in _instruction_files(target):
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("check_instructions_read_error", path=str(filepath))
            continue
        files_scanned += 1
        mismatches = validate_instruction_manifest(content, exposed)
        if mismatches:
            all_mismatches[filepath.relative_to(target).as_posix()] = mismatches

    logger.info(
        "check_instructions_complete",
        target=str(target),
        files_scanned=files_scanned,
        exposed_count=len(exposed),
        mismatch_files=len(all_mismatches),
    )

    return (1 if all_mismatches else 0), all_mismatches


def _run_check_instructions(args: argparse.Namespace) -> None:
    """Handle the ``check-instructions`` subcommand (PRD-CORE-135-FR02).

    Scans every instruction file the client-profile registry lists (AGENTS.md,
    .trw/INSTRUCTIONS.md, per-client carriers) for trw_* tool mentions and compares
    against the project's resolved tool surface. Exits 1 if mismatches are found,
    2 if the target is not a directory, 0 if clean.
    """
    target = Path(getattr(args, "target_dir", ".")).resolve()
    exit_code, all_mismatches = _check_instructions_core(target)

    if exit_code == 2:
        print(f"error: target directory does not exist: {target}", file=sys.stderr)
        sys.exit(2)

    if not all_mismatches:
        if not _instruction_files(target):
            print(f"OK: no instruction files found under {target} (nothing to check)")
            sys.exit(0)
        print("OK: all instruction files reference only exposed tools")
        sys.exit(0)

    for filename, tools in all_mismatches.items():
        print(f"{filename}: mentions unexposed tools: {', '.join(tools)}")

    total = sum(len(v) for v in all_mismatches.values())
    print(f"\nTotal: {total} unexposed tool reference(s) in {len(all_mismatches)} file(s)")
    sys.exit(exit_code)
