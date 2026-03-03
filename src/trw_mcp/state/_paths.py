"""Shared path resolution -- single source of truth for project root, .trw dir, and run paths.

All modules that need to resolve TRW_PROJECT_ROOT, the .trw directory,
or an active run path MUST use these functions instead of inline logic.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateReader

_config = get_config()
_reader = FileStateReader()

# --- Process-local run pinning (RC-001 fix) ---
# Each Claude Code instance spawns its own MCP process (stdio transport).
# trw_init pins the run it creates so all subsequent find_active_run() calls
# in THIS process return it, preventing telemetry hijack when parallel
# instances share the same filesystem.
_pinned_run_dir: Path | None = None


def pin_active_run(run_dir: Path) -> None:
    """Pin a run directory as the active run for this process.

    After pinning, find_active_run() returns this directory instead of
    scanning the filesystem. This prevents telemetry hijack when multiple
    instances share the same project root.

    Args:
        run_dir: Absolute path to the run directory to pin.
    """
    global _pinned_run_dir  # noqa: PLW0603
    _pinned_run_dir = run_dir.resolve()


def unpin_active_run() -> None:
    """Remove the process-local run pin, reverting to filesystem scan."""
    global _pinned_run_dir  # noqa: PLW0603
    _pinned_run_dir = None


def get_pinned_run() -> Path | None:
    """Return the currently pinned run directory, or None."""
    return _pinned_run_dir


def resolve_memory_store_path() -> Path:
    """Resolve the sqlite-vec memory store database path.

    Strips the ``.trw/`` prefix from the configured ``memory_store_path``
    and joins with the resolved .trw directory.

    Returns:
        Absolute path to the sqlite-vec database file.
    """
    return resolve_trw_dir() / _config.memory_store_path.removeprefix(".trw/")


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
    return resolve_project_root() / _config.trw_dir


def iter_run_dirs(task_root: Path) -> Iterator[tuple[Path, Path]]:
    """Yield ``(run_dir, run_yaml_path)`` for all valid runs under *task_root*.

    Scans ``task_root/*/runs/*/meta/run.yaml`` in sorted order and yields
    each run directory paired with its ``run.yaml`` path.  Directories
    without a ``run.yaml`` are silently skipped.

    This is the **single source of truth** for run-directory iteration.
    All modules that need to walk run directories MUST use this generator
    instead of reimplementing the triple-nested loop.

    Args:
        task_root: Base directory containing task subdirectories.

    Yields:
        Tuples of ``(run_dir, run_yaml_path)``.
    """
    if not task_root.is_dir():
        return
    for task_dir in sorted(task_root.iterdir()):
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in sorted(runs_dir.iterdir()):
            run_yaml = run_dir / "meta" / "run.yaml"
            if run_yaml.exists():
                yield run_dir, run_yaml


def _find_latest_run_dir(base_dir: Path) -> Path | None:
    """Scan ``base_dir/*/runs/*/meta/run.yaml`` and return the run dir with the newest mtime.

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


def find_active_run() -> Path | None:
    """Find the active run directory for this process.

    Resolution order:
    1. Process-local pinned run (set by ``pin_active_run`` during ``trw_init``)
    2. Filesystem scan: ``{task_root}/*/runs/*/meta/run.yaml``, highest
       lexicographic name (ISO timestamp prefix ensures chronological ordering)

    The pinned run prevents telemetry hijack when multiple Claude Code
    instances share the same filesystem — each instance's MCP process
    pins its own run at init time.

    Returns:
        Path to run directory, or None if no active run found.
    """
    if _pinned_run_dir is not None:
        return _pinned_run_dir

    try:
        project_root = resolve_project_root()
        task_root = project_root / _config.task_root
        if not task_root.exists():
            return None

        latest_name = ""
        latest_dir: Path | None = None
        for run_dir, _run_yaml in iter_run_dirs(task_root):
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
        return resolved

    project_root = resolve_project_root()
    task_dir = project_root / _config.task_root
    if not task_dir.exists():
        raise StateError(
            f"Cannot auto-detect run path: {_config.task_root}/ directory not found",
            project_root=str(project_root),
        )

    latest_run = _find_latest_run_dir(task_dir)
    if latest_run is None:
        raise StateError(
            f"No active runs found in {_config.task_root}/*/runs/",
            project_root=str(project_root),
        )

    return latest_run


def detect_current_phase() -> str | None:
    """Detect the current phase from the active run.

    Resolution order:
    1. Pinned run directory (process-local, set by ``trw_init``)
    2. Filesystem scan: ``{task_root}/*/runs/`` for the latest ``run.yaml``

    Only returns a phase when the run's ``status`` is ``"active"``.

    Returns:
        Current phase string (e.g. ``"implement"``), or ``None`` if no active run.
    """
    try:
        # Use pinned run if available
        if _pinned_run_dir is not None:
            run_yaml = _pinned_run_dir / "meta" / "run.yaml"
            if run_yaml.exists():
                data = _reader.read_yaml(run_yaml)
                if str(data.get("status", "")) != "active":
                    return None
                return str(data.get("phase", "")) or None
            return None

        task_root = resolve_project_root() / _config.task_root
        if not task_root.exists():
            return None

        latest_name = ""
        latest_yaml: Path | None = None
        for run_dir, run_yaml in iter_run_dirs(task_root):
            if run_dir.name > latest_name:
                latest_name = run_dir.name
                latest_yaml = run_yaml

        if latest_yaml is None:
            return None

        data = _reader.read_yaml(latest_yaml)
        if str(data.get("status", "")) != "active":
            return None
        return str(data.get("phase", "")) or None
    except (OSError, ValueError, TypeError):
        return None
