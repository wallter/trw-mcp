"""Server-side ceremony enforcement middleware.

PRD-INFRA-007 + PRD-CORE-098-FR06: Tracks per-session ceremony state.
When `.trw/context/pre_compact_state.json` indicates recovery is pending,
``trw_session_start`` is required before other ``trw_*`` tools may run. Outside
that post-compaction state, tools execute normally and the middleware only adds
advisory warnings for sessions that skipped ceremony.

The post-compaction gate is scoped to a *gate generation*: when the shared
signal is first observed, only the sessions already known at that instant are
blanket-gated. A session first registered afterwards holds no pre-compaction
context to recover, so gating it would be both semantically wrong and
unsatisfiable for any toolset that lacks ``trw_session_start`` (a TRW sub-agent
is allowlisted for ``trw_learn``/``trw_checkpoint``/``trw_recall``/
``trw_build_check`` only, so a blanket gate deadlocked every tool it could
call). The session that actually compacted is in the snapshot and stays blocked
until its own ``trw_session_start`` succeeds.

This is client-agnostic — works with Claude Code, Cursor, Windsurf, or
any MCP client. Middleware state is keyed by MCP session_id so parallel
connections remain isolated from one another.
"""

from __future__ import annotations

__all__ = ["COMPACTION_GATE_EXEMPT_TOOLS", "TERMINAL_TOOLS", "CeremonyMiddleware"]

import json
import os
from collections import OrderedDict

import structlog
from fastmcp.server.middleware.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from fastmcp.tools import ToolResult
from mcp.types import CallToolRequestParams, TextContent

from trw_mcp.middleware._compaction_gate_owner import (
    MarkerOwnership as MarkerOwnership,
)
from trw_mcp.middleware._compaction_gate_owner import marker_owner_exempts, marker_ownership
from trw_mcp.middleware._compaction_gate_payload import build_compaction_block

# Bound on per-session tracking maps. In stdio mode connections are short-lived
# (1 per client session) so these stay tiny — but in a long-lived shared-HTTP
# server (this monorepo's dev mode) clients reconnect with fresh session_ids
# indefinitely, so without eviction these maps leak memory for the life of the
# process. Cap them and evict the least-recently-registered session_id.
_MAX_TRACKED_SESSIONS = 2048

# Module-level session state: session_id -> True (ceremony completed).
_session_state: dict[str, bool] = {}

# Session-local recovery gate decision. A session that sees the post-compaction
# marker must complete its own successful session_start() even if another
# session later clears the shared disk marker. ``True`` = owes recovery,
# ``False`` = explicitly exempted for the current gate generation, absent =
# not yet decided.
_compaction_gate_sessions: dict[str, bool] = {}

# Rising-edge tracking for the shared compaction signal. The blanket gate is
# applied ONCE, to the sessions known at the moment the signal is first
# observed ("the gate generation"). A session first registered AFTER that edge
# cannot hold pre-compaction context, so retroactively gating it is both
# semantically wrong and — for a sub-agent toolset that lacks
# ``trw_session_start`` — unsatisfiable: every trw_* call it can make would be
# blocked for the life of the server with no way to clear the gate.
_compaction_signal_raised: bool = False

# Monotonic generation counter, bumped on each rising edge. Log-only: it makes
# gate raises and per-session exemptions correlatable in structured logs.
_compaction_gate_generation: int = 0

# Sessions observed by the middleware in this server process, in insertion
# order. When a new compaction marker appears, every currently known session
# must recover again. Ordered so we can evict the oldest under the cap; the
# value is unused (acts as an ordered set).
_known_sessions: OrderedDict[str, None] = OrderedDict()


def _register_session(session_id: str) -> None:
    """Track a session, evicting the oldest once the cap is exceeded.

    Keeps ``_known_sessions`` (the authoritative recency order) and the two
    per-session maps bounded so a long-lived shared-HTTP server does not leak
    memory as clients reconnect with new session_ids.
    """
    if session_id in _known_sessions:
        _known_sessions.move_to_end(session_id)
    else:
        _known_sessions[session_id] = None
    while len(_known_sessions) > _MAX_TRACKED_SESSIONS:
        oldest, _ = _known_sessions.popitem(last=False)
        _session_state.pop(oldest, None)
        _compaction_gate_sessions.pop(oldest, None)
        _compaction_gate_attempts.pop(oldest, None)


