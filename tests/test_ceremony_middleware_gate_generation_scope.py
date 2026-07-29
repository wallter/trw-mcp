"""Gate-generation scoping tests for the post-compaction ceremony gate.

Regression cover for the unsatisfiable-gate deadlock: the blanket gate used to
mark EVERY known session whenever the shared compaction signal was observed, so
a sub-agent session — whose toolset is limited to ``trw_learn`` /
``trw_checkpoint`` / ``trw_recall`` / ``trw_build_check`` and therefore has no
``trw_session_start`` to clear the gate with — had all four of its tools blocked
for the life of the server process.

The gate is now scoped to the sessions known at the signal's rising edge. These
tests pin both halves of that contract: the pre-existing session stays blocked
(the safeguard survives), and a session first seen afterwards passes through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog
from mcp.types import TextContent

from tests._test_ceremony_middleware_gate_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeRequestContext,
    FakeToolResult,
    _seed_compaction_marker,
    _text,
    middleware,  # noqa: F401
)
from trw_mcp.middleware.ceremony import CeremonyMiddleware, is_session_active, reset_state

# Exactly the toolset a TRW sub-agent is allowlisted for — none of these can
# clear a compaction gate, so gating them must be satisfiable or not applied.
_SUBAGENT_TOOLSET = ("trw_learn", "trw_recall", "trw_build_check", "trw_checkpoint")


def _ctx(session_id: str, tool_name: str) -> FakeMiddlewareContext:
    return FakeMiddlewareContext(
        message=FakeMessage(name=tool_name),
        fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id=session_id)),
    )


class _Recorder:
    """call_next stub that records tool names and replays canned results."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, ctx: Any) -> Any:
        self.calls.append(ctx.message.name)
        if ctx.message.name == "trw_session_start":
            return FakeToolResult(
                content=[TextContent(type="text", text='{"success": true, "errors": []}')],
                structured_content={"success": True, "errors": []},
            )
        return FakeToolResult(content=[TextContent(type="text", text="tool ok")])


class TestGateGenerationScope:
    """The blanket gate applies only to the generation that owed recovery."""

    @pytest.fixture(autouse=True)
    def _local_clean_state(self) -> None:
        reset_state()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_session_known_before_signal_is_still_gated(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """(a) SAFEGUARD: a session that pre-dates the compaction signal is blocked."""
        trw_dir = tmp_path / ".trw"
        call_next = _Recorder()

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            # Pre-compaction traffic registers the session while no signal exists.
            pre = await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]
            assert _text(pre.content[-1]) == "tool ok"

            _seed_compaction_marker(tmp_path)
            blocked = await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]

        assert blocked.structured_content is not None
        assert blocked.structured_content["error"] == "session_start_required"
        assert call_next.calls == ["trw_recall"], "the gated call must not reach the tool"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gated_session_ungates_after_successful_session_start(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """(b) The gated session recovers by calling trw_session_start."""
        trw_dir = tmp_path / ".trw"
        call_next = _Recorder()

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]
            _seed_compaction_marker(tmp_path)
            blocked = await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]
            await middleware.on_call_tool(_ctx("session-main", "trw_session_start"), call_next)  # type: ignore[arg-type]
            recovered = await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]

        assert blocked.structured_content is not None
        assert blocked.structured_content["error"] == "session_start_required"
        assert recovered.structured_content is None
        assert _text(recovered.content[0]) == "tool ok"
        assert is_session_active("session-main")
        assert call_next.calls == ["trw_recall", "trw_session_start", "trw_recall"]
        assert not (trw_dir / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_session_first_seen_after_signal_is_not_gated(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """(c) A session registered after the rising edge passes through, and it is logged."""
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            # session-main observes the signal first -> it owns the gate generation.
            blocked = await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]
            with structlog.testing.capture_logs() as logs:
                allowed = await middleware.on_call_tool(_ctx("session-sub", "trw_recall"), call_next)  # type: ignore[arg-type]

        assert blocked.structured_content is not None
        assert blocked.structured_content["error"] == "session_start_required"
        assert allowed.structured_content is None
        assert _text(allowed.content[-1]) == "tool ok"
        assert call_next.calls == ["trw_recall"]
        # The marker is untouched: the exemption does not disarm recovery.
        assert (trw_dir / "context" / "pre_compact_state.json").exists()
        exempt_events = [
            entry
            for entry in logs
            if entry.get("event") == "compaction_gate_session_exempt" and entry.get("session_id") == "session-sub"
        ]
        assert exempt_events, f"exemption must be observable, got {logs}"
        assert exempt_events[0]["outcome"] == "registered_after_gate_raise"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_subagent_toolset_usable_without_session_start_after_raise(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """(d) DEADLOCK: a post-raise session with no trw_session_start keeps all four tools."""
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]
            outs = [
                await middleware.on_call_tool(_ctx("session-sub", tool), call_next)  # type: ignore[arg-type]
                for tool in _SUBAGENT_TOOLSET
            ]

        for tool, out in zip(_SUBAGENT_TOOLSET, outs, strict=True):
            assert out.structured_content is None, f"{tool} must not be gated"
            assert _text(out.content[-1]) == "tool ok", f"{tool} must reach the tool"
        assert call_next.calls == list(_SUBAGENT_TOOLSET)
        # The session that actually compacted is still blocked throughout.
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            still_blocked = await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]
        assert still_blocked.structured_content is not None
        assert still_blocked.structured_content["error"] == "session_start_required"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_scoping_exception_fails_open(self, middleware: CeremonyMiddleware, tmp_path: Path) -> None:
        """(e) An exception inside the gate-scoping bookkeeping never hard-blocks a call."""
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.middleware.ceremony._raise_compaction_gate",
                side_effect=RuntimeError("bookkeeping exploded"),
            ),
            structlog.testing.capture_logs() as logs,
        ):
            out = await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]

        assert out.structured_content is None, "fail-open: the call must execute"
        assert call_next.calls == ["trw_recall"]
        assert any(
            entry.get("event") == "compaction_gate_scoping_failed" and entry.get("outcome") == "fail_open"
            for entry in logs
        ), f"fail-open must be logged, got {logs}"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_new_compaction_regates_a_previously_exempt_session(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """A session exempt from generation N is gated by generation N+1 it was present for."""
        trw_dir = tmp_path / ".trw"
        marker = trw_dir / "context" / "pre_compact_state.json"
        call_next = _Recorder()

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            _seed_compaction_marker(tmp_path)
            await middleware.on_call_tool(_ctx("session-main", "trw_recall"), call_next)  # type: ignore[arg-type]
            exempt = await middleware.on_call_tool(_ctx("session-sub", "trw_recall"), call_next)  # type: ignore[arg-type]
            marker.unlink()
            # Signal drops, then a second compaction happens while both are known.
            await middleware.on_call_tool(_ctx("session-sub", "trw_recall"), call_next)  # type: ignore[arg-type]
            _seed_compaction_marker(tmp_path)
            regated = await middleware.on_call_tool(_ctx("session-sub", "trw_recall"), call_next)  # type: ignore[arg-type]

        assert exempt.structured_content is None
        assert regated.structured_content is not None
        assert regated.structured_content["error"] == "session_start_required"
        assert call_next.calls == ["trw_recall", "trw_recall"]
