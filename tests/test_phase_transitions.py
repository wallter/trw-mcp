"""PRD-INTENT-002 FR04/FR05b — phase-transition refresh + capability detection.

The capability-detection matrix (FR05b): the server persists the client's
advertised ``tools.listChanged`` capability per session at ``initialize``; on a
phase transition the middleware:
  - (capability advertised, *) → always emit notifications/tools/list_changed
  - (no capability, profile=notify) → warn + emit anyway
  - (no capability, profile=require_reconnect) → X-Phase-Changed header + close
  - (no capability, profile=silent) → no-op
"""

from __future__ import annotations

import pytest

from trw_mcp.middleware import _phase_transitions as pt


@pytest.fixture(autouse=True)
def _clean() -> None:
    pt.reset_transition_state()


def test_no_transition_when_phase_unchanged() -> None:
    """First observation seeds; a repeat of the same phase is not a transition."""
    assert pt.detect_transition("sess-1", "RESEARCH") is True  # seed → first sight
    assert pt.detect_transition("sess-1", "RESEARCH") is False


def test_transition_detected_on_phase_change() -> None:
    pt.detect_transition("sess-1", "RESEARCH")
    assert pt.detect_transition("sess-1", "IMPLEMENT") is True


def test_capability_persisted_and_read() -> None:
    """FR05b: the advertised tools.listChanged flag is stored per session."""
    pt.set_list_changed_capability("sess-1", advertised=True)
    assert pt.client_supports_list_changed("sess-1") is True
    # Unknown session defaults to False (absent == unsupported).
    assert pt.client_supports_list_changed("sess-unknown") is False


@pytest.mark.parametrize(
    ("advertised", "policy", "expected"),
    [
        (True, "silent", "notify"),  # capability wins regardless of policy
        (True, "require_reconnect", "notify"),
        (False, "notify", "notify"),  # best-effort emit
        (False, "require_reconnect", "require_reconnect"),
        (False, "silent", "silent"),
    ],
)
def test_capability_detection_matrix(advertised: bool, policy: str, expected: str) -> None:
    """FR05b: (capability, profile) → resolved transition action."""
    assert pt.resolve_transition_action(advertised=advertised, on_transition=policy) == expected


def test_resolve_action_unknown_policy_falls_back_to_require_reconnect() -> None:
    """FR05b default: an unknown/absent profile policy → require_reconnect (safest)."""
    assert pt.resolve_transition_action(advertised=False, on_transition="nonsense") == "require_reconnect"


@pytest.mark.asyncio
async def test_emit_list_changed_calls_session_notification() -> None:
    """FR04: the notify path invokes session.send_tool_list_changed()."""

    class _FakeSession:
        def __init__(self) -> None:
            self.sent = 0

        async def send_tool_list_changed(self) -> None:
            self.sent += 1

    class _FakeContext:
        def __init__(self, session: object) -> None:
            self.session = session

    session = _FakeSession()
    ctx = _FakeContext(session)
    sent = await pt.emit_list_changed(ctx)
    assert sent is True
    assert session.sent == 1


@pytest.mark.asyncio
async def test_emit_list_changed_fails_open_without_session() -> None:
    """FR04/NFR02: a missing/broken session is a no-op, never a crash."""
    assert await pt.emit_list_changed(None) is False
    assert await pt.emit_list_changed(object()) is False
