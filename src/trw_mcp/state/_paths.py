# ruff: noqa: E402
"""Shared path resolution -- single source of truth for project root, .trw dir, and run paths.

All modules that need to resolve TRW_PROJECT_ROOT, the .trw directory,
or an active run path MUST use these functions instead of inline logic.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.state._path_context_probe import (
    _FASTMCP_CTX_PROBES as _FASTMCP_CTX_PROBES,
)
from trw_mcp.state._path_context_probe import (
    _extract_fastmcp_session_id as _extract_fastmcp_session_id,
)
from trw_mcp.state._path_context_probe import (
    _ProbeOutcome as _ProbeOutcome,
)
from trw_mcp.state._path_context_probe import (
    _walk_ctx_attrs as _walk_ctx_attrs,
)
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


# PRD-FIX-085 FR02: HOT_PATH ContextVar marks the request-scope window
# during which the legacy mtime scan is forbidden. Set on entry to
# trw_session_start and middleware handlers; reset on exit.
HOT_PATH: ContextVar[bool] = ContextVar("trw_hot_path", default=False)


class HotPathLegacyScanError(RuntimeError):
    """Raised when find_run_via_mtime_scan() is called from the hot path.

    Only raised when ``TRW_HOT_PATH_STRICT=1`` is set in the environment;
    in default (non-strict) mode, the violation is logged at WARN.
    """


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger(__name__)


# File/dir permission hardening (PRD-QUAL-110-FR02) lives in the
# ``trw_mcp.state._paths_permissions`` sibling (``harden_dir_mode`` /
# ``harden_secret_file_mode``). Import it from there directly; it is kept out of
# this module to hold _paths.py under the 350-effective-LOC gate.


def _get_config() -> Any:
    """Lazy-load TRWConfig to avoid import cycles during path-only imports."""
    from trw_mcp.models.config import get_config

    return get_config()


# Backward-compatible patch point for tests and integrations that monkeypatch
# trw_mcp.state._paths.get_config directly. Runtime code should still call
# _get_config() so config import remains lazy at module import time.
get_config = _get_config


def __getattr__(name: str) -> Any:
    """Backward-compat shim for removed module-level singletons (FIX-044).

    Return type is ``Any`` (not ``object``) so Pyright does not widen every
    imported symbol to ``object`` via fallback resolution through this
    hook.  mypy --strict remains clean because ``Any`` is compatible with
    every return site.
    """
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


# --- Session identity (PRD-FIX-042 FR03) ---
_session_lock = threading.Lock()
_session_id: str = uuid.uuid4().hex


def get_session_id() -> str:
    """Return the current process session ID."""
    with _session_lock:
        return _session_id


def _reset_session_id(new_id: str | None = None) -> None:
    """Reset the session ID (for testing). Generates a new one if *new_id* is None."""
    global _session_id
    with _session_lock:
        _session_id = new_id if new_id is not None else uuid.uuid4().hex


# --- Per-connection pin isolation (PRD-CORE-141 FR01/FR02) ---
# TRWCallContext carries the resolved per-call identity plus diagnostics.
# resolve_pin_key implements a four-layer precedence ordering:
#   1. explicit arg   2. TRW_SESSION_ID env    3. FastMCP ctx    4. process UUID
# Wave 1 wires the plumbing only; tool handlers still use pin_active_run's
# legacy session_id path until Wave 3 migrates the helper signatures.


@dataclass(frozen=True)
class TRWCallContext:
    """Frozen value object carrying resolved per-call session identity.

    Fields
    ------
    session_id:
        Resolved pin-key (the string returned by :func:`resolve_pin_key`).
        This is what pin / find / resolve helpers key off.
    client_hint:
        Optional client identity hint (``"cursor-ide"`` | ``"cursor-cli"`` |
        ``"claude-code"`` | ``None``).  Used by analytics and nudge sizing;
        never authoritative for identity.
    explicit:
        ``True`` when the caller supplied the pin-key directly (tests,
        advanced integrations), ``False`` when auto-resolved from env/ctx/
        process.  Surfaced in structured logs for diagnostics.
    fastmcp_session:
        Raw pre-resolution value harvested from FastMCP context (if any),
        preserved for debugging FastMCP API drift.  ``None`` when ctx was
        absent or every probe returned None.
    """

    session_id: str
    client_hint: str | None
    explicit: bool
    fastmcp_session: str | None


def resolve_pin_key(ctx: object | None, explicit: str | None = None) -> str:
    """Resolve the pin-key for the current call via four-layer fallback.

    Precedence (strict):
      1. *explicit* arg — caller-supplied, wins unconditionally.
      2. ``TRW_SESSION_ID`` env var — operator-forced identity / subprocess
         inheritance.
      3. FastMCP :class:`~fastmcp.Context` probing via
         :func:`_extract_fastmcp_session_id`.
      4. Process-level :data:`_session_id` UUID — legacy fallback for
         stdio-per-instance clients.

    When ``config.ctx_isolation_enabled`` is ``False`` the resolver
    short-circuits to the process UUID regardless of *ctx* — matches the
    pre-PRD-CORE-141 behavior (Wave 3 rollback kill-switch).

    Every layer emits a ``pin_resolved`` structured log with a ``source``
    field (``explicit`` | ``env`` | ``ctx`` | ``process``).  When
    ``source=ctx``, a ``ctx_attr_path`` field names the probe that matched.

    Parameters
    ----------
    ctx:
        FastMCP Context object (or ``None``).  Typed as ``object`` to avoid
        a hard runtime dependency on FastMCP; attribute access is defensive.
    explicit:
        Caller-supplied pin-key override.  When non-empty, beats every
        lower layer unconditionally.

    Returns
    -------
    str
        The resolved pin-key.  Callers construct the :class:`TRWCallContext`
        value object separately from this string plus their own diagnostics.
    """
    # Kill switch — skip ctx isolation entirely when operators disable it.
    try:
        kill_switch_enabled = not bool(get_config().ctx_isolation_enabled)
    except Exception:  # justified: config unavailable must not break pin resolution
        kill_switch_enabled = False
        _runtime_logger().debug("ctx_isolation_config_unavailable", exc_info=True)

    if kill_switch_enabled:
        process_key = get_session_id()
        _runtime_logger().info(
            "pin_resolved",
            source="process",
            kill_switch=True,
            pin_key=process_key,
        )
        return process_key

    # Layer 1 — explicit arg
    if explicit:
        _runtime_logger().info("pin_resolved", source="explicit", pin_key=explicit)
        return explicit

    # Layer 2 — TRW_SESSION_ID env var
    env_id = os.environ.get("TRW_SESSION_ID")
    if env_id:
        _runtime_logger().info("pin_resolved", source="env", pin_key=env_id)
        return env_id

    # Layer 3 — FastMCP Context probing
    if ctx is not None:
        ctx_id = _extract_fastmcp_session_id(ctx)
        if ctx_id is not None:
            # Recover which probe matched by re-walking (cheap; attribute
            # probes are O(1) and this is off the hot path).
            matched_path = "unknown"
            for path in _FASTMCP_CTX_PROBES:
                outcome = _walk_ctx_attrs(ctx, path)
                if outcome.broken:
                    continue
                if isinstance(outcome.value, str) and outcome.value == ctx_id:
                    matched_path = ".".join(path)
                    break
            _runtime_logger().info(
                "pin_resolved",
                source="ctx",
                ctx_attr_path=matched_path,
                pin_key=ctx_id,
            )
            return ctx_id

    # Layer 4 — process-level UUID fallback
    process_key = get_session_id()
    _runtime_logger().info("pin_resolved", source="process", pin_key=process_key)
    return process_key


# --- Per-session run pinning (PRD-FIX-042 FR06, PRD-CORE-141 FR04) ---
# Production storage lives at ``.trw/runtime/pins.json`` via the
# :mod:`trw_mcp.state._pin_store` module.  The implementation there uses
# atomic writes (``os.replace``) guarded by the portable file-lock shim
# (``from trw_mcp._locking import _lock_ex, _lock_un``) and enforces
# mode 0o600 on the pins file (NFR03).  Cache invalidation
# (``_pin_store_cache = None`` immediately after every ``os.replace``)
# is non-negotiable — see _pin_store.save_pin_store for details.  Load
# stale-path eviction emits ``pin_stale_run_path_evicted`` WARN logs;
# malformed JSON fails open with
# ``pin_store_malformed_fallback``.
#
# The in-memory ``_pinned_runs`` dict below is RETAINED ONLY for
# conftest compat — legacy tests call ``_pinned_runs.clear()`` directly
# via ``from trw_mcp.state._paths import _pinned_runs``.  Production
# reads/writes flow through the on-disk store; this dict is never
# consulted during tool calls.
_pinned_runs: dict[str, Path] = {}


def _resolve_session_id(
    context: TRWCallContext | None,
    session_id: str | None,
) -> str:
    """Resolve the effective pin-key for a helper call.

    Precedence (PRD-CORE-141 FR01, API Changes):
    1. ``context.session_id`` — ctx-aware callers (tool handlers wired
       to FastMCP ``Context``) always win.
    2. ``session_id`` — legacy kwarg, preserved for backward compat with
       direct Python callers and tests.
    3. Process-level :data:`_session_id` UUID — final fallback.
    """
    if context is not None:
        return context.session_id
    if session_id is not None:
        return session_id
    return get_session_id()


# Pin management helpers extracted to _paths_pin_mgmt (PRD-DIST-243 batch 26).
# Re-exported for back-compat with all callers (build/_registration, middleware,
# tools/report, tools/review, telemetry).
from trw_mcp.state._paths_pin_mgmt import (
    get_pinned_run as get_pinned_run,
)
from trw_mcp.state._paths_pin_mgmt import (
    pin_active_run as pin_active_run,
)
from trw_mcp.state._paths_pin_mgmt import (
    unpin_active_run as unpin_active_run,
)


def resolve_memory_store_path() -> Path:
    """Resolve the SECONDARY sqlite-vec embedding-sidecar database path.

    PRD-INFRA-102 FR-03 clarification (2026-05-04): this returns the path
    to the embedding-sidecar database used by ``dedup.py`` re-indexing via
    ``MemoryStore`` (default ``<trw_dir>/memory/vectors.db``). It is NOT
    the primary memory store path — that is hardcoded to
    ``<trw_dir>/memory/memory.db`` in ``_memory_connection.get_backend``
    and contains the canonical ``vec_memories`` table. The
    ``MemoryStore`` schema uses ``vec_entries`` table prefix instead.

    Strips the ``.trw/`` prefix from the configured ``memory_store_path``
    and joins with the resolved .trw directory.

    Returns:
        Absolute path to the secondary sqlite-vec database file.
    """
    config = get_config()
    memory_store_path = str(config.memory_store_path)
    return resolve_trw_dir() / memory_store_path.removeprefix(".trw/")


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
    return resolve_project_root() / str(config.trw_dir)


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


def find_active_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Find the active run directory for a session — pin-only.

    PRD-FIX-085 FR01: This function is now PIN-ONLY by default. The
    legacy mtime-scan fallback that previously kicked in when
    ``context is None`` has been moved to :func:`find_run_via_mtime_scan`.
    Five regressions in one week shared the same root cause -- a hot-path
    caller forgot ``context=`` and silently routed to the slow scan path.
    Removing the implicit fallback eliminates the regression class.

    Resolution: returns the pinned run for the caller's session, or
    ``None`` when no pin exists. Never scans disk.

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred,
            PRD-CORE-141 FR01).
        session_id: Legacy kwarg; passed through to the pin lookup only.

    Returns:
        Path to pinned run directory, or ``None`` when no pin exists.

    See Also:
        :func:`get_pinned_run` -- equivalent for callers that don't need
        the legacy session_id kwarg.
        :func:`find_run_via_mtime_scan` -- explicit legacy mtime-scan,
        for one-shot CLI tools with no session context. Emits a WARN
        (or raises in TRW_HOT_PATH_STRICT=1 mode) when called from the
        hot path.
    """
    pinned = get_pinned_run(context=context, session_id=session_id)
    if pinned is not None:
        return pinned

    if context is not None:
        logger.info(
            "run_resolution_no_pin_scan_suppressed",
            pin_key=context.session_id,
            reason="ctx_aware_no_pin",
        )
    return None


