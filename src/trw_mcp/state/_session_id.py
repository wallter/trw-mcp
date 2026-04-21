"""Effective session_id resolver for surface-event emission.

PRD-CORE-144 FR01: every ``log_surface_event(...)`` caller must populate
``session_id``. This helper centralizes the resolution precedence so all
call sites behave consistently.

Precedence
----------
1. Active run's ``run_id`` (the final path component of ``find_active_run()``
   when a pin resolves).
2. ``TRW_SESSION_ID`` env var (operator-forced identity / PRD-CORE-141).
3. Process-level UUIDv4 — cached at module level and reused for the
   lifetime of the MCP process so IPS counting groups a process's events
   together.

Never raises. On any failure in 1/2, falls back to the process UUID.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Process-level stable UUID — generated on first call, reused thereafter.
# This is deliberately *separate* from ``_paths.get_session_id`` so the
# surface-tracking fallback is not entangled with the pin resolver's UUID
# (which tests reset aggressively).
_process_id_lock = threading.Lock()
_process_session_id: str | None = None


def _get_process_session_id() -> str:
    """Return a stable-per-process UUIDv4, generating it on first call."""

    global _process_session_id
    with _process_id_lock:
        if _process_session_id is None:
            _process_session_id = uuid.uuid4().hex
        return _process_session_id


def _reset_process_session_id(new_id: str | None = None) -> None:
    """Test hook: reset the cached process id. Never call from production."""

    global _process_session_id
    with _process_id_lock:
        _process_session_id = new_id


def resolve_effective_session_id(trw_dir: Path | None = None) -> str:
    """Resolve the effective session id for surface-event tagging.

    Fail-open: any exception inside the active-run lookup is swallowed and
    the function falls through to the env/process fallback. The returned
    string is always non-empty.
    """

    # Layer 1 — active run's run_id. Uses find_active_run() which reads
    # the persistent pin store. No pin -> None -> fall through.
    try:
        from trw_mcp.state._paths import find_active_run, resolve_trw_dir

        _ = trw_dir or resolve_trw_dir()
        active = find_active_run()
        if active is not None:
            run_id = active.name
            if run_id:
                return run_id
    except Exception:  # justified: fail-open, resolution must never crash callers
        logger.debug("session_id_active_run_lookup_failed", exc_info=True)

    # Layer 2 — operator-forced identity via env var (PRD-CORE-141).
    env_id = os.environ.get("TRW_SESSION_ID")
    if env_id:
        return env_id

    # Layer 3 — stable-per-process UUID fallback.
    return _get_process_session_id()
