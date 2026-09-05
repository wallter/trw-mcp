"""Session-isolation tests for post-compaction ceremony gate."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mcp.types import TextContent

from tests._test_ceremony_middleware_gate_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeRequestContext,
    FakeToolResult,
    _clean_state,  # noqa: F401  # autouse: resets middleware module state per test
    _seed_compaction_marker,
    _text,
    middleware,  # noqa: F401
    session_ctx,  # noqa: F401
)
from trw_mcp.middleware.ceremony import CeremonyMiddleware, is_session_active


class TestCompactionGate:
    """Tests for the post-compaction gate that blocks trw_* tools."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_remains_per_session_after_sibling_recovers(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """A sibling session that already owes recovery stays blocked until its own start succeeds."""
        trw_dir = _seed_compaction_marker(tmp_path)
        session_a = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        session_b = FakeContext(request_context=FakeRequestContext(session_id="session-b"))
        start_result = FakeToolResult(
            content=[TextContent(type="text", text='{"success": true, "errors": []}')],
            structured_content={"success": True, "errors": []},
        )
        checkpoint_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_names: list[str] = []

        async def call_next(ctx: Any) -> Any:
            call_names.append(ctx.message.name)
            if ctx.message.name == "trw_session_start":
                return start_result
            return checkpoint_result

        blocked_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_recall"),
            fastmcp_context=session_b,
        )
        start_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_a,
        )

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            first_block = await middleware.on_call_tool(blocked_ctx, call_next)  # type: ignore[arg-type]
            start_out = await middleware.on_call_tool(start_ctx, call_next)  # type: ignore[arg-type]
            second_block = await middleware.on_call_tool(blocked_ctx, call_next)  # type: ignore[arg-type]

        assert first_block.structured_content is not None
        assert first_block.structured_content["error"] == "post_compaction_recovery_required"
        assert start_out.structured_content == {"success": True, "errors": []}
        assert is_session_active("session-a")
        assert not is_session_active("session-b")
        assert call_names == ["trw_session_start"]
        assert second_block.structured_content is not None
        assert second_block.structured_content["error"] == "post_compaction_recovery_required"
        assert not (trw_dir / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_known_sibling_session_stays_blocked_after_other_session_clears_marker(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """A known sibling session cannot bypass recovery just because another session cleared the file."""
        session_a = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        session_b = FakeContext(request_context=FakeRequestContext(session_id="session-b"))
        initial_start_result = FakeToolResult(content=[TextContent(type="text", text="started")])
        recovered_start_result = FakeToolResult(
            content=[TextContent(type="text", text='{"success": true, "errors": []}')],
            structured_content={"success": True, "errors": []},
        )
        checkpoint_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_names: list[str] = []

        async def call_next(ctx: Any) -> Any:
            call_names.append(ctx.message.name)
            if ctx.message.name == "trw_session_start" and len(call_names) == 1:
                return initial_start_result
            if ctx.message.name == "trw_session_start":
                return recovered_start_result
            return checkpoint_result

        start_a_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_a,
        )
        checkpoint_b_ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_recall"),
            fastmcp_context=session_b,
        )

        trw_dir = tmp_path / ".trw"
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            await middleware.on_call_tool(start_a_ctx, call_next)  # type: ignore[arg-type]
            await middleware.on_call_tool(
                FakeMiddlewareContext(
                    message=FakeMessage(name="Read"),
                    fastmcp_context=session_b,
                ),
                call_next,
            )
            _seed_compaction_marker(tmp_path)
            await middleware.on_call_tool(start_a_ctx, call_next)  # type: ignore[arg-type]
            blocked_out = await middleware.on_call_tool(checkpoint_b_ctx, call_next)  # type: ignore[arg-type]

        assert blocked_out.structured_content is not None
        assert blocked_out.structured_content["error"] == "post_compaction_recovery_required"
        assert call_names == ["trw_session_start", "Read", "trw_session_start"]

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_inactive_without_compaction(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Normal flow: session started first, then tools work without interference."""
        start_result = FakeToolResult(content=[TextContent(type="text", text='{"status":"success"}')])
        learn_result = FakeToolResult(content=[TextContent(type="text", text="learned")])
        build_result = FakeToolResult(content=[TextContent(type="text", text="built")])
        results = [start_result, learn_result, build_result]
        idx = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal idx
            result = results[idx]
            idx += 1
            return result

        ctx1 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        await middleware.on_call_tool(ctx1, call_next)  # type: ignore[arg-type]

        ctx2 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_recall"),
            fastmcp_context=session_ctx,
        )
        out2 = await middleware.on_call_tool(ctx2, call_next)  # type: ignore[arg-type]
        assert _text(out2.content[0]) == "learned"
        assert len(out2.content) == 1

        ctx3 = FakeMiddlewareContext(
            message=FakeMessage(name="trw_recall"),
            fastmcp_context=session_ctx,
        )
        out3 = await middleware.on_call_tool(ctx3, call_next)  # type: ignore[arg-type]
        assert _text(out3.content[0]) == "built"
        assert len(out3.content) == 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Patches:
    """Enter several ``patch`` objects as one context manager."""

    def __init__(self, *patches: Any) -> None:
        self._patches = patches

    def __enter__(self) -> None:
        for p in self._patches:
            p.__enter__()

    def __exit__(self, *exc: object) -> None:
        for p in reversed(self._patches):
            p.__exit__(*exc)