def find_run_via_mtime_scan() -> Path | None:
    """Scan the filesystem for the most recent active run via mtime.

    PRD-FIX-085 FR01: explicit legacy entry point for one-shot CLI tools
    that have no session context (and therefore no pin). Hot-path
    callers MUST NOT use this -- it PyYAML-parses every ``run.yaml``
    under ``.trw/runs/`` (~25 s on ~200 runs).

    PRD-FIX-085 FR02: when called while the :data:`HOT_PATH` ContextVar
    is True (set by ``trw_session_start`` and middleware), this function
    emits a ``hot_path_legacy_scan_attempted`` WARN with the calling
    stack. When ``TRW_HOT_PATH_STRICT=1`` is set, it raises
    ``HotPathLegacyScanError`` instead -- catches the regression class
    AT THE API BOUNDARY in dev/test.

    Returns:
        Path to the latest active run directory by lexicographic name,
        or ``None`` if no active run exists.
    """
    if HOT_PATH.get():
        # PRD-FIX-085 FR02: caller is on the session_start / middleware
        # hot path. The legacy scan is forbidden here; surface the offender.
        try:
            import inspect

            frame = inspect.stack()[1]
            caller_module = frame.frame.f_globals.get("__name__", "<unknown>")
            caller_function = frame.function
            caller_lineno = frame.lineno
        except Exception:  # justified: fail-open, diagnostic only
            caller_module = "<unknown>"
            caller_function = "<unknown>"
            caller_lineno = 0

        logger.warning(
            "hot_path_legacy_scan_attempted",
            caller_module=caller_module,
            caller_function=caller_function,
            caller_lineno=caller_lineno,
        )

        if os.environ.get("TRW_HOT_PATH_STRICT") == "1":
            raise HotPathLegacyScanError(
                f"find_run_via_mtime_scan() called from hot path "
                f"({caller_module}:{caller_function}:{caller_lineno}); "
                f"use get_pinned_run() instead. "
                f"Set TRW_HOT_PATH_STRICT=0 to demote to a WARN."
            )

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
            # Status-aware: skip non-active runs (PRD-FIX-042 FR02)
            try:
                data = reader.read_yaml(run_yaml)
                status = str(data.get("status", "active"))
                if status in ("complete", "failed", "abandoned", "delivered"):
                    continue
            except Exception:  # justified: fail-open, unreadable run.yaml treated as active for backward compat
                logger.debug("run_yaml_read_failed", path=str(run_yaml), exc_info=True)
            if run_dir.name > latest_name:
                latest_name = run_dir.name
                latest_dir = run_dir

        return latest_dir
    except (StateError, OSError):
        return None