# Tools that clear the ceremony gate.
CEREMONY_TOOLS: frozenset[str] = frozenset({"trw_session_start"})

#: Tools the post-compaction gate MUST NOT block. Named for the question the
#: gate is asking — "may this call proceed on stale post-compaction context?" —
#: rather than for whichever members happen to answer it today. Each member
#: carries its own reason, because they are exempt for two different ones.
#:
#: ``trw_checkpoint`` / ``trw_learn`` / ``trw_build_check`` (operator-approved
#: 2026-07-26, closing the decision audit C-5 deferred): the gate exists to stop
#: an agent ACTING on stale context, and these do not act — they record what
#: already happened, from content the caller supplies, which a stale framework
#: cannot corrupt. Blocking them buys no context integrity; it destroys evidence,
#: unrecoverably. Measured: a delegated VALIDATE completed with NO recorded
#: ``trw_build_check`` and a checkpoint reporting ``recorded: false`` (three
#: delegates, one session, 2026-07-26). A gate that defends context integrity by
#: destroying evidence integrity has its priorities inverted.
#:
#: ``trw_request_tool_access`` (PRD-CORE-258-FR03): it is the documented escape
#: from a restricted surface — ``middleware/surface_authority.py`` and
#: ``middleware/phase_exposure.py`` both instruct callers to reach for it BY
#: NAME — and a remedy for restriction A must not be withheld by restriction B.
#: An agent told to call the escape hatch and then refused the escape hatch has
#: been handed a loop with no documented exit. Exempting it does not widen this
#: gate: it grants one masked call, and that granted call is itself evaluated by
#: this same ``on_call_tool`` and still blocked.
#:
#: Everything else stays gated, including ``trw_recall`` and ``trw_status`` (they
#: SHAPE subsequent decisions, which is exactly what a stale-context agent must
#: not do) and ``trw_deliver`` (a terminal act — see ``TERMINAL_TOOLS``).
COMPACTION_GATE_EXEMPT_TOOLS: frozenset[str] = frozenset(
    {"trw_checkpoint", "trw_learn", "trw_build_check", "trw_request_tool_access"}
)

#: Acts that must never ride the bounded escape below (PRD-CORE-258-FR09).
#:
#: The escape exists for a caller that CANNOT clear the gate, and that is
#: measurably not the caller who delivers: of the eleven bundled agents exactly
#: one names ``trw_session_start`` in its toolset, and it is the same one — and
#: the only one — that names ``trw_deliver``. Excluding delivery therefore
#: strands nobody, and it restores an invariant two other places in this tree
#: already document (``tools/_delivery_build_gates.py`` names this middleware as
#: layer 1 of the deliver-gate defence in depth).
#:
#: ``trw_init`` is deliberately NOT a member: it is not a terminal act, it
#: creates the run container the exempt evidence tools write into, and blocking
#: it past the bound would strand a delegate trying to record work — the failure
#: the bounded escape exists to prevent.
TERMINAL_TOOLS: frozenset[str] = frozenset({"trw_deliver"})

# How many times a session may be hard-blocked by the post-compaction gate
# before the gate degrades to advisory for that session (audit C-5).
#
# Under stdio there is ONE ``ServerSession`` per process, so ``ctx.session_id``
# is a single value shared by the parent agent and every sub-agent it spawns
# (see data/hooks/subagent-start.sh: "A subagent shell inherits its parent's
# session id"). Ten of eleven bundled agents have no ``trw_session_start`` in
# their toolset, so an inherited-session sub-agent could never clear a gate the
# parent's id had already armed — every trw_* call it can make blocked, for the
# life of the server. Observed live three times in one session.
#
# The first N blocks behave exactly as before: a capable top-level agent is
# stopped and told precisely what to do, so the recovery guarantee holds. Only
# after repeated blocks with no intervening session_start — proof the caller
# CANNOT satisfy the gate — does it degrade to pass-through with the recovery
# text still prepended. The nudge is never removed (operator standing rule), and
# the disk marker is deliberately left in place so the genuine obligation
# survives for whoever can act on it.
_COMPACTION_GATE_MAX_BLOCKS: int = max(1, int(os.environ.get("TRW_COMPACTION_GATE_MAX_BLOCKS", "2")))

