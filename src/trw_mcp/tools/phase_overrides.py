"""Phase-exposure override tool — PRD-INTENT-002 FR06.

``trw_request_tool_access(tool_name, reason, ttl_seconds=300)`` grants the
requesting session SINGLE-USE, session-scoped, TTL-capped access to one masked
tool. The override ledger is in-memory (process-local) — grants expire
naturally, so rollback requires no state cleanup (PRD §9 Rollback Plan).

Invariants:
  - NFR02: TTL is clamped to a 5-minute cap regardless of the requested value.
  - NFR03: ``reason`` must be non-empty and >= 20 chars (audit-trail quality).
  - The grant is consumed on first masked call (single-use); after that the
    standard mask re-applies.

The middleware (``middleware/phase_exposure.py``) is the consumer: it calls
:func:`has_active_override` / :func:`consume_override` during ``on_call_tool``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)

#: NFR02: hard 5-minute cap on override TTL.
_MAX_TTL_SECONDS = 300

#: NFR03: minimum reason length.
_MIN_REASON_CHARS = 20


def _now() -> float:
    """Monotonic-ish wall clock (patched in tests)."""
    return time.time()


@dataclass(frozen=True)
class OverrideGrant:
    """A single-use grant for one (session, tool) pair."""

    override_id: str
    session_id: str
    tool_name: str
    granted_at: float
    expires_at: float
    ttl_seconds: int


# Ledger: (session_id, tool_name) -> active grant.
_overrides: dict[tuple[str, str], OverrideGrant] = {}


def reset_overrides() -> None:
    """Clear the override ledger — for testing only."""
    _overrides.clear()


def grant_override(
    session_id: str,
    tool_name: str,
    *,
    reason: str,
    ttl_seconds: int = _MAX_TTL_SECONDS,
) -> OverrideGrant:
    """Record a single-use override for ``(session_id, tool_name)``.

    ``ttl_seconds`` is clamped to :data:`_MAX_TTL_SECONDS` (NFR02). The caller
    is responsible for reason validation (see :func:`request_tool_access`); the
    ``reason`` is logged for the audit trail but never stored in the grant or
    echoed into tool results (NFR03).
    """
    effective_ttl = min(max(int(ttl_seconds), 1), _MAX_TTL_SECONDS)
    now = _now()
    grant = OverrideGrant(
        override_id=uuid.uuid4().hex,
        session_id=session_id,
        tool_name=tool_name,
        granted_at=now,
        expires_at=now + effective_ttl,
        ttl_seconds=effective_ttl,
    )
    _overrides[(session_id, tool_name)] = grant
    logger.info(
        "phase_override_granted",
        component="phase_exposure",
        op="grant_override",
        session_id=session_id,
        tool=tool_name,
        ttl_seconds=effective_ttl,
        reason_len=len(reason),
    )
    return grant


def has_active_override(session_id: str, tool_name: str) -> bool:
    """Return True if a non-expired override exists for the pair."""
    grant = _overrides.get((session_id, tool_name))
    if grant is None:
        return False
    if _now() >= grant.expires_at:
        _overrides.pop((session_id, tool_name), None)
        return False
    return True


def consume_override(session_id: str, tool_name: str) -> bool:
    """Consume (single-use) an active override. Returns True if one was used."""
    if not has_active_override(session_id, tool_name):
        return False
    grant = _overrides.pop((session_id, tool_name), None)
    if grant is None:
        return False
    logger.info(
        "phase_override_consumed",
        component="phase_exposure",
        op="consume_override",
        session_id=session_id,
        tool=tool_name,
        override_id=grant.override_id,
    )
    return True


def _is_registered_tool(tool_name: str) -> bool:
    """Best-effort check that ``tool_name`` is a real registered MCP tool."""
    try:
        from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY

        known = set(DEFAULT_PHASE_POLICY.safe_set)
        for tools in DEFAULT_PHASE_POLICY.allowed_tools_by_phase.values():
            known.update(tools)
        return tool_name in known
    except Exception:  # justified: fail-open validation — never block on a lookup error
        logger.warning("override_tool_validation_failed", tool=tool_name, exc_info=True)
        return True


def request_tool_access(
    session_id: str,
    tool_name: str,
    *,
    reason: str,
    ttl_seconds: int = _MAX_TTL_SECONDS,
) -> dict[str, object]:
    """Validate + grant a single-use override (the tool body, sans server ctx).

    Returns a structured dict: ``{granted, override_id?, expires_at?, error?}``.
    Rejections (short reason, unknown tool) return ``granted=False`` + ``error``.
    """
    if not reason or len(reason.strip()) < _MIN_REASON_CHARS:
        return {
            "granted": False,
            "error": f"reason must be non-empty and at least {_MIN_REASON_CHARS} characters",
        }
    if not _is_registered_tool(tool_name):
        return {
            "granted": False,
            "error": f"tool {tool_name!r} is not a registered MCP tool",
        }
    grant = grant_override(session_id, tool_name, reason=reason, ttl_seconds=ttl_seconds)
    return {
        "granted": True,
        "override_id": grant.override_id,
        "expires_at": grant.expires_at,
        "ttl_seconds": grant.ttl_seconds,
    }


def register_phase_override_tools(server: FastMCP) -> None:
    """Register the ``trw_request_tool_access`` override tool (FR06)."""

    @server.tool(output_schema=None)
    def trw_request_tool_access(
        tool_name: str,
        reason: str,
        ttl_seconds: int = _MAX_TTL_SECONDS,
    ) -> dict[str, object]:
        """Grant this session single-use access to a phase-masked tool.

        Use when a genuine cross-phase or emergency-debug need requires a tool
        the current phase masks — and only then, since every grant is logged to
        telemetry. The grant is single-use (one subsequent call) and the TTL is
        capped at 5 minutes regardless of ``ttl_seconds``.

        Args:
            tool_name: The masked tool to temporarily expose.
            reason: Non-empty audit reason (>= 20 chars).
            ttl_seconds: Requested TTL; clamped to a 5-minute maximum.

        Returns:
            {"granted": bool, "override_id"?: str, "expires_at"?: float,
             "error"?: str}
        """
        from trw_mcp.middleware._phase_session import safe_session_id_from_context

        try:
            from fastmcp.server.dependencies import get_context

            session_id = safe_session_id_from_context(get_context())
        except Exception:  # justified: fail-closed — see below; do not grant
            logger.warning("override_session_resolution_failed", exc_info=True)
            session_id = ""
        # A grant is single-use AND session-scoped: it is keyed on the session
        # id so it only unmasks a tool for the requesting session. If the
        # session id is unavailable, a shared "unknown" sentinel bucket would
        # let one session's grant be consumed by another (cross-session grant
        # pollution). Fail CLOSED — reject the grant rather than pool it under a
        # sentinel key. (Sprint-97 adaptive-surface review F2.)
        if not session_id:
            return {
                "granted": False,
                "error": "session_id_unavailable",
            }
        return request_tool_access(session_id, tool_name, reason=reason, ttl_seconds=ttl_seconds)


__all__ = [
    "OverrideGrant",
    "consume_override",
    "grant_override",
    "has_active_override",
    "register_phase_override_tools",
    "request_tool_access",
    "reset_overrides",
]
