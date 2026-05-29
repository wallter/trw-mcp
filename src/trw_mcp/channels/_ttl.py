"""TTL staleness check with detached HEAD fallback (SYS-03 fix).

Implements check_staleness per PRD-DIST-2400 FR14.

Key design: subprocess non-zero exit OR empty/unparseable stdout from
  git rev-list --count <sha>..HEAD
returns CheckResult(is_stale=False, ttl_unknown=True) rather than
is_stale=True, preventing false T0 beacon degradation in CI / detached HEAD
environments (GitHub Actions SHA checkout, git bisect, etc.).

PRD-DIST-2400 Phase C.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.channels._manifest_models import ChannelEntry

log = structlog.get_logger(__name__)

__all__ = [
    "CheckResult",
    "check_staleness",
]


class CheckResult(BaseModel):
    """Result of a TTL staleness check.

    Field semantics (MED-6 fix — aligned with PRD-DIST-2400 FR14):
    - ``ttl_commits_remaining``: budget commits left before stale
      (None when ttl_commits is not configured or ttl_unknown).
    - ``ttl_days_remaining``: budget days left before stale
      (None when ttl_days is not configured or last_render_ts absent).
    Both fields carry a non-negative remaining budget, matching the FR14 spec.
    The renderer uses ttl_commits_remaining directly in InstructionSegmentResult.
    """

    model_config = ConfigDict(extra="forbid")

    is_stale: bool
    ttl_unknown: bool = False
    ttl_commits_remaining: int | None = None
    ttl_days_remaining: float | None = None


def _git_commits_since(sha: str, *, repo_root: Path | None) -> int | None:
    """Run git rev-list --count sha..HEAD and return the count.

    Returns None on any failure (non-zero exit, empty output, parse error,
    subprocess timeout, FileNotFoundError) so callers can treat it as
    ttl_unknown.

    This is the SYS-03 detached HEAD fix: when git exits non-zero (detached
    HEAD, unknown sha, shallow clone) we return None rather than raising.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-list", "--count", f"{sha}..HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_root) if repo_root else None,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.debug(
            "git_rev_list_failed",
            sha=sha,
            error=str(exc),
            outcome="ttl_unknown",
        )
        return None

    if result.returncode != 0:
        log.debug(
            "git_rev_list_nonzero",
            sha=sha,
            returncode=result.returncode,
            stderr=result.stderr.strip(),
            outcome="ttl_unknown",
        )
        return None

    stdout = result.stdout.strip()
    if not stdout:
        log.debug(
            "git_rev_list_empty_output",
            sha=sha,
            outcome="ttl_unknown",
        )
        return None

    try:
        return int(stdout)
    except ValueError:
        log.debug(
            "git_rev_list_parse_error",
            sha=sha,
            stdout=stdout,
            outcome="ttl_unknown",
        )
        return None


def check_staleness(
    *,
    entry: ChannelEntry,
    last_sidecar_sha: str | None,
    last_render_ts: str | None,
    repo_root: Path | None = None,
) -> CheckResult:
    """Check whether a channel's rendered content is TTL-stale.

    Args:
        entry: The ChannelEntry manifest config (provides ttl_commits,
            ttl_days).
        last_sidecar_sha: The git SHA recorded at last render time, or None
            if the channel has never been rendered.
        last_render_ts: ISO-8601 UTC timestamp of the last render, or None.
        repo_root: Optional path to the git repository root.  Defaults to
            the current working directory when None.

    Returns:
        CheckResult with is_stale, ttl_unknown, ttl_commits_remaining, ttl_days_remaining.

    SYS-03 contract:
        If last_sidecar_sha is None → ttl_unknown=True (never rendered).
        If git exits non-zero or stdout is unparseable → ttl_unknown=True
        (NOT is_stale=True).  Callers treat ttl_unknown=True as "proceed
        with current content."
    """
    # ---- never-rendered fast path ----
    if last_sidecar_sha is None:
        return CheckResult(is_stale=False, ttl_unknown=True)

    is_stale = False
    ttl_commits_remaining: int | None = None
    ttl_days_remaining: float | None = None

    # ---- commit-based TTL ----
    if entry.ttl_commits is not None:
        count = _git_commits_since(last_sidecar_sha, repo_root=repo_root)
        if count is None:
            # SYS-03: detached HEAD / error → ttl_unknown, never stale
            return CheckResult(is_stale=False, ttl_unknown=True)
        ttl_commits_remaining = entry.ttl_commits - count
        if count > entry.ttl_commits:
            is_stale = True

    # ---- days-based TTL ----
    if last_render_ts is not None and entry.ttl_days is not None:
        try:
            last_dt = datetime.fromisoformat(last_render_ts.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            delta = now - last_dt
            days_elapsed = delta.total_seconds() / 86400.0
            ttl_days_remaining = entry.ttl_days - days_elapsed
            if delta.days > entry.ttl_days:
                is_stale = True
        except (ValueError, TypeError) as exc:
            log.debug(
                "ttl_days_parse_error",
                last_render_ts=last_render_ts,
                error=str(exc),
                outcome="ttl_days_skipped",
            )

    return CheckResult(
        is_stale=is_stale,
        ttl_unknown=False,
        ttl_commits_remaining=ttl_commits_remaining,
        ttl_days_remaining=ttl_days_remaining,
    )