# session_id -> consecutive blocked trw_* calls since the last session_start.
_compaction_gate_attempts: dict[str, int] = {}

# Warning prepended to every non-exempt tool response when ceremony
# has not been run. Value-oriented framing — explains what the agent gains
# by calling session_start, rather than threatening consequences.
# Loaded from centralized messages.yaml with inline fallback.
_DEFAULT_CEREMONY_WARNING = (
    "trw_session_start() has not been called yet for this session.\n"
    "Without it, you are working without:\n"
    "  - Prior session learnings (patterns and gotchas that prevent re-work)\n"
    "  - Active run state (phase, progress, last checkpoint)\n"
    "Call trw_session_start() now \u2014 it takes one call and gives you the full context"
    " accumulated from all prior sessions."
)


def _load_ceremony_warning() -> str:
    """Load ceremony warning from centralized messages, with inline fallback."""
    from trw_mcp.prompts.messaging import get_message_or_default

    return get_message_or_default("ceremony_warning", _DEFAULT_CEREMONY_WARNING)


CEREMONY_WARNING = _load_ceremony_warning()

logger = structlog.get_logger(__name__)


def mark_session_active(session_id: str) -> None:
    """Mark a session as having completed ceremony."""
    _session_state[session_id] = True


def is_session_active(session_id: str) -> bool:
    """Return True if ceremony has been run for this session."""
    return _session_state.get(session_id, False)


def reset_state() -> None:
    """Clear all session state — for testing only."""
    global _compaction_signal_raised, _compaction_gate_generation

    _session_state.clear()
    _compaction_gate_sessions.clear()
    _compaction_gate_attempts.clear()
    _known_sessions.clear()
    _compaction_signal_raised = False
    _compaction_gate_generation = 0


def _annotate_operation_backed_claim(tool_name: str, result: object) -> None:
    """Validate any operation-backed claim in a tool result against the owner registry.

    PRD-CORE-215 FR03 production consumption point: a tool that returns an
    operation handle must be a registered operation owner (delivery -> the
    PRD-CORE-208 journal). When a payload declares operation-backed behavior the
    middleware stamps the registry's verdict (``valid``/``unowned_claim``) into
    the structured result so an unowned handle claim is visible, never silently
    trusted. Fail-open — it never blocks execution or touches a non-claim result.
    """
    try:
        payload = getattr(result, "structured_content", None)
        if not isinstance(payload, dict):
            return
        from trw_mcp.tools._operation_owner_adapter import (
            declares_operation_backed,
            validate_operation_backed_claim,
        )

        if not declares_operation_backed(payload):
            return
        payload["operation_backed_claim"] = validate_operation_backed_claim(tool_name, payload)
    except Exception:  # justified: fail-open -- claim annotation must never block a tool call
        logger.debug("operation_backed_claim_annotation_failed", exc_info=True)


def _touch_heartbeat_safe(session_id: str) -> None:
    """Touch the heartbeat file for the active run (PRD-QUAL-050-FR01).

    Deferred import avoids circular dependency between middleware and state.
    Completely fail-open — never blocks tool execution.
    """
    try:
        from trw_mcp.state._paths import touch_heartbeat

        touch_heartbeat(session_id=session_id)
    except Exception:  # justified: fail-open -- heartbeat must never block tool execution
        logger.warning("heartbeat_middleware_failed", exc_info=True)


def _is_compaction_gate_required() -> bool:
    """Return True when a pre-compaction marker indicates recovery is pending."""

    try:
        from trw_mcp.state.pre_compact_marker import pre_compact_marker_path

        return pre_compact_marker_path().exists()
    except Exception:  # justified: fail-open, compaction detection must never block tool execution
        logger.debug(
            "compaction_gate_detection_failed",
            component="ceremony",
            op="detect_compaction_gate",
            outcome="fail_open",
            exc_info=True,
        )
        return False


# PRD-CORE-258-FR10 ownership lives in its own module (LOC ratchet); the
# underscore names are kept here so existing tests and call sites keep working.
_marker_ownership = marker_ownership
_marker_owner_exempts = marker_owner_exempts


