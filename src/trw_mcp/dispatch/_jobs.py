"""Background dispatch job registry (files-as-communication).

Belongs to the ``trw_mcp.dispatch`` package. A harness WITHOUT a shell cannot
run :func:`dispatch` synchronously and block on a multi-minute child agent. This
module provides a fire-and-poll model:

- :func:`start_background` spawns a DETACHED child (``python -m
  trw_mcp.dispatch._run_job``) that runs the request, writes its result file,
  and exits — the parent (the MCP server) does not wait on it.
- :func:`get_status` reconciles a job's recorded state with the filesystem: the
  result file appearing means terminal (succeeded / timed_out / failed); a dead
  pid with no result means the child crashed (failed).
- :func:`get_result` and :func:`cancel_job` round out the API.

Storage lives under ``<trw_dir>/runtime/dispatch-jobs/`` with three files per
job: ``<id>.req.json`` (the request), ``<id>.json`` (the :class:`DispatchJob`
record), ``<id>.result.json`` (the :class:`DispatchResult`, written atomically by
the child). The request is transient and the child deletes it after reading and
writing a result. The result file's existence is the authoritative completion
signal.

The prompt body is NEVER persisted to the job record or logged — only the
prompt-redacted ``argv_redacted`` is kept. (The request file does contain the
prompt because the child needs it; it is gitignored runtime state, not a log.)
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.dispatch._env import build_runner_env
from trw_mcp.dispatch._private_io import write_private_atomic
from trw_mcp.dispatch._runner import _redact_argv
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult

logger = structlog.get_logger(__name__)

JobStatus = Literal["running", "succeeded", "failed", "timed_out", "cancelled"]

_TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "timed_out", "cancelled"})

# A child that never wrote a result and whose pid-liveness probe is unreliable
# (non-POSIX, or pid reused) would otherwise hang in ``running`` forever. We
# declare it ``failed`` once wall-clock exceeds the job's own timeout times this
# slack factor — generous enough to cover the runner's own shutdown/kill window.
_STUCK_TTL_FACTOR = 1.5

# Default retention for terminal job files swept by ``_sweep_old_jobs``.
_DEFAULT_JOB_MAX_AGE_DAYS = 7
_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")

# POSIX gate mirrors the runner: process-group / signal-0 liveness probing is only
# available where the primitives exist. Elsewhere we degrade to "assume running
# until a result file appears".
_POSIX = hasattr(os, "killpg") and hasattr(os, "kill") and hasattr(os, "setsid")


class DispatchJob(BaseModel):
    """A persisted background dispatch job record.

    Not frozen: :func:`get_status` / :func:`cancel_job` mutate the in-memory copy
    before persisting the new status. The prompt body is never stored here.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    client: str
    status: JobStatus
    created_at: str = Field(description="ISO-8601 timezone-aware creation timestamp.")
    timeout_s: int = Field(
        default=600,
        gt=0,
        description="The request's wall-clock timeout; used to declare a stuck job failed.",
    )
    pid: int | None = Field(default=None, description="OS pid of the detached child, if spawned.")
    argv_redacted: list[str] = Field(
        default_factory=list,
        description="The launched command with the prompt body redacted.",
    )
    result_path: str = Field(description="Absolute path to the result JSON file.")
    job_path: str = Field(description="Absolute path to this job's record file.")


def _jobs_dir(trw_dir: Path | None) -> Path:
    """Resolve (and create) the dispatch-jobs storage directory."""
    if trw_dir is None:
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
    jobs_dir = trw_dir / "runtime" / "dispatch-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(jobs_dir, 0o700)
    return jobs_dir


def _validate_job_id(job_id: str) -> str:
    if _JOB_ID_RE.fullmatch(job_id) is None:
        raise ValueError("invalid dispatch job_id")
    return job_id


def _job_record_path(jobs_dir: Path, job_id: str) -> Path:
    return jobs_dir / f"{_validate_job_id(job_id)}.json"


def _result_path(jobs_dir: Path, job_id: str) -> Path:
    return jobs_dir / f"{_validate_job_id(job_id)}.result.json"


def _req_path(jobs_dir: Path, job_id: str) -> Path:
    return jobs_dir / f"{_validate_job_id(job_id)}.req.json"


