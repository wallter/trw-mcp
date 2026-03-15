"""Shared path resolution -- single source of truth for project root, .trw dir, and run paths.

All modules that need to resolve TRW_PROJECT_ROOT, the .trw directory,
or an active run path MUST use these functions instead of inline logic.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


# --- Session identity (PRD-FIX-042 FR03) ---
_session_id: str = uuid.uuid4().hex


def get_session_id() -> str:
    """Return the current process session ID."""
    return _session_id


def _reset_session_id(new_id: str | None = None) -> None:
    """Reset the session ID (for testing). Generates a new one if *new_id* is None."""
    global _session_id
    _session_id = new_id if new_id is not None else uuid.uuid4().hex


# --- Per-session run pinning (PRD-FIX-042 FR06) ---
# Each Claude Code instance spawns its own MCP process (stdio transport).
# trw_init pins the run it creates so all subsequent find_active_run() calls
# for THIS session return it, preventing telemetry hijack when parallel
# instances share the same filesystem.
_pinned_runs: dict[str, Path] = {}


def pin_active_run(run_dir: Path, *, session_id: str | None = None) -> None:
    """Pin a run directory as the active run for a session.

    After pinning, find_active_run() returns this directory instead of
    scanning the filesystem. This prevents telemetry hijack when multiple
    instances share the same project root.

    Args:
        run_dir: Absolute path to the run directory to pin.
        session_id: Session to pin for. Defaults to the current process session.
    """
    sid = session_id if session_id is not None else get_session_id()
    _pinned_runs[sid] = run_dir.resolve()


def unpin_active_run(*, session_id: str | None = None) -> None:
    """Remove the run pin for a session, reverting to filesystem scan."""
    sid = session_id if session_id is not None else get_session_id()
    _pinned_runs.pop(sid, None)


def get_pinned_run(*, session_id: str | None = None) -> Path | None:
    """Return the currently pinned run directory for a session, or None."""
    sid = session_id if session_id is not None else get_session_id()
    return _pinned_runs.get(sid)


def resolve_memory_store_path() -> Path:
    """Resolve the sqlite-vec memory store database path.

    Strips the ``.trw/`` prefix from the configured ``memory_store_path``
    and joins with the resolved .trw directory.

    Returns:
        Absolute path to the sqlite-vec database file.
    """
    config = get_config()
    return resolve_trw_dir() / config.memory_store_path.removeprefix(".trw/")


def resolve_project_root() -> Path:
    """Resolve the project root from environment or CWD.

    Resolution order:
    1. ``TRW_PROJECT_ROOT`` environment variable (if set)
    2. Current working directory

    Returns:
        Absolute path to the project root directory.
    """
    env_root = os.environ.get("TRW_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


def resolve_trw_dir() -> Path:
    """Resolve the .trw directory path.

    Returns:
        Absolute path to the .trw directory (project_root / config.trw_dir).
    """
    config = get_config()
    return resolve_project_root() / config.trw_dir


def iter_run_dirs(runs_root: Path) -> Iterator[tuple[Path, Path]]:
    """Yield ``(run_dir, run_yaml_path)`` for all valid runs under *runs_root*.

    Scans ``runs_root/{task}/{run_id}/meta/run.yaml`` in sorted order and yields
    each run directory paired with its ``run.yaml`` path.  Directories
    without a ``run.yaml`` are silently skipped.

    This is the **single source of truth** for run-directory iteration.
    All modules that need to walk run directories MUST use this generator
    instead of reimplementing the triple-nested loop.

    Args:
        runs_root: Base directory containing task subdirectories with runs.

    Yields:
        Tuples of ``(run_dir, run_yaml_path)``.
    """
    if not runs_root.is_dir():
        return
    for task_dir in sorted(runs_root.iterdir()):
        if not task_dir.is_dir():
            continue
        for run_dir in sorted(task_dir.iterdir()):
            run_yaml = run_dir / "meta" / "run.yaml"
            if run_yaml.exists():
                yield run_dir, run_yaml


def _find_latest_run_dir(base_dir: Path) -> Path | None:
    """Scan ``base_dir/{task}/{run_id}/meta/run.yaml`` and return the run dir with the newest mtime.

    Returns:
        The run directory whose ``run.yaml`` was most recently modified,
        or ``None`` if no valid run directories exist.
    """
    latest_run = None
    latest_mtime = 0.0

    for run_dir, run_yaml in iter_run_dirs(base_dir):
        mtime = run_yaml.stat().st_mtime
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_run = run_dir

    return latest_run


def find_active_run(*, session_id: str | None = None) -> Path | None:
    """Find the active run directory for a session.

    Resolution order:
    1. Per-session pinned run (set by ``pin_active_run`` during ``trw_init``)
    2. Filesystem scan: ``{runs_root}/{task}/{run_id}/meta/run.yaml``, highest
       lexicographic name (ISO timestamp prefix ensures chronological ordering),
       skipping runs with status "complete" or "failed" (PRD-FIX-042 FR02).

    The pinned run prevents telemetry hijack when multiple Claude Code
    instances share the same filesystem — each instance's MCP process
    pins its own run at init time.

    Args:
        session_id: Session to check pin for. Defaults to the current process session.

    Returns:
        Path to run directory, or None if no active run found.
    """
    sid = session_id if session_id is not None else get_session_id()
    pinned = _pinned_runs.get(sid)
    if pinned is not None:
        return pinned

    try:
        config = get_config()
        reader = FileStateReader()
        project_root = resolve_project_root()
        runs_root = project_root / config.runs_root
        if not runs_root.exists():
            return None

        latest_name = ""
        latest_dir: Path | None = None
        for run_dir, run_yaml in iter_run_dirs(runs_root):
            # Status-aware: skip completed/failed runs (PRD-FIX-042 FR02)
            try:
                data = reader.read_yaml(run_yaml)
                status = str(data.get("status", "active"))
                if status in ("complete", "failed"):
                    continue
            except Exception:  # justified: fail-open, unreadable run.yaml treated as active for backward compat
                logger.debug("run_yaml_read_failed", path=str(run_yaml), exc_info=True)
            if run_dir.name > latest_name:
                latest_name = run_dir.name
                latest_dir = run_dir

        return latest_dir
    except (StateError, OSError):
        return None


def resolve_run_path(run_path: str | None = None) -> Path:
    """Resolve a run directory from an explicit path or auto-detection.

    Unified implementation (PRD-FIX-007) replacing the duplicated private
    ``_resolve_run_path`` helpers in orchestration.py and findings.py.

    Resolution order:
    1. If *run_path* is provided, resolve to absolute and verify existence.
    2. Otherwise, scan ``{task_root}/*/runs/*/meta/run.yaml`` and select the
       directory whose ``run.yaml`` has the most recent ``st_mtime``.

    Args:
        run_path: Explicit run directory path, or ``None`` for auto-detect.

    Returns:
        Absolute path to the run directory.

    Raises:
        StateError: If the explicit path does not exist or no run directory
            can be found during auto-detection.
    """
    if run_path:
        resolved = Path(run_path).resolve()
        if not resolved.exists():
            raise StateError(
                f"Run path does not exist: {resolved}",
                path=str(resolved),
            )
        # Path containment check (PRD-QUAL-042-FR02)
        project_root = resolve_project_root()
        if not resolved.is_relative_to(project_root):
            raise StateError(
                f"Run path escapes project root: {resolved}",
                path=str(resolved),
            )
        return resolved

    config = get_config()
    project_root = resolve_project_root()
    runs_dir = project_root / config.runs_root
    if not runs_dir.exists():
        raise StateError(
            f"Cannot auto-detect run path: {config.runs_root}/ directory not found",
            project_root=str(project_root),
        )

    latest_run = _find_latest_run_dir(runs_dir)
    if latest_run is None:
        raise StateError(
            f"No active runs found in {config.runs_root}/",
            project_root=str(project_root),
        )

    return latest_run


def detect_current_phase() -> str | None:
    """Detect the current phase from the active run.

    Resolution order:
    1. Pinned run directory (process-local, set by ``trw_init``)
    2. Filesystem scan: ``{runs_root}/{task}/`` for the latest ``run.yaml``

    Only returns a phase when the run's ``status`` is ``"active"``.

    Returns:
        Current phase string (e.g. ``"implement"``), or ``None`` if no active run.
    """
    try:
        config = get_config()
        reader = FileStateReader()

        # Use pinned run if available
        pinned = get_pinned_run()
        if pinned is not None:
            run_yaml = pinned / "meta" / "run.yaml"
            if run_yaml.exists():
                data = reader.read_yaml(run_yaml)
                if str(data.get("status", "")) != "active":
                    return None
                return str(data.get("phase", "")) or None
            return None

        runs_root = resolve_project_root() / config.runs_root
        if not runs_root.exists():
            return None

        latest_name = ""
        latest_yaml: Path | None = None
        for run_dir, run_yaml in iter_run_dirs(runs_root):
            # Status-aware: skip completed/failed runs (matches find_active_run)
            try:
                data = reader.read_yaml(run_yaml)
                status = str(data.get("status", "active"))
                if status in ("complete", "failed"):
                    continue
            except Exception:  # justified: fail-open, unreadable run.yaml treated as active
                logger.debug("run_yaml_read_failed", path=str(run_yaml), exc_info=True)
            if run_dir.name > latest_name:
                latest_name = run_dir.name
                latest_yaml = run_yaml

        if latest_yaml is None:
            return None

        data = reader.read_yaml(latest_yaml)
        if str(data.get("status", "")) != "active":
            return None
        return str(data.get("phase", "")) or None
    except (OSError, ValueError, TypeError):
        return None
