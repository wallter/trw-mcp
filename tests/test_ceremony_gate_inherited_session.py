"""The post-compaction gate must be satisfiable by an INHERITED session (audit C-5).

READ THIS BEFORE "SIMPLIFYING" THE DUPLICATE SESSION ID BELOW.

Every one of these tests deliberately uses ONE session id for both the parent
agent and the sub-agent. That is not redundancy to clean up — it is the entire
point. Under stdio there is exactly one ``ServerSession`` per process, and
FastMCP caches the generated ``ctx.session_id`` on it, so a parent and every
sub-agent it spawns present the SAME id to this middleware. TRW's own hook says
so outright (``data/hooks/subagent-start.sh``: "A subagent shell inherits its
parent's session id").

The pre-existing suite (``test_ceremony_middleware_gate_generation_scope.py``)
models parent and sub-agent as ``"session-main"`` vs ``"session-sub"``. Those
distinct ids let the "first seen after the rising edge is exempt" escape fire,
so the suite passed green while the deadlock was live in production:

  * the parent's id is registered and marked owing-recovery at the rising edge;
  * the sub-agent presents that SAME id, so the exemption at
    ``_is_compaction_gate_required_for_session`` never fires for it;
  * ten of eleven bundled agents have no ``trw_session_start`` in their toolset,
    so the only clear-site is unreachable;
  * => every ``trw_*`` call the sub-agent can make is blocked for the life of
    the server.

Observed live three times in one session (a ``trw_learn`` call returning
``session_start_required`` with no way to clear it).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
from trw_mcp.middleware import ceremony as ceremony_module
from trw_mcp.middleware.ceremony import CeremonyMiddleware, reset_state

# The single id a parent and all its sub-agents share under stdio.
INHERITED = "shared-stdio-session"

# Exactly the toolset a TRW sub-agent is allowlisted for. None of these can
# clear a compaction gate.
#
# Since 2026-07-26 three of the four are exempt outright
# (``EVIDENCE_RECORDING_TOOLS``): they only record what already happened, and
# gating them destroyed unrecoverable evidence. ``trw_recall`` is the sole
# member still gated — it SHAPES the next decision — so it is the probe every
# blocking assertion below uses. Probing with ``trw_learn``, as this module did
# originally, would now assert gate behavior using a tool outside the gate.
_SUBAGENT_TOOLSET = ("trw_learn", "trw_recall", "trw_build_check", "trw_checkpoint")


def _ctx(session_id: str, tool_name: str) -> FakeMiddlewareContext:
    return FakeMiddlewareContext(
        message=FakeMessage(name=tool_name),
        fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id=session_id)),
    )


class _Recorder:
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


class TestInheritedSessionIsNotDeadlocked:
    @pytest.fixture(autouse=True)
    def _local_clean_state(self) -> Iterator[None]:
        reset_state()
        yield
        reset_state()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_inherited_session_subagent_toolset_is_not_permanently_gated(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """THE regression: a sub-agent sharing the parent's id must escape.

        Asserts on ``call_next.calls`` — the tool actually running — not merely
        on the absence of an error payload, so a future change that returns a
        friendlier refusal without executing anything still fails.
        """
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with patch_trw_dir(trw_dir):
            # The parent observes the signal and is gated on the shared id.
            await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]
            # The sub-agent now hammers its (session_start-less) toolset.
            for _ in range(ceremony_module._COMPACTION_GATE_MAX_BLOCKS + 2):
                for tool in _SUBAGENT_TOOLSET:
                    await middleware.on_call_tool(_ctx(INHERITED, tool), call_next)  # type: ignore[arg-type]

        for tool in _SUBAGENT_TOOLSET:
            assert tool in call_next.calls, (
                f"{tool} never reached the tool — an inherited-session sub-agent is "
                "still deadlocked by the compaction gate"
            )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_first_block_still_hard_gates_a_capable_session(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """The guarantee survives: the first call is still a hard refusal."""
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with patch_trw_dir(trw_dir):
            first = await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]

        assert first.structured_content is not None
        assert first.structured_content["error"] == "session_start_required"
        assert call_next.calls == [], "the first blocked call must not execute the tool"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_degradation_preserves_the_nudge_and_the_disk_marker(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """Degrading must not discard the obligation or the recovery instruction."""
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with patch_trw_dir(trw_dir):
            for _ in range(ceremony_module._COMPACTION_GATE_MAX_BLOCKS):
                await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]
            degraded = await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]

        assert degraded.structured_content is None, "past the bound the tool must run"
        assert "trw_session_start" in _text(degraded.content[0]), (
            "the recovery instruction must still be prepended — the nudge is never removed"
        )
        assert (trw_dir / "context" / "pre_compact_state.json").exists(), (
            "degradation must NOT clear the marker: the real obligation survives for a caller able to discharge it"
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_degradation_is_observable_in_structured_logs(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """An operator must be able to see that a gate degraded, and for whom."""
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with patch_trw_dir(trw_dir):
            for _ in range(ceremony_module._COMPACTION_GATE_MAX_BLOCKS):
                await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]
            with structlog.testing.capture_logs() as logs:
                await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]

        degraded = [entry for entry in logs if entry.get("event") == "compaction_gate_degraded"]
        assert degraded, f"degradation must be observable, got {logs}"
        assert degraded[0]["session_id"] == INHERITED
        assert degraded[0]["blocked_count"] > ceremony_module._COMPACTION_GATE_MAX_BLOCKS

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_successful_session_start_resets_the_attempt_counter(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """A recovered session starts fresh, so a LATER compaction hard-gates again.

        Without the reset, one degradation would permanently spend the session's
        budget and every future compaction would degrade on its first call.
        """
        trw_dir = _seed_compaction_marker(tmp_path)
        call_next = _Recorder()

        with patch_trw_dir(trw_dir):
            await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]
            await middleware.on_call_tool(_ctx(INHERITED, "trw_session_start"), call_next)  # type: ignore[arg-type]

        assert ceremony_module._compaction_gate_attempts.get(INHERITED, 0) == 0

        # A fresh compaction must hard-block again, not degrade immediately.
        # The gate arms on a RISING edge, so the latch has to observe the marker
        # absent (session_start deleted it) before a re-seed counts as new.
        with patch_trw_dir(trw_dir):
            await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]
        trw_dir = _seed_compaction_marker(tmp_path)
        with patch_trw_dir(trw_dir):
            again = await middleware.on_call_tool(_ctx(INHERITED, "trw_recall"), call_next)  # type: ignore[arg-type]

        assert again.structured_content is not None
        assert again.structured_content["error"] == "session_start_required"


def patch_trw_dir(trw_dir: Path) -> Any:
    from unittest.mock import patch

    return patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir)
