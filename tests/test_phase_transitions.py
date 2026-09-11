"""Standard best-effort phase refresh and per-session transition deduplication."""

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


@pytest.mark.asyncio
async def test_emit_list_changed_preserves_dispatch_on_transport_failure() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    send = AsyncMock(side_effect=RuntimeError("closed session"))
    ctx = SimpleNamespace(session=SimpleNamespace(send_tool_list_changed=send))
    assert await pt.emit_list_changed(ctx) is False
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_policy", ["notify", "require_reconnect", "silent"])
async def test_legacy_transition_metadata_does_not_control_refresh(monkeypatch, legacy_policy):
    from types import SimpleNamespace

    from structlog.testing import capture_logs

    from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware
    from trw_mcp.models.config._client_profile import ClientProfile
    from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY

    profile = ClientProfile(client_id="test", display_name="Test", on_transition=legacy_policy)
    assert ClientProfile.model_validate(profile.model_dump()).on_transition == legacy_policy
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: SimpleNamespace(client_profile=profile))
    pt.reset_transition_state()
    emitted = []

    async def emit(ctx):
        emitted.append(ctx)
        return True

    monkeypatch.setattr("trw_mcp.middleware.phase_exposure.emit_list_changed", emit)
    mw = PhaseExposureMiddleware(enabled=True, policy=DEFAULT_PHASE_POLICY)
    sentinel = object()
    with capture_logs() as logs:
        await mw._on_phase_transition(session_id="s", phase="RESEARCH", ctx=sentinel)
        await mw._on_phase_transition(session_id="s", phase="RESEARCH", ctx=sentinel)
        await mw._on_phase_transition(session_id="s", phase="IMPLEMENT", ctx=sentinel)
    assert emitted == [sentinel, sentinel]
    assert not any(e.get("event") == "phase_transition_require_reconnect" for e in logs)
