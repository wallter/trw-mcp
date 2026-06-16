"""Session + run-dir resolution helpers for phase-exposure middleware.

Belongs to the ``middleware/phase_exposure.py`` facade — extracted so the
middleware module stays under the 350 effective-LOC gate. Wraps the existing
session-id / pinned-run resolution so PhaseExposureMiddleware reads the SAME
phase source (``run.yaml``) the CeremonyMiddleware uses.

All helpers are fail-open: any resolution error returns a safe default (empty
session id / ``None`` run dir) and never raises into the middleware hot path.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def safe_session_id_from_context(fastmcp_context: object | None) -> str:
    """Return ``ctx.session_id`` or ``""`` when no request context exists.

    Mirrors ``mcp_security._safe_session_id``: recent FastMCP raises
    ``RuntimeError`` (not ``AttributeError``) from the ``session_id`` descriptor
    when accessed outside a request context.
    """
    if fastmcp_context is None:
        return ""
    try:
        value = fastmcp_context.session_id  # type: ignore[attr-defined]
    except (AttributeError, RuntimeError):
        return ""
    return value if isinstance(value, str) else ""


def resolve_run_dir_for_session(
    *,
    session_id: str = "",
    fastmcp_context: object | None = None,
) -> Path | None:
    """Resolve the active run directory for the session (pin-only, fail-open).

    Uses the same ``resolve_run_context`` pin resolution the security
    middleware uses, so the phase source is consistent across the chain.
    Returns ``None`` when no pinned run exists or resolution fails.
    """
    try:
        from trw_mcp.middleware._mcp_security_helpers import resolve_run_context

        run_dir, _ = resolve_run_context(
            configured_run_dir=None,
            session_id=session_id,
            fastmcp_context=fastmcp_context,
        )
        return run_dir
    except Exception:  # justified: fail-open — phase resolution must never raise
        logger.warning("phase_run_dir_resolution_failed", exc_info=True)
        return None


__all__ = [
    "resolve_run_dir_for_session",
    "safe_session_id_from_context",
]