def _child_pid_path(jobs_dir: Path, job_id: str) -> Path:
    """Sidecar file holding the foreign agent's session-leader pid.

    Written by the detached ``_run_job`` child (via the runner's ``pid_callback``)
    and read by :func:`cancel_job` so a cancel can reach the foreign tree. Ends in
    ``.pid`` (not ``.json``) so the ``*.json`` glob in :func:`_sweep_old_jobs`
    never mistakes it for a job record.
    """
    return jobs_dir / f"{_validate_job_id(job_id)}.child.pid"


def _persist(job: DispatchJob, jobs_dir: Path) -> None:
    """Write *job* to its record path atomically (temp file + os.replace)."""
    path = _job_record_path(jobs_dir, job.job_id)
    write_private_atomic(path, job.model_dump_json(indent=2))


def _load_job(jobs_dir: Path, job_id: str) -> DispatchJob:
    """Load a :class:`DispatchJob` record, raising KeyError if it is unknown."""
    path = _job_record_path(jobs_dir, job_id)
    if not path.exists():
        raise KeyError(f"unknown job_id {job_id!r}")
    job = DispatchJob.model_validate_json(path.read_text(encoding="utf-8"))
    if job.job_id != job_id or Path(job.job_path) != path or Path(job.result_path) != _result_path(jobs_dir, job_id):
        raise ValueError("dispatch job record path mismatch")
    return job


def _pid_alive(pid: int | None) -> bool:
    """Best-effort liveness probe for *pid* (POSIX signal-0).

    On non-POSIX (or when pid is unknown) we cannot cheaply probe, so we report
    alive=True and rely on the result file as the terminal signal instead.
    """
    if pid is None or not _POSIX:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - process exists, owned by someone else
        return True
    except OSError:  # pragma: no cover - defensive
        return True
    return True


def start_background(req: DispatchRequest, *, trw_dir: Path | None = None) -> DispatchJob:
    """Spawn a DETACHED child to run *req*, persist a running job, and return it.

    The child is started with ``start_new_session`` (POSIX) so it (and its own
    child agent tree) is fully detached from the MCP server; stdout/stderr are
    sent to ``/dev/null`` since the only durable output is the result file.
    """
    jobs_dir = _jobs_dir(trw_dir)
    # Sweep stale terminal jobs before creating a new one so the directory does
    # not accumulate request prompts / records indefinitely.
    _sweep_old_jobs(jobs_dir)

    # Full 32-char uuid hex: a truncated id risks a collision that would let one
    # job's record/result clobber another's.
    job_id = uuid.uuid4().hex

    req_path = _req_path(jobs_dir, job_id)
    result_path = _result_path(jobs_dir, job_id)
    job_path = _job_record_path(jobs_dir, job_id)
    child_pid_path = _child_pid_path(jobs_dir, job_id)

    write_private_atomic(req_path, req.model_dump_json(indent=2))

    # The 4th argv element is the foreign-agent pid sidecar path; the child writes
    # the session-leader pid there so cancel_job can reach the whole foreign tree.
    run_argv = [
        sys.executable,
        "-m",
        "trw_mcp.dispatch._run_job",
        str(req_path),
        str(result_path),
        str(child_pid_path),
    ]

    # Redact the prompt from the recorded argv: the same redaction the runner
    # applies to its own argv_redacted. argv here is purely descriptive.
    argv_redacted = _redact_argv(run_argv, req.prompt)

    try:
        proc = subprocess.Popen(
            run_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=_POSIX,
            # Minimal env (NOT the full host os.environ): only the per-client
            # allowlist + PYTHONPATH/VIRTUAL_ENV the ``python -m`` import needs. This
            # keeps host secrets out of both the intermediate and the foreign agent.
            env=build_runner_env(req.client),
        )
    except BaseException:
        try:
            req_path.unlink()
        except OSError:
            pass
        raise

    job = DispatchJob(
        job_id=job_id,
        client=req.client,
        status="running",
        created_at=datetime.now(timezone.utc).isoformat(),
        timeout_s=req.timeout_s,
        pid=proc.pid,
        argv_redacted=argv_redacted,
        result_path=str(result_path),
        job_path=str(job_path),
    )
    _persist(job, jobs_dir)
    logger.info(
        "dispatch_job_started",
        job_id=job_id,
        client=req.client,
        pid=proc.pid,
    )
    # Keep the Popen object alive and reap the detached child when it exits.
    # Detaching the process group does not transfer waitpid ownership; dropping
    # the object here would emit ResourceWarning and can leave a zombie child.
    threading.Thread(target=proc.wait, name=f"trw-dispatch-reaper-{job_id}", daemon=True).start()
    return job


