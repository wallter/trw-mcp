"""Phase-exposure override tool — PRD-INTENT-002 FR06.

``trw_request_tool_access(tool_name, reason, ttl_seconds=300)`` grants the
requesting session SINGLE-USE, session-scoped, TTL-capped access to one masked
tool. The override ledger is in-memory (process-local) — grants expire
naturally, so rollback requires no state cleanup (PRD §9 Rollback Plan).

Invariants:
  - NFR02: TTL is clamped to ``tool_access_grant_max_ttl_seconds`` (default
    300s) regardless of the requested value.
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
from functools import lru_cache

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)

#: Fallback ceiling on override TTL, used only when config cannot be read.
#: The live ceiling is ``tool_access_grant_max_ttl_seconds`` — see
#: :func:`_max_ttl_seconds`. This was a bare module constant until 2026-09-10,
#: which made the one knob an operator might need to turn (a client that cannot
#: refresh its tool list in-session needs a longer-lived grant) unreachable
#: without a code edit.
_MAX_TTL_FALLBACK_SECONDS = 300

#: NFR03: minimum reason length.
_MIN_REASON_CHARS = 20


#: Attribute stamped on the per-request ``MiddlewareContext`` recording which
#: ``(session_id, tool_name)`` pairs this request already burned a grant for.
#:
#: Keyed on the request OBJECT, not a ContextVar. A ContextVar scopes to the
#: asyncio task, which is *usually* one per request but is not guaranteed to
#: be — two calls served from one task would share the record and a grant would
#: leak into a second call. FastMCP hands the identical ``MiddlewareContext``
#: down the chain for exactly one call, so it is the honest request identity.
_REQUEST_AUTHORIZED_ATTR = "_trw_override_authorized"


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


def _max_ttl_seconds() -> int:
    """The configured grant-TTL ceiling, falling back if config is unreadable."""
    try:
        from trw_mcp.models.config import get_config

        return int(get_config().tool_access_grant_max_ttl_seconds)
    except Exception:  # justified: a config read must never block an escape hatch
        logger.warning("override_ttl_config_unreadable", exc_info=True)
        return _MAX_TTL_FALLBACK_SECONDS


def grant_override(
    session_id: str,
    tool_name: str,
    *,
    reason: str,
    ttl_seconds: int | None = None,
) -> OverrideGrant:
    """Record a single-use override for ``(session_id, tool_name)``.

    ``ttl_seconds`` is clamped to ``tool_access_grant_max_ttl_seconds`` (NFR02);
    ``None`` requests the ceiling. The caller is responsible for reason
    validation (see :func:`request_tool_access`); the ``reason`` is logged for
    the audit trail but never stored in the grant or echoed into tool results
    (NFR03).
    """
    ceiling = _max_ttl_seconds()
    requested = ceiling if ttl_seconds is None else int(ttl_seconds)
    effective_ttl = min(max(requested, 1), ceiling)
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


def consume_override(session_id: str, tool_name: str, request: object | None = None) -> bool:
    """Consume an active override. Single-use per REQUEST, not per gate.

    A grant is documented as permitting "one masked call", and one call
    traverses BOTH masking middlewares: ``surface_authority`` runs first, then
    ``phase_exposure``. Both funnel through this function. When a tool is
    masked at both layers, the first gate popped the grant and the second then
    found nothing and denied the call — so a grant reported as ``granted: true``
    was consumed and the call still failed with ``tool_not_in_phase``, having
    executed no handler. Reproduced by an independent review, and it is exactly
    the reports-success-without-doing-its-job shape this tool has now been
    fixed for twice.

    The authorization is therefore stamped on *request* — the per-call
    ``MiddlewareContext`` FastMCP passes down the chain. A second gate handed
    the SAME request sees the stamp and passes without re-popping; a later
    request carries no stamp and finds the grant genuinely gone. That makes
    "single-use" mean one CALL, which is what the tool's own documentation
    promises.

    Callers that pass no *request* fall back to per-gate behaviour, which is
    the old semantics and is correct for a single-gate caller.
    """
    key = (session_id, tool_name)
    authorized: set[tuple[str, str]] | None = None
    if request is not None:
        authorized = getattr(request, _REQUEST_AUTHORIZED_ATTR, None)
        if authorized is not None and key in authorized:
            # An earlier gate in THIS request already burned the grant.
            return True
    if not has_active_override(session_id, tool_name):
        return False
    grant = _overrides.pop(key, None)
    if grant is None:
        return False
    if request is not None:
        if authorized is None:
            authorized = set()
            try:
                object.__setattr__(request, _REQUEST_AUTHORIZED_ATTR, authorized)
            except Exception:  # justified: a request object that refuses the stamp degrades to per-gate
                logger.warning("phase_override_request_stamp_failed", tool=tool_name)
        authorized.add(key)
    logger.info(
        "phase_override_consumed",
        component="phase_exposure",
        op="consume_override",
        session_id=session_id,
        tool=tool_name,
        override_id=grant.override_id,
    )
    return True


@lru_cache(maxsize=1)
def _registered_tool_names() -> frozenset[str]:
    """The authoritative registered surface, computed once per process.

    ``raw_registered_tool_names`` builds a throwaway FastMCP app to enumerate
    the registrars, so it is far too heavy to run per grant — but the registered
    surface cannot change within a process, so one cached call is enough. An
    empty result means the lookup failed and the caller falls back.
    """
    try:
        from trw_mcp.server._tools import raw_registered_tool_names

        return raw_registered_tool_names()
    except Exception:  # justified: caller falls back to the phase-policy subset
        logger.warning("override_registry_lookup_failed", exc_info=True)
        return frozenset()


def _is_registered_tool(tool_name: str) -> bool:
    """Best-effort check that ``tool_name`` is a real registered MCP tool.

    The phase policy is a SUBSET of the registry, so it cannot answer this
    question on its own. Checking only the policy made the refusal message a
    false statement: ``trw_channel_stats``, ``trw_meta_tune_propose`` and
    ``trw_replay_outcomes`` are all genuinely registered tools absent from the
    policy, and a grant request for any of them was refused with "tool '...' is
    not a registered MCP tool". The registry is consulted first and the policy
    kept as the fallback for when it cannot be reached.
    """
    names = _registered_tool_names()
    if names:
        return tool_name in names
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
    ttl_seconds: int | None = None,
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
    async def trw_request_tool_access(
        tool_name: str,
        reason: str,
        ttl_seconds: int | None = None,
    ) -> dict[str, object]:
        """Grant single-use, TTL-capped access to one phase-masked tool.

        Use when: a genuine cross-phase or emergency need requires a tool
        the phase masks — every grant is logged.

        Output: {"granted": bool}; on denial also "error". "client_notified"
        reports notification emission, not acknowledgement of a client refresh.
        "action_required" means refresh tools/list in the same session before the grant expires;
        restarting the server loses the grant.

        Args:
            tool_name: masked tool; must be a registered MCP tool.
            reason: audit justification, >= 20 characters.
            ttl_seconds: requested TTL; clamped to the configured ceiling.
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
        result = request_tool_access(session_id, tool_name, reason=reason, ttl_seconds=ttl_seconds)
        if not result.get("granted"):
            return result

        # A grant updates SERVER state only. `on_list_tools` already unions
        # `_active_override_tools`, so the tool WOULD be advertised on the next
        # `tools/list` — but nothing told the client to re-list, so a capable
        # client kept its cached view and the single-use, TTL-bounded grant expired
        # unused. `granted: true` while the tool stayed uncallable is the exact
        # reports-success-without-doing-its-job shape this codebase keeps
        # finding. Emit the same refresh signal the phase-transition path uses.
        notified = False
        try:
            from fastmcp.server.dependencies import get_context

            from trw_mcp.middleware._phase_transitions import emit_list_changed

            # MCP tools.listChanged is a SERVER capability, not a client
            # subscription. Standard clients need no experimental opt-in.
            notified = await emit_list_changed(get_context())
        except Exception:  # justified: fail-open — the grant itself stands
            logger.warning("override_list_changed_failed", exc_info=True)

        result["client_notified"] = notified
        if not notified:
            # Tell the AGENT what the AGENT can do. The previous text sent the
            # caller to a human keystroke ("ask the operator", "reconnect"),
            # which is unusable advice for the one situation this tool exists to
            # serve: an autonomous session that needs a masked tool. The clamped
            # TTL then expired unused (sub_hJ96RkVjxsLXwqWA, 2026-09-07).
            #
            # The grant is sound server-side and does NOT depend on the listing.
            # FastMCP resolves tools/call by NAME out of the registry, with no
            # cross-check against whatever tools/list last returned; TRW's
            # filtering happens only in on_list_tools. Both masking layers honour
            # an active override on a call for a tool they are hiding
            # (middleware/phase_exposure.py and middleware/surface_authority.py).
            # So the tool IS callable right now on any client willing to emit the
            # call — which is most of them — and the honest instruction is to try.
            result["action_required"] = (
                "granted. This client was not notified to refresh its tool list, so the tool may "
                "not appear in your listing — call it anyway: the server resolves the call by name "
                "and will consume this override. Only if your client refuses to emit a call for an "
                "unlisted tool do you need a tools/list refresh in this same session. The grant is "
                "single-use, expires with the TTL, and is lost if the server restarts."
            )
        logger.info(
            "phase_override_granted",
            component="phase_overrides",
            op="request_tool_access",
            tool=tool_name,
            client_notified=notified,
            outcome="granted",
        )
        return result


__all__ = [
    "OverrideGrant",
    "consume_override",
    "grant_override",
    "has_active_override",
    "register_phase_override_tools",
    "request_tool_access",
    "reset_overrides",
]
