"""Channel manifest hygiene CLI — trw-mcp channel-doctor sub-command.

Provides four sub-commands (argparse dispatched from _subcommands.py):

  trw-mcp channel-doctor init      — create .trw/channels/ + empty manifest
  trw-mcp channel-doctor validate  — validate manifest.yaml schema (exit 1 on error)
  trw-mcp channel-doctor scan      — list orphaned locks + stale state files
  trw-mcp channel-doctor clean     — remove orphaned locks older than --max-age-hours

PRD-DIST-2400 FR18.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

__all__ = [
    "run_channel_doctor",
]

_DEFAULT_MANIFEST_PATH = Path(".trw/channels/manifest.yaml")
_DEFAULT_CHANNELS_DIR = Path(".trw/channels")
_DEFAULT_MAX_AGE_HOURS = 24
_LOCK_SUFFIX = ".lock"
_STATE_SUFFIX = ".state.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_manifest(channels_dir: Path) -> Path:
    return channels_dir / "manifest.yaml"


def _run_init(args: argparse.Namespace, channels_dir: Path) -> None:
    """Create .trw/channels/ directory and empty manifest if absent."""
    manifest_path = _find_manifest(channels_dir)
    if manifest_path.exists():
        print(f"OK: manifest already exists at {manifest_path}")
        return
    try:
        from trw_mcp.channels._manifest_loader import auto_recreate_empty

        auto_recreate_empty(manifest_path)
        print(f"Created: {manifest_path}")
    except Exception as exc:
        print(f"ERROR: could not create manifest: {exc}", file=sys.stderr)
        sys.exit(1)


def _run_validate(args: argparse.Namespace, channels_dir: Path) -> None:
    """Validate manifest YAML schema; exit 1 on error."""
    manifest_path = _find_manifest(channels_dir)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)
    try:
        from trw_mcp.channels._manifest_loader import load

        manifest = load(manifest_path)
    except Exception as exc:  # covers ManifestMissingError + ManifestValidationError
        print(f"ERROR: manifest invalid: {exc}", file=sys.stderr)
        sys.exit(1)

    # Per FR25: warn on active hook channels without schema_confirmed_at.
    warnings: list[str] = []
    hook_surfaces = {"hook_script", "hook_stdout_ephemeral"}
    for entry in manifest.channels:
        surface_val: str = (
            entry.surface.value
            if hasattr(entry.surface, "value")
            else str(entry.surface)
        )
        status_val: str = (
            entry.status.value
            if hasattr(entry.status, "value")
            else str(entry.status)
        )
        if surface_val in hook_surfaces and status_val == "active":
            schema_key = getattr(entry, "hook_schema_confirmed_at", None)
            if schema_key is None:
                warnings.append(
                    f"  WARN: channel '{entry.id}' surface={surface_val} "
                    f"status=active but hook_schema_confirmed_at is null"
                )

    if warnings:
        for w in warnings:
            print(w, file=sys.stderr)

    n = len(manifest.channels)
    print(f"OK: manifest valid ({n} channel{'s' if n != 1 else ''})")


def _run_scan(args: argparse.Namespace, channels_dir: Path) -> None:
    """List orphaned locks and stale state files."""
    max_age_hours: int = getattr(args, "max_age_hours", _DEFAULT_MAX_AGE_HOURS)
    dry_run: bool = getattr(args, "dry_run", True)
    max_age_secs = max_age_hours * 3600
    now = time.time()

    # Load manifest to know which channels are active/disabled.
    manifest_path = _find_manifest(channels_dir)
    active_lock_paths: set[Path] = set()
    if manifest_path.exists():
        try:
            from trw_mcp.channels._manifest_loader import load

            manifest = load(manifest_path)
            for entry in manifest.channels:
                if entry.lock_file:
                    active_lock_paths.add(Path(entry.lock_file))
        except Exception:
            pass  # best-effort

    orphaned_locks: list[Path] = []
    stale_states: list[Path] = []

    if channels_dir.exists():
        for candidate in channels_dir.rglob(f"*{_LOCK_SUFFIX}"):
            # Orphaned = lock path NOT in manifest's active lock_file set.
            if candidate not in active_lock_paths:
                try:
                    age = now - candidate.stat().st_mtime
                    if age > max_age_secs:
                        orphaned_locks.append(candidate)
                except OSError:
                    orphaned_locks.append(candidate)

        for candidate in channels_dir.rglob(f"*{_STATE_SUFFIX}"):
            try:
                age = now - candidate.stat().st_mtime
                if age > max_age_secs * 7:  # 7x threshold for state files
                    stale_states.append(candidate)
            except OSError:
                stale_states.append(candidate)

    if not orphaned_locks and not stale_states:
        print("OK: no orphaned locks or stale state files found")
        return

    if orphaned_locks:
        print(f"Found {len(orphaned_locks)} orphaned lock(s) older than {max_age_hours}h:")
        for p in orphaned_locks:
            print(f"  {p}")

    if stale_states:
        print(f"Found {len(stale_states)} stale state file(s):")
        for p in stale_states:
            print(f"  {p}")

    if dry_run:
        print("(dry-run: no files removed)")


def _run_clean(args: argparse.Namespace, channels_dir: Path) -> None:
    """Remove orphaned lock files older than --max-age-hours."""
    max_age_hours: int = getattr(args, "max_age_hours", _DEFAULT_MAX_AGE_HOURS)
    dry_run: bool = getattr(args, "dry_run", False)
    max_age_secs = max_age_hours * 3600
    now = time.time()

    # Determine which lock files are registered in the manifest.
    manifest_path = _find_manifest(channels_dir)
    active_lock_paths: set[Path] = set()
    disabled_lock_paths: set[Path] = set()
    if manifest_path.exists():
        try:
            from trw_mcp.channels._manifest_loader import load

            manifest = load(manifest_path)
            for entry in manifest.channels:
                status_val: str = (
                    entry.status.value
                    if hasattr(entry.status, "value")
                    else str(entry.status)
                )
                if entry.lock_file:
                    lp = Path(entry.lock_file)
                    if status_val in {"disabled", "deprecated"}:
                        disabled_lock_paths.add(lp)
                    else:
                        active_lock_paths.add(lp)
        except Exception:
            pass  # best-effort

    removed: list[Path] = []
    errors: list[str] = []

    if channels_dir.exists():
        for candidate in list(channels_dir.rglob(f"*{_LOCK_SUFFIX}")):
            should_remove = False
            # Remove if for disabled/deprecated channel.
            if candidate in disabled_lock_paths:
                should_remove = True
            # Remove if orphaned (not in manifest) AND older than max_age.
            elif candidate not in active_lock_paths:
                try:
                    age = now - candidate.stat().st_mtime
                    if age > max_age_secs:
                        should_remove = True
                except OSError:
                    should_remove = True  # unreadable → treat as orphan

            if should_remove:
                if dry_run:
                    removed.append(candidate)
                else:
                    try:
                        candidate.unlink()
                        removed.append(candidate)
                    except OSError as exc:
                        errors.append(f"  ERROR removing {candidate}: {exc}")

    if removed:
        verb = "Would remove" if dry_run else "Removed"
        print(f"{verb} {len(removed)} lock file(s):")
        for p in removed:
            print(f"  {p}")
    else:
        print("OK: no lock files to remove")

    for err in errors:
        print(err, file=sys.stderr)

    if dry_run and removed:
        print("(dry-run: no files removed)")


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def run_channel_doctor(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate channel-doctor sub-command."""
    # Resolve channels dir from project root default.
    project_dir = Path(getattr(args, "project_dir", "."))
    channels_dir = project_dir / _DEFAULT_CHANNELS_DIR

    sub = getattr(args, "channel_doctor_command", None)

    if sub == "init":
        _run_init(args, channels_dir)
    elif sub == "validate":
        _run_validate(args, channels_dir)
    elif sub == "scan":
        _run_scan(args, channels_dir)
    elif sub == "clean":
        _run_clean(args, channels_dir)
    else:
        # No sub-command: print help.
        print(
            "Usage: trw-mcp channel-doctor {init,validate,scan,clean} [options]\n"
            "  init      Create .trw/channels/ + empty manifest if absent\n"
            "  validate  Validate manifest.yaml schema (exits 1 on error)\n"
            "  scan      List orphaned locks + stale state files\n"
            "  clean     Remove orphaned locks older than --max-age-hours\n"
        )
