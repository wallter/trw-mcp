# Parent facade: bootstrap/_utils.py
"""File operation helpers — extracted from ``_utils.py`` for module-size compliance.

Pure utility functions for copying, writing, and comparing files during
bootstrap (init-project and update-project).  All public names are
re-exported from ``_utils.py`` so existing import paths are preserved.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config._client_profile import ClientProfile

logger = structlog.get_logger(__name__)

# Type for streaming progress callback.
# Called as: callback(action, path) where action is one of:
# "Created", "Updated", "Skipped", "Preserved", "Error"
ProgressCallback = Callable[[str, str], None] | None


# ---------------------------------------------------------------------------
# Bootstrap result helpers (shared across _copilot.py, _codex.py, _opencode.py)
# ---------------------------------------------------------------------------


def _new_result() -> dict[str, list[str]]:
    """Return a standard bootstrap result payload with all four keys."""
    return {"created": [], "updated": [], "preserved": [], "errors": []}


def _record_write(result: dict[str, list[str]], rel_path: str, *, existed: bool) -> None:
    """Record a create/update action for a generated artifact."""
    if existed:
        result.setdefault("updated", []).append(rel_path)
    else:
        result.setdefault("created", []).append(rel_path)


# ---------------------------------------------------------------------------
# File-level helpers
# ---------------------------------------------------------------------------


def _ensure_dir(
    path: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Create directory if it doesn't exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        result["created"].append(str(path) + "/")
        if on_progress:
            on_progress("Created", str(path) + "/")
    # Already existing dirs are silently fine -- not worth reporting as "skipped".


def _result_action_key(result: dict[str, list[str]]) -> str:
    """Return the appropriate result key: ``'updated'`` for update flows, ``'created'`` for init."""
    return "updated" if "updated" in result else "created"


def _copy_file(
    src: Path,
    dest: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Copy *src* to *dest* with idempotency."""
    if dest.exists() and not force:
        result["skipped"].append(str(dest))
        if on_progress:
            on_progress("Skipped", str(dest))
        return
    try:
        shutil.copy2(src, dest)
        # Ensure shell scripts are executable (pip install may strip permissions)
        if dest.suffix == ".sh":
            executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            os.chmod(dest, os.stat(dest).st_mode | executable)
        result["created"].append(str(dest))
        if on_progress:
            on_progress("Created", str(dest))
    except OSError as exc:
        result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")
        if on_progress:
            on_progress("Error", str(dest))


def _write_if_missing(
    dest: Path,
    content: str,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Write *content* to *dest* if it doesn't exist (or *force* is True)."""
    if dest.exists() and not force:
        result["skipped"].append(str(dest))
        if on_progress:
            on_progress("Skipped", str(dest))
        return
    try:
        dest.write_text(content, encoding="utf-8")
        result["created"].append(str(dest))
        if on_progress:
            on_progress("Created", str(dest))
    except OSError as exc:
        result["errors"].append(f"Failed to write {dest}: {exc}")
        if on_progress:
            on_progress("Error", str(dest))


def _write_hook_env_file(trw_dir: Path, profile: ClientProfile) -> Path:
    """PRD-CORE-149 FR04: write ``.trw/runtime/hook-env.sh`` for hook scripts.

    The generated file is sourced by every TRW hook at startup to decide
    whether to emit stdout (``HOOKS_ENABLED``), whether to run the nudge-pool
    init (``NUDGE_ENABLED``), and which client-identity tokens to expose
    (``TRW_CLIENT_DISPLAY_NAME`` / ``TRW_CLIENT_CONFIG_DIR``).

    Idempotent: safe to rewrite on every sync. Permissions are 0644
    (world-readable; hooks only need read access). Creates ``runtime/`` if
    missing.
    """
    runtime_dir = trw_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "hook-env.sh"
    hooks_flag = "true" if profile.hooks_enabled else "false"
    nudge_flag = "true" if profile.nudge_enabled else "false"
    # Quote every value so naive ``source`` consumers don't split on spaces.
    content = (
        "# TRW hook environment (generated by trw_instructions_sync / init-project)\n"
        "# PRD-CORE-149 FR04: surfaces per-profile hook flags + client identity.\n"
        f'export HOOKS_ENABLED="{hooks_flag}"\n'
        f'export NUDGE_ENABLED="{nudge_flag}"\n'
        f'export TRW_CLIENT_DISPLAY_NAME="{profile.display_name}"\n'
        f'export TRW_CLIENT_CONFIG_DIR="{profile.config_dir}"\n'
    )
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o644)
    logger.debug(
        "hook_env_written",
        path=str(path),
        client_id=profile.client_id,
        hooks_enabled=profile.hooks_enabled,
        nudge_enabled=profile.nudge_enabled,
    )
    return path


