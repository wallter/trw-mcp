"""Starting the trw-distill CLI from trw-mcp, and requesting the detached sidecar rebuild.

Responsibility: every trw-mcp call site that runs the proprietary
``trw-distill`` console script goes through here, so the child environment,
the CLI lookup and the detached-spawn contract are decided once. It also
decides when the pre-edit hint or post-commit REQUESTS a whole-repo
``trw-distill self-improve refresh-sidecars`` build (8.2 T2, design §3).

Interface:

- :func:`request_rebuild_if_due` — given a batch-sidecar lookup, request a
  detached rebuild when one is due; returns a :class:`RebuildRequest`.
- :func:`rebuild_reason` — the trigger rule alone (why a rebuild is due, or None).
- :func:`sanitized_env` — the child environment, projected onto an allowlist.
- :func:`resolve_distill_cli` — the ``trw-distill`` executable, or None.
- :func:`spawn_detached` — start a child in its own session with no stdio,
  never waited on.
- :func:`distill_available` — whether the proprietary package is installed.
- :func:`stderr_tail` — a bounded, decoded stderr tail for a log line.

Invariants:

- This module never imports ``trw_distill`` (trw-mcp is PUBLIC; the CLI is
  invoked as an external process), and no child inherits the full parent
  environment.
- A rebuild request never waits for the build: it costs one
  config read, one entitlement check, one stamp read and write, two PATH
  lookups and one fork/exec.
- It fails CLOSED (spawns nothing, logs why, names the flag) when the flag is
  off, under the reviewer role, without the distill entitlement, inside the
  minimum interval, or when ``trw-distill`` or ``nice`` cannot be found.
- The child's environment carries ``TRW_SURFACE_ROLE`` when the caller's does;
  the CLI refuses to build under ``reviewer``.
- Single-flight is the CLI's own flock in the cache dir; this module adds the
  rate limit, a timestamp file written atomically BEFORE the spawn so a failed
  spawn still waits out the interval.

Knobs (``TRWConfig``): ``hint_sidecar_auto_refresh_enabled`` (the gate),
``hint_sidecar_rebuild_after_commits`` (the stale-ancestor trigger, capped at
``hint_sidecar_max_commits_behind``),
``hint_sidecar_rebuild_min_interval_minutes`` (the rate limit).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from trw_mcp.tools._sidecar_substrate import CurrentSidecarResult

logger = structlog.get_logger(__name__)

#: Env vars a trw-distill child legitimately needs. Everything else is
#: dropped — a git hook inherits the committing shell's full environment, and
#: full-env passthrough to a child process previously enabled secret
#: exfiltration via probe stdout (.claude/rules/trw-mcp-python.md).
_ENV_ALLOWLIST: tuple[str, ...] = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONPATH",
    "TMPDIR",
    "TRW_CLIENT_PROFILE",
    "TRW_PROJECT_DIR",
    "VIRTUAL_ENV",
)

#: How much of a failing subprocess's stderr to keep in the maintainer log.
#: Enough to name the cause, bounded so a runaway traceback cannot flood the
#: sink. Local log file only — never a tool response, never telemetry.
_STDERR_TAIL_CHARS: int = 300

#: The console script trw-distill installs.
DISTILL_CLI_NAME: str = "trw-distill"


def sanitized_env(source: Mapping[str, str]) -> dict[str, str]:
    """Project *source* onto the explicit allowlist (NFR03).

    Never ``os.environ.copy()``: a git hook runs with the committing user's
    full environment, including credentials that a third-party CLI has no
    business receiving.
    """
    return {key: source[key] for key in _ENV_ALLOWLIST if key in source}


def stderr_tail(stderr: object) -> str:
    """Bounded, decoded tail of a subprocess's stderr ('' when unavailable)."""
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace")
    elif isinstance(stderr, str):
        text = stderr
    else:
        return ""
    return text.strip()[-_STDERR_TAIL_CHARS:]


