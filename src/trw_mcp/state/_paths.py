"""Shared path resolution -- single source of truth for project root, .trw dir, and run paths.

All modules that need to resolve TRW_PROJECT_ROOT, the .trw directory,
or an active run path MUST use these functions instead of inline logic.
"""

from __future__ import annotations

import os
from pathlib import Path

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateReader

_config = get_config()
_reader = FileStateReader()


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


def _find_latest_run_dir(base_dir: Path) -> Path | None:
    """Scan ``base_dir/*/runs/*/meta/run.yaml`` and return the run dir with the newest mtime.

    Returns:
        The run directory whose ``run.yaml`` was most recently modified,
        or ``None`` if no valid run directories exist.
    """
    latest_run = None
    latest_mtime = 0.0

    for task_dir in base_dir.iterdir():
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in runs_dir.iterdir():
            run_yaml = run_dir / "meta" / "run.yaml"
            if not run_yaml.exists():
                continue
            mtime = run_yaml.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_run = run_dir

    return latest_run


def resolve_run_path(run_path: str | None = None) -> Path:
    """Resolve a run directory from an explicit path or auto-detection.

    Unified implementation (PRD-FIX-007) replacing the duplicated private
    ``_resolve_run_path`` helpers in orchestration.py and findings.py.

    Resolution order:
    1. If *run_path* is provided, resolve to absolute and verify existence.
    2. Otherwise, scan ``docs/*/runs/*/meta/run.yaml`` and select the
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
    docs_dir = project_root / "docs"
    if not docs_dir.exists():
        raise StateError(
            "Cannot auto-detect run path: docs/ directory not found",
            project_root=str(project_root),
        )

    latest_run = _find_latest_run_dir(docs_dir)
    if latest_run is None:
        raise StateError(
            "No active runs found in docs/*/runs/",
            project_root=str(project_root),
        )

    return latest_run


def detect_current_phase() -> str | None:
    """Detect the current phase from the most recent active run.

    Scans ``{task_root}/*/runs/`` for the latest ``run.yaml`` with
    ``status: active`` and returns its ``phase`` field.  Selects the
    run directory by lexicographic name (newest naming convention wins).

    Returns:
        Current phase string (e.g. ``"implement"``), or ``None`` if no active run.
    """
    try:
        task_root = resolve_project_root() / _config.task_root
        if not task_root.exists():
            return None

        latest_name = ""
        latest_yaml: Path | None = None
        for task_dir in task_root.iterdir():
            runs_dir = task_dir / "runs"
            if not runs_dir.is_dir():
                continue
            for run_dir in runs_dir.iterdir():
                run_yaml = run_dir / "meta" / "run.yaml"
                if run_yaml.exists() and run_dir.name > latest_name:
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