class TestOnlyTheMarkersOwnerIsArmedOrMayClearIt:
    """PRD-CORE-258-FR10: the marker names its owner (audit rows 1 and 2).

    Six concurrent ``trw-mcp`` server processes were counted on the authoring
    machine at 22:00Z on 2026-09-04, so a marker that names no session at all is
    the steady state, not a corner case: a fresh process observed a stale marker
    as its own rising edge, and ANY session's successful start unlinked it.

    The owner is a *pin key* — the one identifier an MCP server and a shell hook
    can both observe — never a FastMCP ``session_id`` no hook can see.
    """

    @staticmethod
    def _pin_keys(mapping: dict[str, str], *, live: bool = True) -> Any:
        """Patch pin resolution at the DEFINITION site, keyed by session id.

        ``live`` also seeds the pin store so every mapped key reads as a LIVE
        session: a foreign owner is honoured only while its pin is live (codex
        audit 2026-09-05 row 5), so the exemption tests must make that true
        explicitly rather than inherit it from an empty store.
        """

        def _resolve(ctx: object | None, explicit: str | None = None) -> str:
            session_id = getattr(ctx, "session_id", "")
            return mapping.get(str(session_id), str(session_id))

        def _entry(pin_key: str) -> dict[str, Any] | None:
            if not live or pin_key not in mapping.values():
                return None
            return {"pid": os.getpid(), "last_heartbeat_ts": _iso_now()}

        return _Patches(
            patch("trw_mcp.state._paths.resolve_pin_key", _resolve),
            patch("trw_mcp.state._pin_store.get_pin_entry", _entry),
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_only_the_owning_session_is_armed_by_an_owned_marker(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        trw_dir = tmp_path / ".trw"
        owner = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        stranger = FakeContext(request_context=FakeRequestContext(session_id="session-b"))
        executed: list[str] = []

        async def call_next(ctx: Any) -> Any:
            executed.append(ctx.message.name)
            return FakeToolResult(content=[TextContent(type="text", text="tool ok")])

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            self._pin_keys({"session-a": "pin-a", "session-b": "pin-b"}),
        ):
            # BOTH sessions are known before the rising edge, so generation
            # scoping (PRD-CORE-233) gates both. Anything that exempts the
            # stranger below is ownership and nothing else.
            for pre in (owner, stranger):
                await middleware.on_call_tool(
                    FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=pre),
                    call_next,
                )  # type: ignore[arg-type]
            executed.clear()
            _seed_compaction_marker(tmp_path, owner_pin_key="pin-a")

            owner_out = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=owner),
                call_next,
            )  # type: ignore[arg-type]
            stranger_out = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=stranger),
                call_next,
            )  # type: ignore[arg-type]

        assert owner_out.structured_content is not None
        assert owner_out.structured_content["error"] == "post_compaction_recovery_required"
        assert stranger_out.structured_content is None, "a session that did not compact owes nothing"
        assert executed == ["trw_recall"], "only the non-owner's call reaches the tool"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_non_owner_session_start_leaves_the_marker_in_place(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """An unrelated conversation must not destroy the owner's recovery obligation."""
        trw_dir = tmp_path / ".trw"
        marker = trw_dir / "context" / "pre_compact_state.json"
        owner = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        stranger = FakeContext(request_context=FakeRequestContext(session_id="session-b"))

        async def call_next(ctx: Any) -> Any:
            if ctx.message.name == "trw_session_start":
                return FakeToolResult(
                    content=[TextContent(type="text", text='{"success": true}')],
                    structured_content={"success": True},
                )
            return FakeToolResult(content=[TextContent(type="text", text="tool ok")])

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            self._pin_keys({"session-a": "pin-a", "session-b": "pin-b"}),
        ):
            for pre in (owner, stranger):
                await middleware.on_call_tool(
                    FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=pre),
                    call_next,
                )  # type: ignore[arg-type]
            _seed_compaction_marker(tmp_path, owner_pin_key="pin-a")

            await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=owner),
                call_next,
            )  # type: ignore[arg-type]
            await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_session_start"), fastmcp_context=stranger),
                call_next,
            )  # type: ignore[arg-type]
            assert marker.exists(), "a non-owner's session_start must not unlink the owner's marker"

            still_blocked = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=owner),
                call_next,
            )  # type: ignore[arg-type]

            assert still_blocked.structured_content is not None
            assert still_blocked.structured_content["error"] == "post_compaction_recovery_required"

            # ...and the owner's own start does clear it.
            await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_session_start"), fastmcp_context=owner),
                call_next,
            )  # type: ignore[arg-type]
        assert not marker.exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_an_unresolvable_owner_still_arms_and_is_never_deleted(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """Review P1-2: ``unknown`` ownership fails in opposite directions at the two sites.

        When resolving the caller's pin key raises, the arm site must keep blanket
        arming (not knowing is no licence to skip recovery) and the clear site
        must NOT unlink — deleting is the irreversible act, so an owner we could
        not resolve is treated as somebody else's obligation.
        """
        trw_dir = tmp_path / ".trw"
        marker = trw_dir / "context" / "pre_compact_state.json"
        owner = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        stranger = FakeContext(request_context=FakeRequestContext(session_id="session-b"))

        async def call_next(ctx: Any) -> Any:
            if ctx.message.name == "trw_session_start":
                return FakeToolResult(
                    content=[TextContent(type="text", text='{"success": true}')],
                    structured_content={"success": True},
                )
            return FakeToolResult(content=[TextContent(type="text", text="tool ok")])

        def _raising_pin_key(_ctx: object | None = None) -> str:
            raise RuntimeError("pin store unreadable")

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state._paths.resolve_pin_key", _raising_pin_key),
        ):
            for pre in (owner, stranger):
                await middleware.on_call_tool(
                    FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=pre),
                    call_next,
                )  # type: ignore[arg-type]
            _seed_compaction_marker(tmp_path, owner_pin_key="pin-a")

            blocked = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=stranger),
                call_next,
            )  # type: ignore[arg-type]
            assert blocked.structured_content is not None
            assert blocked.structured_content["error"] == "post_compaction_recovery_required", (
                "an unresolvable owner must keep blanket arming"
            )

            await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_session_start"), fastmcp_context=stranger),
                call_next,
            )  # type: ignore[arg-type]
            assert marker.exists(), "an unresolvable owner must never be deleted by a session_start"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_foreign_owner_whose_pin_is_not_live_is_an_orphan_and_still_arms(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """Codex audit row 5: pin keys are not reconnect-stable for every client.

        After an MCP restart the same logical client can resolve a fresh key, so
        the marker it wrote minutes earlier would read as ``foreign`` and exempt
        it from its own recovery. A foreign owner is honoured only while its pin
        is LIVE; an absent pin is an orphan and arms the generation like an
        ownerless marker — and a successful start may then clear it.
        """
        trw_dir = tmp_path / ".trw"
        marker = trw_dir / "context" / "pre_compact_state.json"
        reconnected = FakeContext(request_context=FakeRequestContext(session_id="session-new"))

        async def call_next(ctx: Any) -> Any:
            if ctx.message.name == "trw_session_start":
                return FakeToolResult(
                    content=[TextContent(type="text", text='{"success": true}')],
                    structured_content={"success": True},
                )
            return FakeToolResult(content=[TextContent(type="text", text="tool ok")])

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            # The old key "pin-old" is nobody's live pin: the store knows only the new key.
            self._pin_keys({"session-new": "pin-new"}),
        ):
            await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=reconnected),
                call_next,
            )  # type: ignore[arg-type]
            _seed_compaction_marker(tmp_path, owner_pin_key="pin-old")

            blocked = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_deliver"), fastmcp_context=reconnected),
                call_next,
            )  # type: ignore[arg-type]
            assert blocked.structured_content is not None
            assert blocked.structured_content["error"] == "post_compaction_recovery_required", (
                "an orphaned owner must not exempt the reconnected session from recovery"
            )

            await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_session_start"), fastmcp_context=reconnected),
                call_next,
            )  # type: ignore[arg-type]
        assert not marker.exists(), "a successful start clears an orphaned marker like an ownerless one"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_marker_with_no_owner_arms_the_whole_generation_as_it_does_at_head(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """RISK-006, deliberate: unowned means blanket, which is the fail-safe answer.

        Every marker the bundled PreCompact hook writes is ownerless, and that
        hook is the ordinary Claude Code compaction path — so treating an
        unowned marker as owned-by-nobody would silently disarm the gate exactly
        where it matters most.
        """
        trw_dir = tmp_path / ".trw"
        session_a = FakeContext(request_context=FakeRequestContext(session_id="session-a"))
        session_b = FakeContext(request_context=FakeRequestContext(session_id="session-b"))

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text="tool ok")])

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            self._pin_keys({"session-a": "pin-a", "session-b": "pin-b"}),
        ):
            # Identical setup to the owned case: both sessions pre-date the edge.
            for pre in (session_a, session_b):
                await middleware.on_call_tool(
                    FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=pre),
                    call_next,
                )  # type: ignore[arg-type]
            _seed_compaction_marker(tmp_path)

            first = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_a),
                call_next,
            )  # type: ignore[arg-type]
            second = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_b),
                call_next,
            )  # type: ignore[arg-type]

        # Only the owner field differs from the case above, and BOTH are gated:
        # the ownerless marker keeps HEAD's blanket behaviour exactly.
        for out in (first, second):
            assert out.structured_content is not None
            assert out.structured_content["error"] == "post_compaction_recovery_required"
