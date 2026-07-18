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

from trw_mcp.server._subcommands_uninstall_config import (
    _remove_managed_block_file as _remove_managed_block_file,
)
from trw_mcp.server._subcommands_uninstall_config import (
    _strip_managed_blocks as _strip_managed_blocks,
)
from trw_mcp.server._subcommands_uninstall_config import (
    _strip_trw_from_merged_config as _strip_trw_from_merged_config,
)

# Subpaths of a project ``.trw`` dir that hold the durable learning corpus.
# ``--keep-memory`` preserves these; the blast-radius warning is gated on them.
# ``memory.db`` is the authoritative SQLite store (a FILE, not the memory/ dir),
# so it MUST be preserved alongside the learning entry files.
_MEMORY_SUBPATHS: tuple[str, ...] = ("memory", "memory.db", "learnings")


def _count_learnings(trw_dir: Path) -> int:
    """Best-effort count of learning entry files under ``.trw/learnings``.

    Counts ``.yaml`` files under ``learnings/`` (and its ``entries/`` subdir),
    excluding the ``index.yaml`` seed. A missing directory yields 0. This is a
    rough blast-radius figure for the destructive-uninstall warning, not an
    exact corpus size (the authoritative store is ``memory.db``).
    """
    learnings = trw_dir / "learnings"
    if not learnings.is_dir():
        return 0
    return sum(1 for p in learnings.rglob("*.yaml") if p.is_file() and p.name != "index.yaml")


def _trw_corpus_blast_radius(trw_dir: Path) -> tuple[bool, int]:
    """Return ``(has_corpus, learning_count)`` for a project ``.trw`` dir.

    ``has_corpus`` is True when ``memory.db`` exists OR any learning entry
    files are present — i.e. removing this dir would permanently destroy the
    accumulated learning corpus. Used to gate the destructive-uninstall
    warning + export nudge.
    """
    has_db = (trw_dir / "memory.db").is_file()
    count = _count_learnings(trw_dir)
    return (has_db or count > 0), count


def _keep_memory_in_dir(trw_dir: Path, target: Path) -> tuple[int, int]:
    """Remove everything under *trw_dir* EXCEPT memory/ and learnings/.

    Implements ``--keep-memory``: the durable learning corpus
    (``.trw/memory`` + ``.trw/learnings``) is preserved while all other
    session/config state is removed. Returns ``(removed, errors)`` counts of
    top-level entries. The ``.trw`` dir itself is preserved (it still holds
    the corpus).
    """
    import shutil

    removed = 0
    errors = 0
    preserved = {trw_dir / name for name in _MEMORY_SUBPATHS}
    for child in sorted(trw_dir.iterdir()):
        # Preserve the corpus dirs/files plus SQLite sidecars (memory.db-wal /
        # memory.db-shm) so the kept DB reopens cleanly.
        if child in preserved or child.name.startswith("memory.db"):
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
            print(f"  Removed: {_display(child, target)}")
        except OSError as exc:
            errors += 1
            print(f"  Error removing {_display(child, target)}: {exc}")
    return removed, errors


