"""Channel correlation health — PRD-CORE-300 slice S3a moved the MCP tool this
module used to register to ``trw-mcp telemetry channel-stats`` (see
``tools/_telemetry_cli.py``, which calls :func:`compute_channel_stats_result`
directly).

The throttle evaluation this tool used to surface was removed 2026-09-22
(RC-014): it fed a manifest field (``tier_default``) nothing read to change
behavior.

NEVER raises — all error paths return a partial or empty result dict.
Zero trw_distill imports.

PRD-DIST-2400 §meta-tune.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "compute_channel_stats_result",
]

_DEFAULT_LOG_SUBPATH = ".trw/telemetry/channel-events.jsonl"


def _resolve_repo_root(repo_root: str | None) -> Path | None:
    if repo_root is not None:
        return Path(repo_root)
    root_env = os.environ.get("TRW_REPO_ROOT")
    if root_env:
        return Path(root_env)
    from trw_mcp.state._paths import resolve_project_root

    try:
        project = resolve_project_root()
        # A bound project (an enclosing install, TRW_PROJECT_ROOT) or a directory that holds the event log's
        # folder is the project. Otherwise the root is only the process directory, and the project is the
        # repository that directory belongs to: run from ``repo/src`` this reads ``repo/.trw``, as it always did.
        if project != Path.cwd().resolve() or (project / ".trw").is_dir():
            return project
        return _git_toplevel(project) or project
    except Exception as exc:  # trw-fail-silent-allow: caller reports could_not_resolve_repo_root
        log.debug("channel_stats_project_root_resolution_failed", error=str(exc))
    return None


def _git_toplevel(directory: Path) -> Path | None:
    """The top level of the repository *directory* is in, or ``None`` when it is in none or git is absent."""
    git = shutil.which("git")
    if git is None:
        return None
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [git, "rev-parse", "--show-toplevel"], cwd=directory, capture_output=True, text=True, timeout=5, check=False
    )
    return Path(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else None


def compute_channel_stats_result(
    window_hours: int = 1,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Compute correlation stats; return as plain dict.

    Never raises.
    """
    try:
        root = _resolve_repo_root(repo_root)
        if root is None:
            return {
                "status": "error",
                "error": "could_not_resolve_repo_root",
                "channels": [],
                "total_events": 0,
                "window_seconds": window_hours * 3600,
            }

        log_path = root / _DEFAULT_LOG_SUBPATH
        window_seconds = max(1, window_hours) * 3600

        from trw_mcp.channels.meta_tune._stats import compute_channel_stats

        report = compute_channel_stats(
            log_path,
            window_seconds=window_seconds,
        )

        channels_out: list[dict[str, Any]] = [e.model_dump() for e in report.channels]

        # "ok" with an empty list reads as "healthy, nothing to report". For a
        # subsystem that has never fired, that is the wrong claim: no channel
        # emitted anything, so there is nothing to be healthy ABOUT. Same
        # distinction the correlator now draws between an unmeasured rate and a
        # measured zero — a caller must be able to tell "we looked and all is
        # well" from "there was nothing to look at".
        status = "ok" if channels_out else "no_activity"

        return {
            "status": status,
            "channels": channels_out,
            "total_events": report.total_events,
            "window_seconds": report.window_seconds,
            "log_path": report.log_path,
        }
    except Exception as exc:
        log.debug(
            "trw_channel_stats_error",
            error=str(exc),
            outcome="stats_error",
        )
        return {
            "status": "error",
            "error": str(exc),
            "channels": [],
            "total_events": 0,
            "window_seconds": window_hours * 3600,
        }
