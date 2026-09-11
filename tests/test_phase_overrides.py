"""PRD-INTENT-002 FR06 — trw_request_tool_access override tool + ledger.

The override grants single-use, session-scoped, TTL-capped access to a masked
tool. NFR03 requires a non-empty reason >= 20 chars; NFR02 caps the TTL at
5 minutes regardless of the requested value.
"""

from __future__ import annotations

import asyncio

import pytest

from trw_mcp.tools import phase_overrides
from trw_mcp.tools.phase_overrides import (
    _max_ttl_seconds,
    consume_override,
    grant_override,
    has_active_override,
)


@pytest.fixture(autouse=True)
def _clean_ledger() -> None:
    phase_overrides.reset_overrides()


def test_override_single_use() -> None:
    """FR06: grant → first consume succeeds → second consume re-masks."""
    grant_override("sess-1", "trw_review", reason="emergency cross-phase debug")
    assert has_active_override("sess-1", "trw_review") is True
    assert consume_override("sess-1", "trw_review") is True
    # Consumed — no longer active.
    assert has_active_override("sess-1", "trw_review") is False
    assert consume_override("sess-1", "trw_review") is False


def test_override_is_session_scoped() -> None:
    """FR06: an override for one session does not leak to another."""
    grant_override("sess-1", "trw_review", reason="emergency cross-phase debug")
    assert has_active_override("sess-2", "trw_review") is False


def test_override_ttl_capped_at_five_minutes() -> None:
    """NFR02: a requested TTL above the cap is clamped to 5 minutes."""
    grant = grant_override("sess-1", "trw_review", reason="emergency cross-phase debug", ttl_seconds=99_999)
    assert grant.ttl_seconds == _max_ttl_seconds()


def test_override_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR06: an expired override is no longer active."""
    times = iter([1000.0, 1000.0 + _max_ttl_seconds() + 1])
    monkeypatch.setattr(phase_overrides, "_now", lambda: next(times))
    grant_override("sess-1", "trw_review", reason="emergency cross-phase debug")
    # Second _now() call (inside has_active_override) is past the TTL.
    assert has_active_override("sess-1", "trw_review") is False


def test_request_tool_access_rejects_short_reason() -> None:
    """NFR03: reason shorter than 20 chars is rejected."""
    result = phase_overrides.request_tool_access("sess-1", "trw_review", reason="too short")
    assert result["granted"] is False
    assert "reason" in result["error"].lower()
    assert has_active_override("sess-1", "trw_review") is False


def test_request_tool_access_grants_with_valid_reason() -> None:
    """FR06: a valid request grants an override and returns its id."""
    result = phase_overrides.request_tool_access(
        "sess-1", "trw_review", reason="emergency cross-phase debugging session"
    )
    assert result["granted"] is True
    assert result["override_id"]
    assert result["expires_at"]
    assert has_active_override("sess-1", "trw_review") is True


def test_request_tool_access_rejects_unknown_tool() -> None:
    """Negative: an override on a non-existent tool is rejected."""
    result = phase_overrides.request_tool_access(
        "sess-1", "trw_not_a_real_tool", reason="emergency cross-phase debugging session"
    )
    assert result["granted"] is False
    assert "tool" in result["error"].lower()


def test_register_tool_exposes_trw_request_tool_access() -> None:
    """FR06: the registrar exposes trw_request_tool_access on the server."""
    from tests.conftest import extract_tool_fn, make_test_server

    server = make_test_server("phase_overrides")
    fn = extract_tool_fn(server, "trw_request_tool_access")
    assert fn is not None


def test_request_tool_access_rejects_when_session_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint-97 adaptive-surface review F2: when session resolution yields no
    id, the override tool must FAIL CLOSED (granted=False,
    error='session_id_unavailable') rather than pooling the grant under a shared
    'unknown' sentinel bucket — which would let one session consume another's
    grant (cross-session grant pollution).
    """
    from tests.conftest import extract_tool_fn, make_test_server

    # Force the in-tool session resolution to return an empty id (no request ctx).
    monkeypatch.setattr(
        "trw_mcp.middleware._phase_session.safe_session_id_from_context",
        lambda ctx: "",
    )

    server = make_test_server("phase_overrides")
    fn = extract_tool_fn(server, "trw_request_tool_access")
    # The tool is async since it now emits notifications/tools/list_changed after
    # a grant (a grant that never refreshes the client leaves the tool uncallable).
    result = asyncio.run(
        fn(
            tool_name="trw_review",
            reason="emergency cross-phase debugging session",
        )
    )

    assert result["granted"] is False
    assert result["error"] == "session_id_unavailable"
    # No grant landed under any sentinel bucket.
    assert has_active_override("unknown", "trw_review") is False
    assert has_active_override("", "trw_review") is False


