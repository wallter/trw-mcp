"""Shared path resolution -- single source of truth for project root, .trw dir, and run paths.

All modules that need to resolve TRW_PROJECT_ROOT, the .trw directory,
or an active run path MUST use these functions instead of inline logic.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.state._pin_store import (
    get_pin_entry,
    remove_pin_entry,
    upsert_pin_entry,
)
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _runtime_logger() -> Any:
    """Return a fresh logger so structlog test capture sees late-bound events."""
    return structlog.get_logger(__name__)


def _get_config() -> Any:
    """Lazy-load TRWConfig to avoid import cycles during path-only imports."""
    from trw_mcp.models.config import get_config

    return get_config()


# Backward-compatible patch point for tests and integrations that monkeypatch
# trw_mcp.state._paths.get_config directly. Runtime code should still call
# _get_config() so config import remains lazy at module import time.
get_config = _get_config


# Explicit public surface — keeps static analyzers (Pyright) from inferring
# imported symbols as ``object`` via the module-level ``__getattr__`` shim
# below.  Must list every name callers ``from trw_mcp.state._paths import …``.
__all__ = [
    "TRWCallContext",
    "detect_current_phase",
    "find_active_run",
    "get_pinned_run",
    "get_session_id",
    "iter_run_dirs",
    "pin_active_run",
    "resolve_installation_id",
    "resolve_memory_store_path",
    "resolve_pin_key",
    "resolve_project_root",
    "resolve_run_path",
    "resolve_trw_dir",
    "touch_heartbeat",
    "unpin_active_run",
]


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


# Ctx attribute paths probed by _extract_fastmcp_session_id, in precedence
# order.  Each path is a tuple of attribute names walked via getattr.
_FASTMCP_CTX_PROBES: tuple[tuple[str, ...], ...] = (
    ("session_id",),
    ("request_context", "meta", "session_id"),
    ("request_id",),
)


class _ProbeOutcome:
    """Internal sentinel for attribute-walk results.

    ``VALUE`` — probe yielded a value (may be None if the attr existed but was None).
    ``MISSING`` — an AttributeError/TypeError was raised along the walk (broken probe).
    """

    __slots__ = ("broken", "value")

    def __init__(self, value: object | None, broken: bool) -> None:
        self.value = value
        self.broken = broken


def _walk_ctx_attrs(ctx: object, path: tuple[str, ...]) -> _ProbeOutcome:
    """Walk *path* on *ctx* via getattr.

    Returns a :class:`_ProbeOutcome` where ``broken=True`` signals that an
    :class:`AttributeError` / :class:`TypeError` was swallowed during the
    walk (i.e. the ctx object's shape does not match this probe path).
    """
    current: object = ctx
    try:
        for name in path:
            current = getattr(current, name)
    except (AttributeError, RuntimeError, TypeError):
        return _ProbeOutcome(value=None, broken=True)
    return _ProbeOutcome(value=current, broken=False)


def _extract_fastmcp_session_id(ctx: object) -> str | None:
    """Probe FastMCP Context *ctx* for a session identifier string.

    Probes (in order): ``ctx.session_id``, ``ctx.request_context.meta.session_id``,
    ``ctx.request_id``.  Each probe is logged at DEBUG with the attribute path
    attempted.  The first non-None string value wins.

    When EVERY probe is broken (every walk raised AttributeError/TypeError),
    emit a single ``fastmcp_context_probe_error`` WARN with the broken paths
    so analytics can detect FastMCP API drift on shared servers.

    Returns the resolved session id string, or ``None`` when no probe
    yielded a string.
    """
    broken_paths: list[str] = []
    for path in _FASTMCP_CTX_PROBES:
        path_str = ".".join(path)
        outcome = _walk_ctx_attrs(ctx, path)
        if outcome.broken:
            broken_paths.append(path_str)
            _runtime_logger().info(
                "fastmcp_context_probe_skipped",
                ctx_attr_path=path_str,
            )
            continue
        value = outcome.value
        if isinstance(value, str) and value:
            _runtime_logger().info(
                "fastmcp_context_probe_hit",
                ctx_attr_path=path_str,
                has_value=True,
            )
            return value
        _runtime_logger().info(
            "fastmcp_context_probe_miss",
            ctx_attr_path=path_str,
            has_value=False,
        )

    if broken_paths and len(broken_paths) == len(_FASTMCP_CTX_PROBES):
        # Every probe raised — ctx object shape is incompatible.  Warn so
        # analytics can spot FastMCP API drift on shared servers.
        _runtime_logger().warning(
            "fastmcp_context_probe_error",
            broken_paths=broken_paths,
            ctx_type=type(ctx).__name__,
        )
    return None


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
# eviction passes emit ``pin_stale_run_path_evicted`` and
# ``pin_orphan_evicted`` WARN logs; malformed JSON fails open with
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


def pin_active_run(
    run_dir: Path,
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> None:
    """Pin a run directory as the active run for a session.

    After pinning, find_active_run() returns this directory instead of
    scanning the filesystem. This prevents telemetry hijack when multiple
    instances share the same project root.

    Writes through to ``.trw/runtime/pins.json`` via :func:`upsert_pin_entry`
    so the pin survives MCP server restart (PRD-CORE-141 FR04).

    Args:
        run_dir: Absolute path to the run directory to pin.
        context: TRWCallContext resolved from the FastMCP Context (preferred,
            PRD-CORE-141 FR01).  When provided, its ``session_id`` wins.
        session_id: Legacy kwarg — retained for backward compat with direct
            Python callers.  Ignored when ``context`` is provided.
    """
    sid = _resolve_session_id(context, session_id)
    record = upsert_pin_entry(sid, run_dir)
    logger.debug(
        "pin_saved",
        pin_key=sid,
        run_path=record["run_path"],
        pid=record["pid"],
    )


def unpin_active_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> None:
    """Remove the run pin for a session, reverting to filesystem scan.

    Persists the removal to ``.trw/runtime/pins.json`` (PRD-CORE-141 FR04).

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred).
        session_id: Legacy kwarg; ignored when ``context`` is provided.
    """
    sid = _resolve_session_id(context, session_id)
    removed = remove_pin_entry(sid)
    if removed:
        logger.debug("pin_cleared", pin_key=sid)


def get_pinned_run(
    *,
    context: TRWCallContext | None = None,
    session_id: str | None = None,
) -> Path | None:
    """Return the currently pinned run directory for a session, or None.

    Reads through the 1-second-TTL pin-store cache (PRD-CORE-141 FR04).

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred).
        session_id: Legacy kwarg; ignored when ``context`` is provided.
    """
    sid = _resolve_session_id(context, session_id)
    entry = get_pin_entry(sid)
    if entry is None:
        return None
    run_path = entry.get("run_path")
    if isinstance(run_path, str) and run_path:
        return Path(run_path)
    return None


def resolve_memory_store_path() -> Path:
    """Resolve the sqlite-vec memory store database path.

    Strips the ``.trw/`` prefix from the configured ``memory_store_path``
    and joins with the resolved .trw directory.

    Returns:
        Absolute path to the sqlite-vec database file.
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
    """Find the active run directory for a session.

    Resolution order:
    1. Per-session pinned run (set by ``pin_active_run`` during ``trw_init``)
    2. Filesystem scan fallback — ONLY when ``context is None`` (legacy /
       stdio-per-instance callers).  PRD-CORE-141 FR05 forbids the scan
       fallback for ctx-aware callers to eliminate cross-session run
       hijack.  The scan selects ``{runs_root}/{task}/{run_id}/meta/run.yaml``
       with the highest lexicographic name, skipping runs with status
       ``"complete"`` / ``"failed"`` / ``"abandoned"`` / ``"delivered"``
       (PRD-FIX-042 FR02).

    The pinned run prevents telemetry hijack when multiple Claude Code
    instances share the same filesystem — each instance's MCP process
    pins its own run at init time.

    Args:
        context: TRWCallContext resolved from FastMCP Context (preferred,
            PRD-CORE-141 FR01).  When provided and no pin exists, returns
            ``None`` immediately — scan fallback is SUPPRESSED.
        session_id: Legacy kwarg; passed through to the pin lookup only.
            Callers passing just ``session_id=`` (and no ``context=``)
            retain the legacy scan fallback (PRD-CORE-141 FR15).

    Returns:
        Path to run directory, or None if no active run found.
    """
    pinned = get_pinned_run(context=context, session_id=session_id)
    if pinned is not None:
        return pinned

    # FR05: Ctx-aware callers do NOT fall through to the legacy mtime scan.
    # The fresh-session-hijack bug (PRD-CORE-141 §Background) requires this
    # to be an early exit — a ctx with no pin means "no run for this
    # session", not "grab whatever's latest on disk".
    if context is not None:
        logger.info(
            "run_resolution_no_pin_scan_suppressed",
            pin_key=context.session_id,
            reason="ctx_aware_no_pin",
        )
        return None

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
            "No active run for this session (pin not found, scan fallback suppressed).",
            project_root=str(project_root),
            pin_key=context.session_id,
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


def detect_current_phase() -> str | None:
    """Detect the current phase from the active run.

    Delegates to :func:`find_active_run` for run-directory resolution, then
    reads the phase from ``run.yaml``.  Only returns a phase when the run's
    ``status`` is ``"active"``.

    Returns:
        Current phase string (e.g. ``"implement"``), or ``None`` if no active run.
    """
    try:
        active_run = find_active_run()
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


def touch_heartbeat() -> None:
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
        run_dir = get_pinned_run()
        if run_dir is None:
            run_dir = find_active_run()
        if run_dir is None:
            return

        heartbeat_path = run_dir / "meta" / "heartbeat"
        heartbeat_path.touch(exist_ok=True)
    except Exception:  # justified: fail-open -- heartbeat must never block tool execution
        logger.warning("heartbeat_touch_failed", exc_info=True)