def get_result(job_id: str, *, trw_dir: Path | None = None) -> DispatchResult | None:
    """Return the parsed :class:`DispatchResult` if the result file exists, else None."""
    jobs_dir = _jobs_dir(trw_dir)
    return _load_result(jobs_dir, job_id)


def _load_result(jobs_dir: Path, job_id: str) -> DispatchResult | None:
    """Load a result from an already-resolved jobs directory."""
    path = _result_path(jobs_dir, job_id)
    if not path.exists():
        return None
    return DispatchResult.model_validate_json(path.read_text(encoding="utf-8"))


def _stuck_past_ttl(job: DispatchJob, *, now: datetime) -> bool:
    """True if a running job has exceeded ``timeout_s * _STUCK_TTL_FACTOR``.

    Guards against a child that never wrote a result AND whose pid-liveness probe
    is unreliable (non-POSIX, or a reused pid) — without this it would stay
    ``running`` forever. A malformed ``created_at`` is treated as not-stuck (the
    pid path still applies) rather than crashing the poller.
    """
    try:
        created = datetime.fromisoformat(job.created_at)
    except ValueError:  # pragma: no cover - defensive against a corrupt record
        return False
    deadline = created + timedelta(seconds=job.timeout_s * _STUCK_TTL_FACTOR)
    return now > deadline


def _cleanup_on_terminal(jobs_dir: Path, job_id: str) -> None:
    """Delete the request + child-pid sidecar whenever a job is known terminal.

    The prompt body lives in ``<id>.req.json`` only because the detached child
    needs it; once the job is terminal it is dead weight, so removing it reduces
    how long the prompt sits on disk. The ``<id>.child.pid`` sidecar is likewise
    only useful for an in-flight cancel. Best-effort: a missing/locked file is fine.
    """
    for path in (_req_path(jobs_dir, job_id), _child_pid_path(jobs_dir, job_id)):
        try:
            path.unlink()
        except OSError:  # pragma: no cover - already gone / racing
            pass


def _reconcile_job(job: DispatchJob, jobs_dir: Path, *, now: datetime) -> DispatchJob:
    """Persist any terminal transition visible in result, pid, or wall-clock state."""
    if job.status in _TERMINAL_STATUSES:
        _cleanup_on_terminal(jobs_dir, job.job_id)
        return job

    result = _load_result(jobs_dir, job.job_id)
    if result is not None:
        if result.ok:
            job.status = "succeeded"
        elif result.timed_out:
            job.status = "timed_out"
        else:
            job.status = "failed"
    elif not _pid_alive(job.pid):
        # The intermediate child already exited without writing a result — it
        # crashed. Nothing to signal.
        job.status = "failed"
    elif _stuck_past_ttl(job, now=now):
        # The pid probe still reports "alive" (or is unreliable) yet wall-clock is
        # past timeout_s * _STUCK_TTL_FACTOR: the child is wedged. Reap its tree
        # (intermediate _run_job group + foreign-agent session via the sidecar)
        # BEFORE cleanup deletes the sidecar — otherwise both leak permanently.
        _kill_job_tree(job, jobs_dir)
        job.status = "failed"
    else:
        return job

    _persist(job, jobs_dir)
    _cleanup_on_terminal(jobs_dir, job.job_id)
    return job


def get_status(job_id: str, *, trw_dir: Path | None = None) -> DispatchJob:
    """Reconcile and return a job's current status.

    Terminal transitions (persisted on first observation):

    - result file present -> succeeded (``result.ok``) / timed_out
      (``result.timed_out``) / failed (otherwise).
    - no result + pid not alive -> failed (the child crashed without writing one).
    - no result + still "alive" but past ``timeout_s * 1.5`` wall-clock -> failed
      (stuck-running TTL: covers an unreliable pid probe).

    Whenever a terminal status is observed or reached, transient request and pid
    files are cleaned idempotently. Otherwise the status stays ``running``. An
    already-terminal recorded status (e.g. ``cancelled``) is returned unchanged.
    """
    jobs_dir = _jobs_dir(trw_dir)
    job = _load_job(jobs_dir, job_id)
    return _reconcile_job(job, jobs_dir, now=datetime.now(timezone.utc))


