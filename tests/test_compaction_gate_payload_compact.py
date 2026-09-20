"""Bound recovery prose without changing CORE-258's gate or payload contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.middleware import _compaction_gate_payload as payload


@pytest.mark.unit
@pytest.mark.parametrize("timestamp", ["2026-09-19T16:00:00+00:00", None])
@pytest.mark.parametrize("attempt", [1, 2, 3, 1000])
def test_recovery_message_is_compact_and_retains_actionable_contract(
    monkeypatch: pytest.MonkeyPatch, timestamp: str | None, attempt: int
) -> None:
    monkeypatch.setattr(payload, "_read_marker_instant", lambda: (timestamp, None if timestamp else "invalid_json"))
    block = payload.build_compaction_block("trw_inbox", attempt, 2)
    assert len(block.message.split()) <= 65
    assert "compact" in block.message
    assert "trw_session_start()" in block.message
    assert "recover" in block.message and "clear" in block.message
    assert "delegate" in block.message.lower()
    assert "retry" in block.message and "2" in block.message
    assert "dispatcher" in block.message
    assert block.payload["message"] == block.message
    assert block.payload["error"] == "post_compaction_recovery_required"
    assert block.payload["remedy"] == "trw_session_start"
    assert block.payload["blocked_count"] == attempt
    assert block.payload["max_blocks"] == 2
    assert block.payload["compaction_marker_ts"] == timestamp
    if timestamp:
        assert timestamp in block.message and block.marker_state == "read"
    else:
        assert "could not be read" in block.message and block.marker_state == "unreadable"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocked_and_degraded_inbox_keep_compact_notice_and_terminal_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp.types import TextContent

    from tests._test_ceremony_middleware_gate_support import (
        FakeContext,
        FakeMessage,
        FakeMiddlewareContext,
        FakeRequestContext,
        FakeToolResult,
        _seed_compaction_marker,
    )
    from trw_mcp.middleware.ceremony import CeremonyMiddleware, reset_state

    reset_state()
    trw_dir = _seed_compaction_marker(tmp_path)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    middleware = CeremonyMiddleware()
    ctx = FakeMiddlewareContext(FakeMessage("trw_inbox"), FakeContext(FakeRequestContext("compact-notice")))
    calls: list[str] = []

    async def call_next(context: Any) -> Any:
        calls.append(context.message.name)
        return FakeToolResult(
            content=[TextContent(type="text", text='{"status":"ok","items":[]}')],
            structured_content={"status": "ok", "items": []},
        )

    try:
        for attempt in range(1, 6):
            result = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
            texts = [block.text for block in result.content if isinstance(block, TextContent)]
            assert len(" ".join(texts).split()) <= 65
            assert "trw_session_start()" in texts[0]
            if attempt <= 2:
                assert result.structured_content["error"] == "post_compaction_recovery_required"
                assert not calls
            else:
                assert result.structured_content == {"status": "ok", "items": []}
                assert len(texts) == 2
        ctx.message.name = "trw_deliver"
        terminal = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]
        assert terminal.structured_content["error"] == "post_compaction_recovery_required"
        assert "trw_deliver" not in calls
        assert (trw_dir / "context" / "pre_compact_state.json").exists()
    finally:
        reset_state()