def resolve_run_path(
    run_path: str | None = None,
    *,
    context: TRWCallContext | None = None,
) -> Path:
    """Resolve a run directory from an explicit path or auto-detection.

    HOT-PATH RULE (PRD-FIX-083): Callers reachable from ``trw_session_start``
    or any ceremony middleware MUST pass ``context=`` or use
    :func:`get_pinned_run` directly. The legacy mtime-scan fallback (step 3
    below) PyYAML-parses every ``run.yaml`` under ``.trw/runs/`` (~25 s on
    ~200 runs).

    Unified implementation (PRD-FIX-007) replacing the duplicated private
    ``_resolve_run_path`` helpers in orchestration.py and findings.py.

    Resolution order (v0.44.5+, PRD-FIX-077):
    1. If *run_path* is provided, resolve to absolute and verify existence.
    2. Otherwise, call ``find_active_run()`` — which checks the per-session
       pin first, then scans for status=active runs. This matches the path
       ``trw_session_start`` / ``trw_init`` use when pinning the active run,
       so ``trw_status``, ``trw_checkpoint``, and reporting tools all
       converge on the SAME run the session anchored.
    3. Fall back to ``_find_latest_run_dir()`` (latest ``st_mtime``) only
       when no pinned / active run exists — preserves discovery for clients
       that never call ``trw_session_start`` (legacy + one-shot tools).

    Prior behavior (pre-0.44.5) went straight to mtime-scan, which
    disagreed with ``trw_session_start`` whenever an abandoned run had a
    more recent ``run.yaml`` mtime than the actual active run — e.g. when
    another session wrote summary.yaml to a stale run. Users reported
    ``trw_status`` returning a different run than the one ``trw_session_start``
    had just pinned.

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

    # Primary: prefer pinned/active run so trw_status aligns with trw_session_start.
    # Thread ctx through so FR05 suppression applies consistently — ctx-aware
    # callers with no pin see ``active is None`` here and raise below.
    active = find_active_run(context=context)
    if active is not None:
        return active

    # FR05: Ctx-aware callers skip the mtime-fallback.  Raise a targeted
    # StateError so the caller sees "no run for this session" rather than
    # hijacking another session's directory.  The ``run_resolution_no_pin_scan_suppressed``
    # INFO event was already emitted inside ``find_active_run`` — no need
    # to log again here.
    if context is not None:
        raise StateError(
            "No active run for this session (pin not found, scan fallback suppressed). "
            "Call trw_init() to create a run or trw_adopt_run(run_path=...) to resume one.",
            suggestion="Call trw_init() or trw_adopt_run(run_path=...) before checkpoint/deliver.",
            project_root=str(project_root),
            pin_key=context.session_id,
        )

    # PRD-FIX-085 FR02: HOT_PATH guard. If we reach this fallback during
    # the session_start / middleware hot-path, that's a regression --
    # callers in that scope must pass context=. The find_run_via_mtime_scan
    # helper is the explicit-opt-in legacy path; mark this mtime fallback
    # the same way for consistency.
    if HOT_PATH.get():
        try:
            import inspect

            frame = inspect.stack()[1]
            caller_module = frame.frame.f_globals.get("__name__", "<unknown>")
            caller_function = frame.function
            caller_lineno = frame.lineno
        except Exception:  # justified: fail-open, diagnostic only
            caller_module = "<unknown>"
            caller_function = "<unknown>"
            caller_lineno = 0
        logger.warning(
            "hot_path_legacy_scan_attempted",
            caller_module=caller_module,
            caller_function=caller_function,
            caller_lineno=caller_lineno,
            via="resolve_run_path_mtime_fallback",
        )
        if os.environ.get("TRW_HOT_PATH_STRICT") == "1":
            raise HotPathLegacyScanError(
                f"resolve_run_path() reached the mtime-fallback from hot path "
                f"({caller_module}:{caller_function}:{caller_lineno}); pass context= or use get_pinned_run()."
            )

    # Fallback: latest mtime for clients that never pinned a run.
    latest_run = _find_latest_run_dir(runs_dir)
    if latest_run is None:
        raise StateError(
            f"No active runs found in {config.runs_root}/",
            project_root=str(project_root),
        )
    logger.info(
        "resolve_run_path_mtime_fallback",
        selected=str(latest_run),
        reason="no_pinned_or_active_run",
    )

    return latest_run


def resolve_installation_id() -> str:
    """Resolve installation ID from config, generating a stable fallback.

    Resolution order:
    1. ``TRWConfig.installation_id`` (if non-empty after stripping whitespace).
    2. A deterministic ``inst-<hash>`` derived from the project root path.

    This is the **single source of truth** for installation ID resolution.
    All modules that need an installation ID MUST call this function instead
    of reimplementing the lookup.
    """
    import hashlib

    cfg = get_config()
    iid = cfg.installation_id.strip() if cfg.installation_id else ""
    if iid:
        return iid
    project_root = str(resolve_project_root())
    return "inst-" + hashlib.sha256(project_root.encode()).hexdigest()[:12]


def detect_current_phase(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> str | None:
    """Detect the current phase from the active run.

    PRD-FIX-083 / PRD-FIX-084 follow-on: pin-only. Was using
    :func:`find_active_run` with no context, which fell through to the
    legacy mtime scan and PyYAML-parsed every ``run.yaml`` (~25 s on
    ~200 runs). This function fires from the recall-context build path
    inside ``step_ceremony_status`` on the session_start hot path -- it
    was the actual cause of the ~27 s ``finalize`` outliers surfaced
    by PRD-FIX-084 telemetry.

    Returns:
        Current phase string (e.g. ``"implement"``), or ``None`` if no
        pinned run for this session.
    """
    try:
        active_run = get_pinned_run(context=context, session_id=session_id)
        if active_run is None:
            return None

        reader = FileStateReader()
        run_yaml = active_run / "meta" / "run.yaml"
        if not run_yaml.exists():
            return None

        data = reader.read_yaml(run_yaml)
        if str(data.get("status", "")) != "active":
            return None
        return str(data.get("phase", "")) or None
    except (OSError, ValueError, TypeError):
        return None


def touch_heartbeat(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> None:
    """Touch the heartbeat file in the active run directory.

    Called on every MCP tool invocation to signal session liveness.
    Uses ``get_pinned_run()`` for fast-path resolution, falling back to
    ``find_active_run()`` only when no pin exists.

    The heartbeat file is ``{run_dir}/meta/heartbeat`` -- only the mtime
    matters (no content is written).  Completely fail-open: any exception
    is logged as a warning and silently swallowed so tool execution is
    never blocked.

    PRD-QUAL-050-FR01.
    """
    try:
        # Pin-only: no scan fallback. touch_heartbeat fires on EVERY tool
        # call via the ceremony middleware; the legacy find_active_run()
        # fallback PyYAML-parses every .trw/runs/*/meta/run.yaml on miss
        # (~3-5s per call with ~200 runs). With no pin there is no
        # session-owned run to heartbeat anyway -- updating some other
        # session's run was the wrong semantics.
        run_dir = get_pinned_run(context=context, session_id=session_id)
        if run_dir is None:
            return

        heartbeat_path = run_dir / "meta" / "heartbeat"
        heartbeat_path.touch(exist_ok=True)
    except Exception:  # justified: fail-open -- heartbeat must never block tool execution
        logger.warning("heartbeat_touch_failed", exc_info=True)