def test_grant_notifies_the_client_to_refresh_its_tool_list(monkeypatch) -> None:
    """A grant that does not refresh the client leaves the tool UNCALLABLE.

    `on_list_tools` unions `_active_override_tools`, so the server WOULD advertise
    the tool on the next `tools/list`. But nothing told the client to re-list, so
    a capable client kept its cached view and the single-use, 5-minute grant
    expired unused — `granted: true` while the tool never became callable.
    Observed live 2026-07-25: a grant for `trw_prd_validate` returned
    `granted: true` and the tool remained unreachable.
    """
    monkeypatch.setattr(
        "trw_mcp.middleware._phase_session.safe_session_id_from_context",
        lambda ctx: "sess-1",
    )
    monkeypatch.setattr("fastmcp.server.dependencies.get_context", lambda: object())
    emitted: list[object] = []

    async def _fake_emit(ctx: object) -> bool:
        emitted.append(ctx)
        return True

    monkeypatch.setattr("trw_mcp.middleware._phase_transitions.emit_list_changed", _fake_emit)

    from tests.conftest import extract_tool_fn, make_test_server

    server = make_test_server("phase_overrides")
    fn = extract_tool_fn(server, "trw_request_tool_access")
    result = asyncio.run(fn(tool_name="trw_review", reason="emergency cross-phase debugging session"))

    assert result["granted"] is True
    assert len(emitted) == 1, "a grant must emit without bespoke client capability opt-in"
    assert has_active_override("sess-1", "trw_review"), "notification does not consume the grant"
    assert result["client_notified"] is True
    assert "action_required" not in result


def test_grant_says_so_when_the_client_cannot_be_notified(monkeypatch) -> None:
    """An un-notified grant tells the AGENT to call the tool, not a human to reconnect.

    Returning a bare `granted: true` here is a reports-success shape: the caller
    reads it as "the tool is now callable". But the previous remedy text was
    itself wrong in the other direction -- it sent the caller to a human
    keystroke ("ask the operator", "reconnect"), which is unusable advice for
    the one case this tool exists to serve, an autonomous session that needs a
    masked tool. The clamped TTL then expired unused (sub_hJ96RkVjxsLXwqWA).

    The grant does not depend on the listing: FastMCP resolves tools/call by
    name out of the registry, and both masking middlewares honour an active
    override for a tool they are hiding. So the correct instruction is to call
    it. See the companion test below, which proves that end to end rather than
    asserting the sentence.
    """
    monkeypatch.setattr(
        "trw_mcp.middleware._phase_session.safe_session_id_from_context",
        lambda ctx: "sess-2",
    )
    monkeypatch.setattr("fastmcp.server.dependencies.get_context", lambda: object())
    emitted: list[object] = []

    async def failed_emit(ctx: object) -> bool:
        emitted.append(ctx)
        return False

    monkeypatch.setattr("trw_mcp.middleware._phase_transitions.emit_list_changed", failed_emit)

    from tests.conftest import extract_tool_fn, make_test_server

    server = make_test_server("phase_overrides")
    fn = extract_tool_fn(server, "trw_request_tool_access")
    result = asyncio.run(fn(tool_name="trw_review", reason="emergency cross-phase debugging session"))

    assert result["granted"] is True
    assert len(emitted) == 1, "failure means notification was attempted"
    assert has_active_override("sess-2", "trw_review")
    assert result["client_notified"] is False
    action = str(result["action_required"])
    assert "call it anyway" in action, "the agent must be told the tool is already callable"
    assert "consume this override" in action
    assert "single-use" in action and "server restarts" in action
    # The remedy must not send an agent to a human keystroke it cannot perform.
    assert "reconnect" not in action.lower()
    assert "ask the operator" not in action.lower()


