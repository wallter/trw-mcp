"""Git-based rework rate computation for delivery metrics.

PRD-CORE-104-FR01: Analyzes git history to detect files modified in
"fix" contexts within a lookback window.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TypedDict

import structlog

logger = structlog.get_logger(__name__)

_FIX_PREFIXES = ("fix", "hotfix", "revert")
_BATCH_SIZE = 50
_GIT_TIMEOUT = 5


class ReworkRateResult(TypedDict):
    """Typed result from compute_rework_rate."""

    rework_rate: float
    rework_files: int
    total_files: int


def compute_rework_rate(
    changed_files: list[str],
    *,
    lookback_days: int = 14,
    project_root: str | Path | None = None,
) -> ReworkRateResult:
    """Compute rework rate from git history.

    For each changed file, checks if it was modified in a "fix" commit
    within the lookback window. A file is rework if the prior commit
    message starts with fix/hotfix/revert (case-insensitive).

    Args:
        changed_files: Files changed in current session.
        lookback_days: Days to look back for fix commits (default 14).
        project_root: Project root for git commands. Defaults to cwd.

    Returns:
        Dict with rework_rate (float 0.0-1.0), rework_files (int),
        total_files (int).
    """
    if not changed_files:
        return {"rework_rate": 0.0, "rework_files": 0, "total_files": 0}

    cwd = str(project_root) if project_root else None
    rework_files = 0
    total = len(changed_files)

    # Process in batches of 50
    for i in range(0, total, _BATCH_SIZE):
        batch = changed_files[i : i + _BATCH_SIZE]
        for file_path in batch:
            try:
                result = subprocess.run(  # noqa: S603
                    [  # noqa: S607
                        "git",
                        "log",
                        f"--since={lookback_days} days ago",
                        "--format=%s",  # subject line only
                        "--diff-filter=M",
                        "--",
                        file_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=_GIT_TIMEOUT,
                    cwd=cwd,
                )
                if result.returncode != 0:
                    continue

                for line in result.stdout.strip().split("\n"):
                    line_lower = line.strip().lower()
                    if any(line_lower.startswith(p) for p in _FIX_PREFIXES):
                        rework_files += 1
                        break

            except subprocess.TimeoutExpired:
                logger.warning(
                    "rework_rate_git_timeout",
                    file=file_path,
                    rework_rate_git_timeout=True,
                )
            except (OSError, ValueError):
                logger.debug("rework_rate_git_failed", file=file_path, exc_info=True)

    rate = rework_files / max(total, 1)
    return {
        "rework_rate": round(rate, 4),
        "rework_files": rework_files,
        "total_files": total,
    }


__all__ = ["compute_rework_rate"]