def distill_available() -> bool:
    """Whether the trw-distill sidecar feature is installed/entitled."""
    try:
        from trw_mcp.tools._sidecar_substrate import distill_installed

        return bool(distill_installed())
    except Exception:  # trw-fail-silent-allow: fail-open, absence is the free-tier norm
        logger.debug("sidecar_refresh_distill_probe_failed", exc_info=True)
        return False


#: ``shutil.which``'s shape: ``(command, search path or None) -> executable or None``.
Which = Callable[[str, "str | None"], "str | None"]


def _which(command: str, path: str | None) -> str | None:
    return shutil.which(command, path=path)


def resolve_distill_cli(env: Mapping[str, str], *, interpreter_bin: bool = False, which: Which = _which) -> str | None:
    """Resolve the ``trw-distill`` console script on *env*'s ``PATH``, once.

    Callers previously built an argv of the bare string ``"trw-distill"`` and
    let ``subprocess`` do the PATH search, which conflates two questions: "is
    the proprietary package importable" (:func:`distill_available`) and "is its
    console script on the PATH a child process would search". They diverge in
    practice — a dev worktree's venv can have ``trw_distill`` importable while
    ``<venv>/bin`` is absent from the committing shell's PATH, so every spawn
    failed with ``OSError`` even though the package itself was present.

    Resolving once, before any subprocess is built, lets the caller record a
    truthful status instead of attempting and swallowing a doomed spawn.
    ``interpreter_bin=True`` also searches the running interpreter's own bin
    directory (a venv's ``bin``), after ``PATH``: an edit hook runs that
    interpreter directly, often with the venv absent from ``PATH``.
    """
    path = env.get("PATH")
    if interpreter_bin:
        path = os.pathsep.join(part for part in (path, str(Path(sys.executable).parent)) if part)
    return which(DISTILL_CLI_NAME, path)


#: ``subprocess.Popen``'s shape, as far as :func:`spawn_detached` uses it.
Popen = Callable[..., Any]


def spawn_detached(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    pass_fds: tuple[int, ...] = (),
    popen: Popen | None = None,
) -> int:
    """Start *argv* in its own session with no stdio, and return its pid without waiting.

    ``start_new_session=True`` puts the child in its own session and process
    group, so neither the caller's exit nor a signal to the caller's group
    reaches it; stdin, stdout and stderr are ``DEVNULL``, so nothing blocks on
    a pipe. The child is never waited on. Raises ``OSError`` or
    ``subprocess.SubprocessError`` when the spawn itself fails. *popen* is the
    test port; None means ``subprocess.Popen``, looked up at call time.
    """
    child = (popen or subprocess.Popen)(  # argv is a caller-built list, never a shell string
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=pass_fds,
    )
    return int(child.pid)


# -- The detached sidecar rebuild request (8.2 T2 S2b) ----------------------

#: Named in every rebuild-request log event (operator rule: a flagged feature names its off-switch).
AUTO_REFRESH_FLAG = "disable with hint_sidecar_auto_refresh_enabled: false in .trw/config.yaml"
#: The rate-limit timestamp, in the cache dir the build writes to.
REBUILD_STAMP_NAME = "rebuild-requested.json"
#: Scheduling priority of the build: it yields the CPU to the editor and to the hint itself.
_REBUILD_NICENESS = 10
_ROLE_ENV = "TRW_SURFACE_ROLE"
#: Statuses of the batch lookup that mean no usable sidecar exists at all.
_NO_USABLE_SIDECAR = frozenset({"sidecar_missing", "sidecar_too_far_behind"})

RebuildTrigger = Literal["hint", "post-commit"]
RebuildStatus = Literal[
    "spawned",
    "not_due",
    "disabled",
    "reviewer_role",
    "no_entitlement",
    "min_interval",
    "cli_unavailable",
    "nice_unavailable",
    "stamp_unwritable",
    "spawn_failed",
]


@dataclass(frozen=True)
class SpawnPorts:
    """The process, PATH and clock ports a rebuild request uses; tests pass fakes."""

    popen: Popen | None = None
    which: Which = _which
    clock: Callable[[], float] = time.time


#: The production ports; a test replaces this to drive the hint's own call site.
DEFAULT_PORTS = SpawnPorts()


