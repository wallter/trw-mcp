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


def _printable(path: Path, target: Path) -> str:
    """A repo-relative name safe for a terminal: control characters are escaped (codex r1 KI, INC-126)."""
    name = path.relative_to(target).as_posix()
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii") for ch in name)


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
    """The instruction files under *target* that can be judged (see :func:`_carriers`)."""
    return _carriers(target)[0]


def _carriers(target: Path) -> tuple[list[Path], list[str]]:
    """``(judged files, skipped carriers)``: every instruction file present under *target*, per the registry.

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
    skipped: list[str] = []  # INC-126 (c): a carrier that exists but was not judged is never an OK
    for f in files:
        inside = _inside(f, root)
        if inside is None:
            logger.warning("check_instructions_carrier_unresolvable", path=str(f))
            skipped.append(f"{_printable(f, target)}: SKIPPED (cannot be resolved)")
        elif inside:
            kept.append(f)
        else:
            skipped.append(f"{_printable(f, target)}: SKIPPED (resolves outside the target; not read)")
    return kept, skipped


def _check_instructions_core(target: Path) -> tuple[int, dict[str, list[str]]]:
    """``(exit code, mismatches)``; see :func:`_check_instructions_report` (a skip also makes the code 1)."""
    code, mismatches, _skipped = _check_instructions_report(target)
    return code, mismatches


def _check_instructions_report(target: Path) -> tuple[int, dict[str, list[str]], list[str]]:
    """Core logic for check-instructions, separated for testability.

    Judges every instruction file the registry lists against the tool surface the
    project's own config resolves to (the surface ``trw_status(detail="surface")``
    reports), not this process's cwd config.

    Returns:
        ``(exit_code, mismatches, skipped)``. Exit 2 when *target* is not a directory; 1 on any mismatch or any
        carrier that was present but not judged (skipped or unreadable).
    """
    from trw_mcp.models.config._loader import config_for_trw_dir
    from trw_mcp.profile.explain import tool_surface_summary
    from trw_mcp.state.claude_md._tool_manifest import validate_instruction_manifest

    if not target.is_dir():
        return 2, {}, []

    surface = tool_surface_summary(config_for_trw_dir(target / ".trw"))
    exposed = frozenset(cast("list[str]", surface["tools"]))

    all_mismatches: dict[str, list[str]] = {}
    files_scanned = 0
    judged, skipped = _carriers(target)
    for filepath in judged:
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.warning("check_instructions_read_error", path=str(filepath))
            skipped.append(f"{_printable(filepath, target)}: SKIPPED (unreadable)")
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

    return (1 if all_mismatches or skipped else 0), all_mismatches, skipped


def _run_check_instructions(args: argparse.Namespace) -> None:
    """Handle the ``check-instructions`` subcommand (PRD-CORE-135-FR02).

    Scans every instruction file the client-profile registry lists (AGENTS.md,
    .trw/INSTRUCTIONS.md, per-client carriers) for trw_* tool mentions and compares
    against the project's resolved tool surface. Exits 1 if mismatches are found,
    2 if the target is not a directory, 0 if clean.
    """
    target = Path(getattr(args, "target_dir", ".")).resolve()
    if target.is_file():
        print(f"error: target is a file, not a directory: {target}", file=sys.stderr)
        sys.exit(2)
    exit_code, all_mismatches, skipped = _check_instructions_report(target)
    manifest = target / ".trw" / "managed-artifacts.yaml"
    if manifest.is_file() and _trw_written(target) is None:  # INC-126 (c): say what an unread manifest means
        print(f"note: {manifest.relative_to(target)} could not be read as a manifest, so ownership is unknown and "
              "every rule file was scanned", file=sys.stderr)  # fmt: skip

    if exit_code == 2:
        print(f"error: target directory does not exist: {target}", file=sys.stderr)
        sys.exit(2)

    if not all_mismatches and not skipped:
        if not _instruction_files(target):
            if not (target / ".trw").is_dir():  # INC-126 (c): nothing checked outside a project is not an OK
                print(f"error: not a TRW project: {target} (no .trw/ and no instruction files); nothing was checked",
                      file=sys.stderr)  # fmt: skip
                sys.exit(2)
            print(f"OK: no instruction files found under {target} (nothing to check)")
            sys.exit(0)
        print("OK: all instruction files reference only exposed tools")
        sys.exit(0)

    for filename, tools in all_mismatches.items():
        print(f"{_printable(target / filename, target)}: mentions unexposed tools: {', '.join(tools)}")
    for line in skipped:
        print(line)

    total = sum(len(v) for v in all_mismatches.values())
    print(
        f"\nTotal: {total} unexposed tool reference(s) in {len(all_mismatches)} file(s); {len(skipped)} carrier(s) not checked"
    )
    sys.exit(exit_code)
