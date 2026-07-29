"""trw_channel_stats MCP tool — channel correlation + throttle health.

Returns per-channel correlation rate and throttle status so an agent or
operator can query channel telemetry health via MCP.

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
from fastmcp import FastMCP

log = structlog.get_logger(__name__)

__all__ = [
    "compute_channel_stats_result",
    "register_channel_stats_tools",
]

_DEFAULT_LOG_SUBPATH = ".trw/telemetry/channel-events.jsonl"
_DEFAULT_MANIFEST_SUBPATH = ".trw/channels/manifest.yaml"


def _resolve_repo_root(repo_root: str | None) -> Path | None:
    if repo_root is not None:
        return Path(repo_root)
    root_env = os.environ.get("TRW_REPO_ROOT")
    if root_env:
        return Path(root_env)
    git = shutil.which("git")
    if git is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [git, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return Path(proc.stdout.strip())
    except Exception as exc:
        log.debug("channel_stats_git_root_resolution_failed", error=str(exc))
    return None


def compute_channel_stats_result(
    window_hours: int = 1,
    repo_root: str | None = None,
) -> dict[str, Any]:
    """Compute correlation/throttle stats; return as plain dict.

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
        manifest_path = root / _DEFAULT_MANIFEST_SUBPATH
        window_seconds = max(1, window_hours) * 3600

        from trw_mcp.channels.meta_tune._stats import compute_channel_stats

        report = compute_channel_stats(
            log_path,
            window_seconds=window_seconds,
            manifest_path=manifest_path if manifest_path.exists() else None,
        )

        channels_out: list[dict[str, Any]] = [e.model_dump() for e in report.channels]

        # "ok" with an empty list reads as "healthy, nothing to throttle". For a
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


def register_channel_stats_tools(mcp: FastMCP) -> None:
    """Register trw_channel_stats on the MCP server."""

    # Reads channel-events.jsonl, computes push->outcome correlation rates per
    # (channel_id, client), applies CLIENT_CORRECTION_FACTORS, and evaluates
    # CLIENT_THROTTLE_THRESHOLDS to produce throttle decisions.
    @mcp.tool()
    def trw_channel_stats(
        window_hours: int = 1,
        repo_root: str | None = None,
    ) -> dict[str, Any]:
        """Return per-channel correlation rate and throttle status.

        Use when: inspecting channel health or debugging throttle decisions.

        Output: status ok|no_activity|error, per-channel correlation + throttle
        rows, total_events. no_activity means the log held no channel events at
        all, which is distinct from ok with an empty list.

        Args:
            window_hours: correlation time window in hours (default 1).
        """
        return compute_channel_stats_result(
            window_hours=window_hours,
            repo_root=repo_root,
        )

    log.debug("channel_stats_tool_registered", outcome="registered")