@dataclass(frozen=True)
class RebuildRequest:
    """What one rebuild request did: ``status``, the trigger ``reason`` and, when spawned, the child ``pid``."""

    status: RebuildStatus
    reason: str = ""
    pid: int | None = None
    argv: tuple[str, ...] = field(default=())


def rebuild_reason(status: str, commits_behind: int | None, *, after_commits: int) -> str | None:
    """Why the batch-sidecar lookup that produced *status* calls for a rebuild, or None when it does not.

    No usable sidecar (``sidecar_missing``, ``sidecar_too_far_behind``) is due;
    so is a served ancestor at least *after_commits* behind HEAD. A fresh hint,
    and every refusal a rebuild would not fix (tier, repo, malformed, git), is not.
    """
    if status in _NO_USABLE_SIDECAR:
        return status
    if status == "hint_available_stale" and commits_behind is not None and commits_behind >= after_commits:
        return f"commits_behind>={after_commits}"
    return None


def request_rebuild_if_due(
    lookup: CurrentSidecarResult,
    *,
    cache_dir: str | Path | None,
    trigger: RebuildTrigger,
    source_env: Mapping[str, str] | None = None,
    ports: SpawnPorts | None = None,
) -> RebuildRequest:
    """Request a detached ``refresh-sidecars`` build when *lookup* (a batch-sidecar resolution) says one is due.

    *cache_dir* is the directory the caller read (None: the shared cache the
    ancestor read path resolves). Never waits; a refusal or a failed spawn is a
    status, not an exception (see the module invariants for every such path).
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    if not config.hint_sidecar_auto_refresh_enabled:
        return _refused("disabled", trigger, "")
    behind = lookup.ancestor.commits_behind if lookup.ancestor is not None else None
    after = min(config.hint_sidecar_rebuild_after_commits, config.hint_sidecar_max_commits_behind)
    reason = rebuild_reason(lookup.status, behind, after_commits=after)
    if reason is None or lookup.repo_root is None:
        return RebuildRequest(status="not_due")
    target = Path(cache_dir) if cache_dir is not None else _shared_cache_dir(lookup.repo_root)
    return _request(
        lookup.repo_root,
        target,
        trigger=trigger,
        reason=reason,
        interval_s=config.hint_sidecar_rebuild_min_interval_minutes * 60.0,
        source_env=os.environ if source_env is None else source_env,
        ports=ports or DEFAULT_PORTS,
    )


def _shared_cache_dir(repo_root: Path) -> Path:
    from trw_mcp.tools._sidecar_ancestry import shared_cache_dir
    from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL

    return shared_cache_dir(repo_root, DEFAULT_CACHE_DIR_REL)


def _request(
    repo_root: Path,
    cache_dir: Path,
    *,
    trigger: RebuildTrigger,
    reason: str,
    interval_s: float,
    source_env: Mapping[str, str],
    ports: SpawnPorts,
) -> RebuildRequest:
    """The fail-closed checks, the stamp, then the spawn."""
    from trw_mcp.state._entitlements import DISTILL_SIDECAR_FEATURE
    from trw_mcp.state._surface_role import reviewer_role_active
    from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

    if reviewer_role_active():
        return _refused("reviewer_role", trigger, reason)
    if not check_tier_for_feature(repo_root, DISTILL_SIDECAR_FEATURE).allowed:
        return _refused("no_entitlement", trigger, reason)
    now = ports.clock()
    last = _read_stamp(cache_dir)
    if last is not None and 0.0 <= now - last < interval_s:
        return _refused("min_interval", trigger, reason, seconds_since_last=round(now - last, 1))
    env = sanitized_env(source_env)
    if source_env.get(_ROLE_ENV):
        env[_ROLE_ENV] = source_env[_ROLE_ENV]
    cli = resolve_distill_cli(env, interpreter_bin=True, which=ports.which)
    if cli is None:
        return _refused("cli_unavailable", trigger, reason)
    nice = ports.which("nice", os.pathsep.join(part for part in (env.get("PATH"), os.defpath) if part))
    if nice is None:
        return _refused("nice_unavailable", trigger, reason)
    if not _write_stamp(cache_dir, now, trigger):
        return _refused("stamp_unwritable", trigger, reason, cache_dir=str(cache_dir))
    argv = (
        *(nice, "-n", str(_REBUILD_NICENESS), cli, "self-improve", "refresh-sidecars"),
        *("--repo", str(repo_root), "--cache-dir", str(cache_dir), "--trigger", trigger),
    )
    try:
        pid = spawn_detached(argv, cwd=repo_root, env=env, popen=ports.popen)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "sidecar_rebuild_request",
            outcome="spawn_failed",
            trigger=trigger,
            reason=reason,
            error=f"{type(exc).__name__}: {exc}",
            disable=AUTO_REFRESH_FLAG,
        )
        return RebuildRequest(status="spawn_failed", reason=reason, argv=argv)
    logger.info(
        "sidecar_rebuild_request", outcome="spawned", trigger=trigger, reason=reason, pid=pid, disable=AUTO_REFRESH_FLAG
    )
    return RebuildRequest(status="spawned", reason=reason, pid=pid, argv=argv)


def _refused(status: RebuildStatus, trigger: str, reason: str, **detail: object) -> RebuildRequest:
    """Log one refusal (debug for the per-edit routine ones, info otherwise) and return it."""
    log = logger.debug if status in ("disabled", "min_interval") else logger.info
    log("sidecar_rebuild_request", outcome=status, trigger=trigger, reason=reason, disable=AUTO_REFRESH_FLAG, **detail)
    return RebuildRequest(status=status, reason=reason)


#: Upper bound on the rebuild stamp read (it holds one small JSON object).
_STAMP_MAX_BYTES = 4096


def _read_stamp(cache_dir: Path) -> float | None:
    """When the last rebuild was requested (epoch seconds), or None when never or unreadable."""
    try:
        # Bounded read: the stamp is ~60 bytes; a huge or special file must never stall the hint.
        with (cache_dir / REBUILD_STAMP_NAME).open("rb") as handle:
            head = handle.read(_STAMP_MAX_BYTES + 1)
        if len(head) > _STAMP_MAX_BYTES:
            raise ValueError(f"stamp larger than {_STAMP_MAX_BYTES} bytes")
        raw: object = json.loads(head.decode("utf-8"))
    except FileNotFoundError:  # trw-fail-silent-allow: no stamp means no rebuild was ever requested here
        return None
    except (OSError, ValueError) as exc:
        logger.info(
            "sidecar_rebuild_stamp_unreadable", error=str(exc), outcome="treated_as_elapsed", disable=AUTO_REFRESH_FLAG
        )
        return None
    value = raw.get("requested_at_unix") if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        logger.info("sidecar_rebuild_stamp_malformed", outcome="treated_as_elapsed", disable=AUTO_REFRESH_FLAG)
        return None
    return float(value)


def _write_stamp(cache_dir: Path, now: float, trigger: str) -> bool:
    """Atomically replace the stamp (temp file, then rename); False when the cache dir is unwritable."""
    tmp: str | None = None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".rebuild-requested-", suffix=".tmp", dir=cache_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"requested_at_unix": now, "trigger": trigger, "pid": os.getpid()}, handle)
        os.replace(tmp, cache_dir / REBUILD_STAMP_NAME)
    except OSError as exc:
        logger.info("sidecar_rebuild_stamp_unwritable", error=str(exc), outcome="no_spawn", disable=AUTO_REFRESH_FLAG)
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)
        return False
    return True


__all__ = [
    "AUTO_REFRESH_FLAG",
    "DEFAULT_PORTS",
    "DISTILL_CLI_NAME",
    "REBUILD_STAMP_NAME",
    "Popen",
    "RebuildRequest",
    "RebuildStatus",
    "RebuildTrigger",
    "SpawnPorts",
    "Which",
    "distill_available",
    "rebuild_reason",
    "request_rebuild_if_due",
    "resolve_distill_cli",
    "sanitized_env",
    "spawn_detached",
    "stderr_tail",
]