def _run_uninstall(args: argparse.Namespace) -> None:
    """Handle the ``uninstall`` subcommand -- remove TRW files from a project.

    Registry-driven (PRD-SEC-006 FR07): the set of surfaces is derived from the
    client-profile registry manifest (all 9 profiles + framework core), so
    every profile is cleaned, not just claude-code. Shared files (CLAUDE.md,
    AGENTS.md, GEMINI.md, settings.json, copilot-instructions) have only their
    TRW-managed marker block removed; only artifacts TRW created are touched.
    With ``--user-tier`` the machine-local ``~/.trw`` store is also removed.
    """
    import shutil

    from trw_mcp.client_profiles.catalog import uninstall_surfaces

    target = Path(getattr(args, "target_dir", ".")).resolve()
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)
    user_tier: bool = getattr(args, "user_tier", False)
    keep_memory: bool = getattr(args, "keep_memory", False)

    # Blast-radius detection for the project .trw learning corpus. Removing
    # .trw wholesale permanently destroys memory.db + all learnings, so we warn
    # explicitly + nudge an export-first when a corpus is present.
    project_trw = (target / ".trw").resolve()
    has_corpus, learning_count = _trw_corpus_blast_radius(project_trw) if project_trw.is_dir() else (False, 0)

    plain_paths: list[Path] = []
    managed_paths: list[Path] = []
    merged_config_paths: list[tuple[Path, str]] = []
    for surface in uninstall_surfaces():
        path = (target / surface.relpath).resolve()
        if not path.exists():
            continue
        if surface.merged_config:
            merged_config_paths.append((path, surface.config_shape))
        elif surface.managed_block:
            managed_paths.append(path)
        else:
            plain_paths.append(path)

    user_trw = (Path.home() / ".trw").resolve()
    remove_user_trw = user_tier and user_trw.exists() and user_trw not in plain_paths

    if not plain_paths and not managed_paths and not merged_config_paths and not remove_user_trw:
        print("  No TRW files found in this project.")
        return

    print(f"\n  TRW files found in {target}:\n")
    for p in plain_paths:
        kind = "dir " if p.is_dir() else "file"
        size = ""
        if p.is_dir():
            count = sum(1 for _ in p.rglob("*") if _.is_file())
            size = f" ({count} files)"
        note = ""
        if p == project_trw and keep_memory and has_corpus:
            note = " — memory/ + learnings/ PRESERVED (--keep-memory)"
        print(f"    {kind}  {_display(p, target)}{size}{note}")
    for p in managed_paths:
        print(f"    block {_display(p, target)} (TRW-managed section)")
    for p, _shape in merged_config_paths:
        print(f"    entry {_display(p, target)} (TRW entries only)")
    if remove_user_trw:
        print(f"    dir   {user_trw} (user-tier)")

    # Destructive blast-radius warning: removing project .trw without
    # --keep-memory permanently deletes memory.db + every learning. TRW's whole
    # value is durable learnings, so name the blast radius + nudge export-first.
    corpus_at_risk = has_corpus and not keep_memory
    if corpus_at_risk:
        _print_corpus_warning(project_trw, learning_count, target)

    if dry_run:
        for p in managed_paths:
            _remove_managed_block_file(p, dry_run=True)
        for p, shape in merged_config_paths:
            _strip_trw_from_merged_config(p, dry_run=True, shape=shape)
        print("\n  --dry-run: no files removed.")
        return

    if not yes:
        print()
        prompt = (
            "  Permanently delete the learning corpus? [y/N] " if corpus_at_risk else "  Remove these files? [y/N] "
        )
        confirm = input(prompt).strip().lower()
        if confirm not in ("y", "yes"):
            print("  Aborted.")
            return

    removed = 0
    errors = 0
    for p in plain_paths:
        # --keep-memory: preserve the learning corpus inside project .trw while
        # removing all other session/config state under it.
        if p == project_trw and keep_memory and has_corpus:
            kept_removed, kept_errors = _keep_memory_in_dir(p, target)
            removed += kept_removed
            errors += kept_errors
            print(f"  Kept: {_display(p, target)}/memory + learnings (--keep-memory)")
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
            print(f"  Removed: {_display(p, target)}")
        except OSError as exc:
            errors += 1
            print(f"  Error removing {_display(p, target)}: {exc}")

    for p in managed_paths:
        try:
            status = _remove_managed_block_file(p, dry_run=False)
        except OSError as exc:
            errors += 1
            print(f"  Error updating {_display(p, target)}: {exc}")
            continue
        if status == "removed":
            removed += 1
            print(f"  Removed: {_display(p, target)} (TRW-only file)")
        elif status == "stripped":
            removed += 1
            print(f"  Cleaned: {_display(p, target)} (removed TRW section)")

    for p, shape in merged_config_paths:
        try:
            status = _strip_trw_from_merged_config(p, dry_run=False, shape=shape)
        except OSError as exc:
            errors += 1
            print(f"  Error updating {_display(p, target)}: {exc}")
            continue
        if status == "removed":
            removed += 1
            print(f"  Removed: {_display(p, target)} (TRW-only file)")
        elif status == "stripped":
            removed += 1
            print(f"  Cleaned: {_display(p, target)} (removed TRW entries)")
        elif status == "skipped":
            print(f"  Preserved: {_display(p, target)} (unparseable; left untouched)")

    if remove_user_trw:
        try:
            shutil.rmtree(user_trw)
            removed += 1
            print(f"  Removed: {user_trw} (user-tier)")
        except OSError as exc:
            errors += 1
            print(f"  Error removing {user_trw}: {exc}")

    print(f"\n  Done. Removed {removed} item(s).")
    if not user_tier and user_trw.exists():
        print("  Note: ~/.trw (user-tier store) preserved. Re-run with --user-tier to remove it.")
    print("  To uninstall the package itself: pip uninstall trw-mcp trw-memory")
    if errors:
        # Truthful exit status: a partial uninstall must not report success to
        # scripted callers (`trw-mcp uninstall --yes && ...`).
        print(f"  {errors} item(s) could not be removed — see errors above.", file=sys.stderr)
        raise SystemExit(1)


def _print_corpus_warning(trw_dir: Path, learning_count: int, target: Path) -> None:
    """Print the destructive-uninstall blast-radius warning + export nudge.

    Names exactly what is about to be permanently destroyed (memory.db + the
    learning count) and nudges an export-first, since the learning corpus is
    TRW's core durable value and cannot be recovered after rmtree.
    """
    has_db = (trw_dir / "memory.db").is_file()
    pieces: list[str] = []
    if has_db:
        pieces.append("memory.db")
    if learning_count > 0:
        pieces.append(f"{learning_count} learning(s)")
    blast = " and ".join(pieces) if pieces else "the learning corpus"
    rel = _display(trw_dir, target)
    print()
    print("  WARNING: this permanently deletes your learning corpus.")
    print(f"    {rel} contains {blast} — removing it CANNOT be undone.")
    print("    Export first:  trw-mcp export --scope learnings --output learnings.json")
    print("    Or keep it:    re-run with --keep-memory to preserve memory/ + learnings/.")


def _display(path: Path, target: Path) -> str:
    """Render *path* relative to *target* when possible, else absolute."""
    try:
        return str(path.relative_to(target))
    except ValueError:
        return str(path)


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