def _kill_child_tree(jobs_dir: Path, job_id: str) -> None:
    """Best-effort kill the foreign agent's process group via its pid sidecar.

    The foreign agent is spawned by :func:`dispatch` with ``start_new_session``,
    so it is its OWN session leader: ``killpg(getpgid(child_pid), SIGKILL)``
    targets its entire tree. The sidecar may be absent (child never wrote it /
    already swept), the pid may be gone or reused — all benign, so every failure
    mode is swallowed. POSIX only.
    """
    if not _POSIX:
        return
    pid_path = _child_pid_path(jobs_dir, job_id)
    try:
        child_pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.killpg(os.getpgid(child_pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError, ValueError):
        # absent/unreadable sidecar, malformed pid, or already-gone process.
        pass


def _kill_job_tree(job: DispatchJob, jobs_dir: Path) -> None:
    """Best-effort kill BOTH the intermediate ``_run_job`` group and the foreign tree.

    Two kills, both best-effort:

    1. ``killpg`` of the intermediate ``_run_job`` child's process group
       (``job.pid``).
    2. ``killpg`` of the FOREIGN agent's own session group, read from the
       ``<id>.child.pid`` sidecar the child recorded via the runner's
       ``pid_callback`` (F-11). The foreign agent is spawned with
       ``start_new_session`` so it lives in its OWN session/group and would
       otherwise survive a kill that only reaps the intermediate's group.

    Every failure mode (already-gone pid, absent/unreadable sidecar, non-POSIX)
    is swallowed — this is a reaping best-effort, not a guarantee. Shared by
    :func:`cancel_job` and the stuck-running TTL branch of :func:`_reconcile_job`
    so both reap the child tree identically instead of orphaning it.
    """
    if job.pid is not None and _POSIX:
        try:
            os.killpg(os.getpgid(job.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):  # benign: already gone
            pass

    # Also reach the foreign agent (its own session leader) via the pid sidecar.
    _kill_child_tree(jobs_dir, job.job_id)


def cancel_job(job_id: str, *, trw_dir: Path | None = None) -> DispatchJob:
    """Kill the job's process tree (intermediate + foreign agent) and mark cancelled.

    The two best-effort kills are delegated to :func:`_kill_job_tree`. The
    verified runner timeout-tree-kill is untouched — cancel reach is added purely
    via the sidecar, not by changing how the runner spawns the child.
    """
    jobs_dir = _jobs_dir(trw_dir)
    job = _load_job(jobs_dir, job_id)

    _kill_job_tree(job, jobs_dir)

    job.status = "cancelled"
    _persist(job, jobs_dir)
    _cleanup_on_terminal(jobs_dir, job_id)
    logger.info("dispatch_job_cancelled", job_id=job_id, pid=job.pid)
    return job


def _sweep_old_jobs(jobs_dir: Path, *, max_age_days: int = _DEFAULT_JOB_MAX_AGE_DAYS) -> None:
    """Remove retained artifacts for terminal jobs older than ``max_age_days``.

    Called at the start of :func:`start_background` so the dispatch-jobs directory
    self-prunes: a genuinely live, non-stuck job is kept, and a fresh terminal
    job inside the retention window is kept. Best-effort — a malformed record or
    an OS error on unlink is skipped, never raised.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)
    for path in jobs_dir.glob("*.json"):
        if path.name.endswith(".result.json") or path.name.endswith(".req.json"):
            continue
        try:
            job = _load_job(jobs_dir, path.stem)
        except (KeyError, ValueError, OSError):  # skip a malformed/partial record
            continue
        try:
            job = _reconcile_job(job, jobs_dir, now=now)
        except (ValueError, OSError):  # malformed result or failed best-effort persistence
            continue
        if job.status not in _TERMINAL_STATUSES:
            continue
        try:
            created = datetime.fromisoformat(job.created_at)
        except ValueError:  # pragma: no cover - corrupt timestamp
            continue
        if created > cutoff:
            continue
        for p in (
            _job_record_path(jobs_dir, job.job_id),
            _result_path(jobs_dir, job.job_id),
            _req_path(jobs_dir, job.job_id),
            _child_pid_path(jobs_dir, job.job_id),
        ):
            try:
                p.unlink()
            except OSError:  # pragma: no cover - already gone
                pass