def _clear_compaction_gate_safe(ctx: object | None = None) -> None:
    """Clear the pre-compaction marker once session_start succeeds.

    PRD-CORE-258-FR10: an owned marker is cleared only by its owner — a session
    that never compacted owes nothing and must not destroy somebody else's
    recovery obligation. An ownerless marker keeps today's behaviour.

    PRD-CORE-258-FR08: a failure to unlink is an OPERATOR condition, not a
    routine one — a read-only filesystem or a directory occupying the name means
    ``Path.exists`` keeps returning True and every new process re-arms the gate.
    It is therefore reported at WARNING with the resolved path and the exception
    type, where DEBUG is below the level an operator reads. The caller is not
    deadlocked by it: the bounded escape still degrades the gate to advisory, so
    an undeletable marker costs a bounded number of blocks per session. No
    in-memory disarm override is introduced — that would silently discharge an
    obligation the disk still asserts.
    """

    marker_path = "<unresolved>"
    try:
        from trw_mcp.state.pre_compact_marker import pre_compact_marker_path

        resolved = pre_compact_marker_path()
        marker_path = str(resolved)
        if not resolved.exists():
            return
        # Ownership is read ONCE, immediately before the unlink, to keep the
        # check-then-delete window as small as a single-file design allows
        # (codex audit 2026-09-05 row 2; owner-keyed markers are the full fix).
        # Deleting is the irreversible act, so the fail direction here is the
        # opposite of the arm site: an owner we could not resolve is treated
        # as somebody else's obligation and left on disk.
        ownership = _marker_ownership(ctx)
        if ownership in ("foreign", "unknown"):
            logger.info(
                "compaction_gate_clear_skipped_foreign_owner",
                component="ceremony",
                op="clear_compaction_gate",
                marker_path=marker_path,
                outcome="not_the_owner" if ownership == "foreign" else "owner_unknown",
            )
            return
        resolved.unlink()
    except Exception as exc:  # justified: fail-open, marker cleanup must not break session start
        logger.warning(
            "compaction_gate_clear_failed",
            component="ceremony",
            op="clear_compaction_gate",
            marker_path=marker_path,
            error_type=type(exc).__name__,
            outcome="fail_open",
            exc_info=True,
        )