def test_one_grant_survives_both_masking_gates_in_a_single_request() -> None:
    """A grant is one CALL, and one call passes through BOTH masking layers.

    This test previously called ``_consume_override`` twice and asserted the
    second returned False -- which looked like "single-use" and was in fact the
    bug. SurfaceAuthority runs before PhaseExposure and both funnel through the
    same ledger, so on a tool masked at both layers the first gate popped the
    grant and the second denied the call with ``tool_not_in_phase``, having
    executed no handler. The caller got ``granted: true``, followed the advice
    to call the tool, and lost both the call and the grant.

    Found independently by two reviewers. The old assertion was vacuous about
    the thing that mattered: it never modelled one request crossing two gates.
    """
    from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware
    from trw_mcp.tools.phase_overrides import consume_override

    class _Request:
        """Stand-in for the per-call MiddlewareContext FastMCP passes down the chain."""

    mw = PhaseExposureMiddleware()

    def one_request(req: object) -> tuple[bool, bool]:
        # Gate order matches _build_middleware: surface authority, then phase exposure.
        return (
            consume_override("sess-2gates", "trw_review", req),
            mw._consume_override("sess-2gates", "trw_review", req),
        )

    grant_override("sess-2gates", "trw_review", reason="one grant crossing both masking gates")

    first_surface, first_phase = one_request(_Request())
    assert first_surface is True and first_phase is True, (
        "both gates must admit the SAME request; one grant is one call, not one gate"
    )

    second_surface, second_phase = one_request(_Request())
    assert second_surface is False and second_phase is False, (
        "the grant is still single-use: a LATER request carries no stamp and finds it gone"
    )
    assert not has_active_override("sess-2gates", "trw_review")

    # And the stamp must not leak between requests that happen to share a task.
    grant_override("sess-2gates", "trw_review", reason="a second grant for the leak check")
    req_a = _Request()
    assert one_request(req_a) == (True, True)
    assert one_request(req_a) == (True, True), (
        "the SAME request object legitimately re-reads its own stamp; a different one must not"
    )
    assert one_request(_Request()) == (False, False), "a fresh request sees the grant is spent"


def test_grants_are_not_refused_for_registered_tools_outside_the_phase_policy() -> None:
    """The refusal message used to be a false statement.

    ``_is_registered_tool`` checked only the phase policy, which is a SUBSET of
    the registry, so a grant for a genuinely registered tool absent from the
    policy came back "tool '...' is not a registered MCP tool".
    """
    from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY
    from trw_mcp.server._tools import raw_registered_tool_names
    from trw_mcp.tools.phase_overrides import _is_registered_tool

    policy = set(DEFAULT_PHASE_POLICY.safe_set)
    for tools in DEFAULT_PHASE_POLICY.allowed_tools_by_phase.values():
        policy.update(tools)
    outside = sorted(raw_registered_tool_names() - policy)
    assert outside, "no registered-but-unpolicied tool left to guard the regression with"

    for name in outside:
        assert _is_registered_tool(name), f"{name} is registered but was reported as unknown"
    assert not _is_registered_tool("trw_not_a_real_tool_at_all")


def test_the_grant_ttl_ceiling_is_operator_configurable(monkeypatch) -> None:
    """The ceiling was a bare module constant, so the one knob that matters was unreachable.

    A client that cannot refresh its tool list in-session may need a grant that
    outlives the historical 300s cap; how long depends on that client, not on
    TRW. Clamping still happens -- only the ceiling moved into config.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.tools.phase_overrides import grant_override

    monkeypatch.setattr(get_config(), "tool_access_grant_max_ttl_seconds", 900)

    at_ceiling = grant_override("sess-ttl", "trw_review", reason="a configured ceiling applies here")
    assert at_ceiling.ttl_seconds == 900, "None requests the configured ceiling, not the old constant"

    clamped = grant_override(
        "sess-ttl2", "trw_review", reason="an over-large request is still clamped", ttl_seconds=99999
    )
    assert clamped.ttl_seconds == 900, "the clamp still binds; only the ceiling is configurable"

    under = grant_override("sess-ttl3", "trw_review", reason="a smaller request is honoured as asked", ttl_seconds=60)
    assert under.ttl_seconds == 60