def _files_identical(a: Path, b: Path) -> bool:
    """Compare two files by SHA-256 hash for dry-run diffing."""
    try:
        ha = hashlib.sha256(a.read_bytes()).hexdigest()
        hb = hashlib.sha256(b.read_bytes()).hexdigest()
        return ha == hb
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Marker-based smart merge — shared across instruction-file generators
# ---------------------------------------------------------------------------


def smart_merge_marker_section(
    existing: str,
    trw_section: str,
    *,
    start_marker: str,
    end_marker: str,
) -> str:
    """Merge a TRW-managed section into a user-authored instruction file.

    Replaces (or appends) the block delimited by *start_marker* / *end_marker*
    while preserving every byte of user content outside the markers. Designed
    to be safe against pre-existing files written by users or other tools
    (the common case for ``GEMINI.md``, ``.github/copilot-instructions.md``,
    and similar shared-namespace artifacts).

    Behavior:
      - Both markers present in correct order → replace the section between.
      - Markers absent / corrupted (only one, or end before start) → append
        a fresh TRW section to the end. User content is preserved as-is.
      - Identical TRW content already in place → return *existing* unchanged
        (so callers can skip a write and report ``preserved``).
      - Empty *existing* → return *trw_section* + trailing newline.

    *trw_section* MUST already include both *start_marker* and *end_marker*;
    callers are responsible for rendering the full delimited block.

    Args:
        existing: Current file contents (may be empty / arbitrary user prose).
        trw_section: Replacement section, including both markers.
        start_marker: Opening sentinel (e.g. ``"<!-- trw:gemini:start -->"``).
        end_marker: Closing sentinel.

    Returns:
        The merged document. Idempotent: ``f(f(x)) == f(x)`` for any *x*.
    """
    start_idx = existing.find(start_marker)
    end_idx = existing.find(end_marker)

    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        end_idx += len(end_marker)
        merged = existing[:start_idx] + trw_section.rstrip("\n") + existing[end_idx:]
        if merged == existing:
            return existing
        return merged

    separator = "\n\n" if existing.strip() else ""
    return existing.rstrip() + separator + trw_section + "\n"


def write_instruction_file_with_merge(
    *,
    target_path: Path,
    rel_path: str,
    trw_section: str,
    start_marker: str,
    end_marker: str,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Idempotently write or merge a TRW-managed instruction file.

    Encapsulates the read/merge/write/short-circuit pattern used identically
    by every per-client instruction-file generator (Gemini, Copilot, Codex,
    OpenCode). On idempotent writes (no diff vs. disk), records the path
    under ``preserved`` so callers can report it without a redundant
    filesystem write.

    Failures are appended to ``result["errors"]`` and the function returns
    normally — the caller decides whether to escalate.
    """
    existed = target_path.exists()
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if existed and not force:
            existing = target_path.read_text(encoding="utf-8")
            merged = smart_merge_marker_section(
                existing,
                trw_section,
                start_marker=start_marker,
                end_marker=end_marker,
            )
            if merged == existing:
                result.setdefault("preserved", []).append(rel_path)
                return
            target_path.write_text(merged, encoding="utf-8")
        else:
            target_path.write_text(trw_section, encoding="utf-8")
        _record_write(result, rel_path, existed=existed)
    except OSError as exc:
        result.setdefault("errors", []).append(f"Failed to write {target_path}: {exc}")