def _extract_session_start_payload(result: object) -> dict[str, object] | None:
    """Best-effort extraction of a session_start payload from ToolResult."""

    structured_content = getattr(result, "structured_content", None)
    if isinstance(structured_content, dict):
        return structured_content

    content = getattr(result, "content", [])
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, TextContent):
            continue
        try:
            payload = json.loads(block.text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _session_start_succeeded(result: object) -> bool:
    """Return True when the session_start payload explicitly reports success."""

    payload = _extract_session_start_payload(result)
    if payload is None:
        # Fail-closed: a non-JSON / unparseable result (e.g. an error or
        # exception ToolResult) is NOT an explicit success. Treating it as
        # success would clear the on-disk compaction-recovery marker and
        # silently destroy post-compaction recovery state.
        return False

    success = payload.get("success")
    if isinstance(success, bool):
        return success

    status = payload.get("status")
    if isinstance(status, str):
        return status.lower() == "success"

    # Fail-closed: a parsed payload that reports neither "success" nor
    # "status" is an unknown result — keep the ceremony/compaction gate
    # closed rather than marking the session active on ambiguous output.
    return False


def _raise_compaction_gate(session_id: str) -> None:
    """Blanket-gate the sessions known at this compaction signal's rising edge.

    Called once per rising edge. ``session_id`` is the session whose call
    observed the signal; it is already in ``_known_sessions`` (the middleware
    registers before evaluating the gate) so it is part of the snapshot and
    stays blocked — the session that actually compacted must still recover.
    """

    global _compaction_gate_generation

    _compaction_gate_generation += 1
    for known_session_id in list(_known_sessions):
        _compaction_gate_sessions[known_session_id] = True
    logger.info(
        "compaction_gate_raised",
        op="ceremony",
        component="ceremony",
        session_id=session_id,
        generation=_compaction_gate_generation,
        gated_session_count=len(_known_sessions),
    )


def _is_reviewer_role() -> bool:
    """True when this PROCESS runs the PRD-SEC-015 reviewer role (FR05).

    The reviewer surface deliberately denies ``trw_session_start``, which is the
    post-compaction gate's ONLY remedy, and a stateless reviewer never had
    context to recover in the first place — so gating it can only deadlock it or
    degrade it through the block bound while producing zero context integrity.

    The raw environment marker is consulted first (the role is declared by the
    process that spawned this one, and the reviewed repository's own config must
    not be able to revoke it), the typed config field second.

    Fail-CLOSED on any error: this predicate GRANTS an exemption, so an
    unreadable config must leave the gate armed rather than exempt every session.
    """
    if os.environ.get("TRW_SURFACE_ROLE", "").strip().lower() == "reviewer":
        return True
    try:
        from trw_mcp.models.config import get_config

        return str(getattr(get_config(), "surface_role", "agent")) == "reviewer"
    except Exception:  # justified: an exemption must never be granted by a fault
        logger.warning("ceremony_reviewer_role_lookup_failed", op="ceremony", outcome="not_exempt", exc_info=True)
        return False


def _is_compaction_gate_required_for_session(session_id: str, ctx: object | None = None) -> bool:
    """Return True when this session still owes post-compaction recovery.

    The blanket gate is scoped to the gate generation: only sessions that
    already existed when the signal was raised owe recovery. A session first
    seen afterwards is recorded as exempt (and the decision logged) so its
    trw_* calls pass through — otherwise a sub-agent whose toolset omits
    ``trw_session_start`` would be permanently unable to clear its own gate.

    PRD-CORE-258-FR10 adds a second scope on top of the generation: a marker that
    NAMES an owner arms only the session whose resolved pin key matches it. The
    read happens only for a session the generation already gated, so the
    pass-through hot path still costs one ``Path.exists``.

    Fail-open: any bookkeeping failure returns False rather than hard-blocking
    a tool call.
    """

    global _compaction_signal_raised

    try:
        signal_present = _is_compaction_gate_required()
        if not signal_present:
            _compaction_signal_raised = False
        elif not _compaction_signal_raised:
            # Latch only AFTER a successful snapshot: a transient failure must
            # re-arm the raise on the next call, never silently disarm the
            # gate for the rest of this generation.
            _raise_compaction_gate(session_id)
            _compaction_signal_raised = True
        elif session_id not in _compaction_gate_sessions:
            _compaction_gate_sessions[session_id] = False
            logger.info(
                "compaction_gate_session_exempt",
                op="ceremony",
                component="ceremony",
                session_id=session_id,
                generation=_compaction_gate_generation,
                outcome="registered_after_gate_raise",
            )
    except Exception:  # justified: fail-open -- gate scoping must never hard-block a tool call
        logger.warning(
            "compaction_gate_scoping_failed",
            component="ceremony",
            op="scope_compaction_gate",
            session_id=session_id,
            outcome="fail_open",
            exc_info=True,
        )
        return False

    gated = _compaction_gate_sessions.get(session_id, False)
    if gated and _marker_owner_exempts(ctx):
        logger.info(
            "compaction_gate_owner_scoped",
            op="ceremony",
            component="ceremony",
            session_id=session_id,
            generation=_compaction_gate_generation,
            outcome="marker_owned_by_another_session",
        )
        return False
    return gated


class CeremonyMiddleware(Middleware):
    """FastMCP middleware that enforces session ceremony.

    For every tool call:
    - If the tool is a ceremony tool, mark the session as active.
    - If the session is NOT active and the tool is a trw_* tool (non-ceremony),
      return an error response instead of executing (PRD-CORE-098-FR06).
    - If the session is NOT active and the tool is NOT a trw_* tool,
      prepend a warning TextContent block to the tool result.
    - If fastmcp_context is None (unit tests), do nothing.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        """Intercept tool calls to enforce ceremony state."""
        tool_name = context.message.name
        ctx = context.fastmcp_context

        # Graceful fallback: no MCP session context (unit tests, direct calls)
        if ctx is None or ctx.request_context is None:
            return await call_next(context)

        session_id = ctx.session_id
        _register_session(session_id)

        compaction_gate_required = _is_compaction_gate_required_for_session(session_id, ctx)

        # Ceremony tool called — mark session as active after a successful session_start
        if tool_name in CEREMONY_TOOLS:
            ceremony_result = await call_next(context)
            if _session_start_succeeded(ceremony_result):
                mark_session_active(session_id)
                _compaction_gate_sessions.pop(session_id, None)
                _compaction_gate_attempts.pop(session_id, None)
                # The in-memory pop above precedes the unlink DELIBERATELY: an
                # unlink that can never succeed must not re-arm the session that
                # just recovered (PRD-CORE-258-FR08 pins this ordering).
                _clear_compaction_gate_safe(ctx)
                logger.debug(
                    "ceremony_activated",
                    op="ceremony",
                    session_id=session_id,
                    tool=tool_name,
                )
            else:
                logger.info(
                    "ceremony_activation_skipped",
                    op="ceremony",
                    session_id=session_id,
                    tool=tool_name,
                    outcome="unsuccessful_session_start",
                )
            _touch_heartbeat_safe(session_id)
            return ceremony_result

        # Post-compaction gate (PRD-CORE-098-FR06): only block trw_* tools
        # when recovery is actually pending after context compaction, and never
        # the exempt tools (see COMPACTION_GATE_EXEMPT_TOOLS).
        if (
            compaction_gate_required
            and tool_name.startswith("trw_")
            and tool_name not in COMPACTION_GATE_EXEMPT_TOOLS
            # PRD-SEC-015-FR05: a reviewer lane is exempt — see _is_reviewer_role.
            and not _is_reviewer_role()
        ):
            blocked_count = _compaction_gate_attempts.get(session_id, 0) + 1
            _compaction_gate_attempts[session_id] = blocked_count
            # One payload builder for both branches below, so the re-block past
            # the bound cannot drift from the block before it.
            block = build_compaction_block(
                tool_name=tool_name,
                blocked_count=blocked_count,
                max_blocks=_COMPACTION_GATE_MAX_BLOCKS,
            )
            recovery_message = block.message
            past_bound = blocked_count > _COMPACTION_GATE_MAX_BLOCKS

            if not past_bound or tool_name in TERMINAL_TOOLS:
                logger.info(
                    "ceremony_gate_blocked",
                    op="ceremony",
                    session_id=session_id,
                    tool=tool_name,
                    compaction_gate_required=compaction_gate_required,
                    blocked_count=blocked_count,
                    marker_state=block.marker_state,
                    compaction_marker_ts=block.marker_ts,
                    marker_unreadable_reason=block.unreadable_reason,
                    terminal_tool_past_bound=past_bound,
                )
                return ToolResult(
                    content=[TextContent(type="text", text=recovery_message)],
                    structured_content=block.payload,
                )

            # Audit C-5: repeated blocks with no intervening session_start are
            # proof the caller CANNOT satisfy this gate — an inherited-session
            # sub-agent whose toolset omits trw_session_start. Degrade to
            # advisory rather than deadlock. The recovery text is still
            # prepended (the nudge is never removed) and the disk marker is
            # deliberately NOT cleared, so the real post-compaction obligation
            # survives for a caller able to discharge it.
            #
            # TERMINAL_TOOLS never reach here: a terminal act rides no escape
            # (PRD-CORE-258-FR09), so it was re-blocked above.
            logger.warning(
                "compaction_gate_degraded",
                op="ceremony",
                component="ceremony",
                session_id=session_id,
                tool=tool_name,
                blocked_count=blocked_count,
                threshold=_COMPACTION_GATE_MAX_BLOCKS,
                outcome="degraded_to_advisory_unsatisfiable_gate",
            )
            degraded: ToolResult = await call_next(context)
            degraded.content.insert(0, TextContent(type="text", text=recovery_message))
            _touch_heartbeat_safe(session_id)
            return degraded

        # Execute the tool
        result: ToolResult = await call_next(context)

        # Post-tool heartbeat: signal session liveness (PRD-QUAL-050-FR01)
        _touch_heartbeat_safe(session_id)

        # FR03: validate any operation-backed claim against the owner registry.
        _annotate_operation_backed_claim(tool_name, result)

        # If session is NOT active (non-trw tool), prepend warning. A reviewer is
        # exempt for the same reason as the gate above (PRD-SEC-015-FR05): the
        # warning asks for a ceremony whose entry point it is denied.
        if not is_session_active(session_id) and not _is_reviewer_role():
            warning_block = TextContent(type="text", text=CEREMONY_WARNING)
            result.content.insert(0, warning_block)
            logger.debug("ceremony_warning_injected", op="ceremony", session_id=session_id, tool=tool_name)

        return result
