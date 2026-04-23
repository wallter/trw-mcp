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


def _write_hook_env_file(trw_dir: Path, profile: "ClientProfile") -> Path:
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
