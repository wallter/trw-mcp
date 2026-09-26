"""Best-effort ``notifications/tools/list_changed`` emission."""

from __future__ import annotations

import pytest

from trw_mcp.middleware import _phase_transitions as pt


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
