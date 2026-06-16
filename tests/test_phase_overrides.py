"""PRD-INTENT-002 FR06 — trw_request_tool_access override tool + ledger.

The override grants single-use, session-scoped, TTL-capped access to a masked
tool. NFR03 requires a non-empty reason >= 20 chars; NFR02 caps the TTL at
5 minutes regardless of the requested value.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools import phase_overrides
from trw_mcp.tools.phase_overrides import (
    _MAX_TTL_SECONDS,
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
    assert grant.ttl_seconds == _MAX_TTL_SECONDS


def test_override_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR06: an expired override is no longer active."""
    times = iter([1000.0, 1000.0 + _MAX_TTL_SECONDS + 1])
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
    result = fn(
        tool_name="trw_review",
        reason="emergency cross-phase debugging session",
    )

    assert result["granted"] is False
    assert result["error"] == "session_id_unavailable"
    # No grant landed under any sentinel bucket.
    assert has_active_override("unknown", "trw_review") is False
    assert has_active_override("", "trw_review") is False
