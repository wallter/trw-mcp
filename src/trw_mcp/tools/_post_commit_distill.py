"""Post-commit distill refresh beyond the before-edit sidecar (PRD-DIST-2482 FR04).

Belongs to the ``_post_commit`` controller: ``_run_pass`` calls it right after
the before-edit refresh, inside the same detached, single-flight, budgeted
worker, so nothing here adds to ``git commit`` latency.

* **risk-report**: ``risk-report-<sha>.json`` is keyed by HEAD like the
  before-edit sidecar, and nothing re-persisted it, so ``trw-mcp code risk``
  read a stale commit's report forever. The CLI reuses the HEAD-keyed map the
  before-edit refresh just built, so this is one map walk per sha, not two.
* **incremental ingest** (opt-in, ``post_commit_distill_incremental``): spawns
  ``trw-distill run --incremental --live-ingest`` fully detached. It can run an
  LLM pass, so it is never on by default and never waited on. A lock the child
  holds for its whole lifetime keeps it single-flight: an arrival while a
  previous run is alive spawns nothing (the guard PRD-INFRA-186 put on the sweep).

Zero trw-distill imports: both are external CLI invocations. Fail-open: every
error is logged and reported as a status, never raised.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import structlog

from trw_mcp.tools._distill_spawn import resolve_distill_cli, sanitized_env, spawn_detached, stderr_tail

logger = structlog.get_logger(__name__)

#: Same per-invocation bound as the before-edit refresh; the worker's own
#: wall-clock budget (TRW_POST_COMMIT_BUDGET_SECONDS) still bounds the pass.
RISK_REPORT_TIMEOUT_SECONDS: int = 60

#: ``flock``-ed by the detached incremental run for its whole lifetime.
INCREMENTAL_LOCK_REL: Path = Path(".trw") / "distill" / ".incremental.lock"

#: Echoed as ``spawn_incremental_run``'s status when the ``trw_distill`` package
#: is importable but the console script it ships is not on the sanitized child PATH.
DISTILL_CLI_UNAVAILABLE: str = "distill_cli_unavailable"


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:  # trw-fail-silent-allow: None IS the answer to "does this artifact exist yet"
        return None


def refresh_risk_report(repo_root: Path, source_env: dict[str, str]) -> bool:
    """Re-persist the risk-report sidecar for HEAD; True only when THIS run wrote a current one.

    Same accounting rule as ``count_refreshed_targets``: an exit code is not the
    record. The artifact must exist afterwards, carry HEAD's sha, and have a
    newer mtime than before the call, so a failed run next to an old artifact
    reports False.
    """
    from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL, load_envelope, resolve_git_sha

    sha = resolve_git_sha(repo_root)
    if sha is None:
        return False
    env = sanitized_env(source_env)
    cli = resolve_distill_cli(env)
    if cli is None:
        logger.info("risk_report_refresh_cli_unavailable")
        return False
    artifact = repo_root / DEFAULT_CACHE_DIR_REL / f"risk-report-{sha}.json"
    before = _mtime_ns(artifact)
    argv = (cli, "self-improve", "risk-report", "--repo", str(repo_root), "--persist-sidecar")
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv tuple plus the repo path, no shell
            argv,
            cwd=repo_root,
            env=env,
            capture_output=True,
            check=False,
            timeout=RISK_REPORT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("risk_report_refresh_failed", reason=type(exc).__name__, error=str(exc))
        return False
    if completed.returncode != 0:
        logger.warning(
            "risk_report_refresh_nonzero_exit",
            returncode=completed.returncode,
            stderr_tail=stderr_tail(completed.stderr),
        )
    after = _mtime_ns(artifact)
    if after is None or after == before:
        return False
    envelope = load_envelope(artifact)
    return envelope is not None and envelope.get("sha") == sha


def spawn_incremental_run(repo_root: Path, source_env: dict[str, str], *, enabled: bool) -> str:
    """Spawn the detached incremental distill run; returns what happened.

    ``disabled`` | ``already_running`` | ``spawned`` | ``spawn_failed`` |
    ``lock_unavailable`` | ``distill_cli_unavailable``. The ``flock`` on
    :data:`INCREMENTAL_LOCK_REL` is taken here and handed to the child through
    ``pass_fds``. A flock belongs to the open file description, so it stays
    held while the child keeps its inherited descriptor and the kernel drops
    it when the child exits, however it exits. There is no pid record to go
    stale and no pid reuse to mistake for a live run. The child gets its own
    session and no stdio, so neither this worker nor the commit waits on it.
    """
    if not enabled:
        return "disabled"
    env = sanitized_env(source_env)
    cli = resolve_distill_cli(env)
    if cli is None:
        logger.info("distill_incremental_cli_unavailable")
        return DISTILL_CLI_UNAVAILABLE
    try:
        import fcntl
    except ImportError:  # trw-fail-silent-allow: no flock here means no safe single-flight, so no spawn
        logger.info("distill_incremental_lock_unsupported")
        return "lock_unavailable"
    lock_path = repo_root / INCREMENTAL_LOCK_REL
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        logger.warning("distill_incremental_lock_unavailable", error=str(exc))
        return "lock_unavailable"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("distill_incremental_already_running")
            return "already_running"
        # 2026-09-27 audit (touchpoint #3): --trigger threads through so the
        # child's continuous.jsonl / ingest-status.json record the real
        # invocation context instead of falling through to the CLI's
        # "manual" default (a detached post-commit spawn is anything but).
        argv = (
            cli,
            "run",
            "--repo",
            str(repo_root),
            "--incremental",
            "--live-ingest",
            "--trigger",
            "post_commit",
        )
        try:
            pid = spawn_detached(argv, cwd=repo_root, env=env, pass_fds=(fd,))
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("distill_incremental_spawn_failed", reason=type(exc).__name__, error=str(exc))
            return "spawn_failed"
        logger.info("distill_incremental_spawned", pid=pid)
        return "spawned"
    finally:
        # Closing THIS descriptor never releases a lock the child still holds.
        os.close(fd)


__all__ = [
    "DISTILL_CLI_UNAVAILABLE",
    "INCREMENTAL_LOCK_REL",
    "RISK_REPORT_TIMEOUT_SECONDS",
    "refresh_risk_report",
    "spawn_incremental_run",
]
