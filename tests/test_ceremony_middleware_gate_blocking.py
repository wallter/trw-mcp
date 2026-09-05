"""Blocking behavior tests for post-compaction ceremony gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mcp.types import TextContent

from tests._test_ceremony_middleware_gate_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeToolResult,
    _clean_state,  # noqa: F401  # autouse: resets middleware module state per test
    _seed_compaction_marker,
    _text,
    middleware,  # noqa: F401
    session_ctx,  # noqa: F401
)
from trw_mcp.middleware import ceremony as ceremony_module
from trw_mcp.middleware.ceremony import CeremonyMiddleware, is_session_active, reset_state


@pytest.fixture(autouse=True)
def _hermetic_trw_dir(tmp_path: Path) -> Any:
    """Keep the gate's marker read off the live repo's ``.trw`` (review P1-1).

    Since PRD-CORE-258-FR02 a blocked call reads the marker to build its payload,
    so a test that patches only ``_is_compaction_gate_required`` would otherwise
    do real disk I/O against shared repository state inside a ``unit`` test. Tests
    that seed their own marker re-patch the same seam with their own directory.
    """
    with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path):
        yield


class TestCompactionGate:
    """Tests for the post-compaction gate that blocks trw_* tools."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_blocks_a_decision_shaping_tool(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """A decision-shaping trw_* tool without session_start returns the error dict.

        Probes ``trw_recall`` rather than ``trw_checkpoint``. Checkpoint used to
        be this module's canonical gated probe, but it is now exempt
        (``COMPACTION_GATE_EXEMPT_TOOLS``) — so probing it here would have asserted
        the gate's scope using a tool outside that scope. ``trw_recall`` is the
        right probe on the merits: it SHAPES what the agent does next, which is
        precisely what a stale-context caller must not do.
        """
        tool_result = FakeToolResult(content=[TextContent(type="text", text="recall ok")])
        call_count = 0
        _seed_compaction_marker(tmp_path)

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_recall"),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 0, "call_next should not be invoked when gate blocks"
        assert len(out.content) == 1
        first = out.content[0]
        assert isinstance(first, TextContent)
        assert "trw_session_start()" in first.text
        assert out.structured_content is not None
        assert out.structured_content["error"] == "post_compaction_recovery_required"
        assert out.structured_content["tool_attempted"] == "trw_recall"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_reads_real_marker_file(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The hard gate is driven by the pre_compact_state.json marker on disk."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="recall ok")])
        trw_dir = _seed_compaction_marker(tmp_path)
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_recall"),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 0
        assert out.structured_content is not None
        assert out.structured_content["error"] == "post_compaction_recovery_required"
        assert (trw_dir / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_compaction_gate_allows_session_start(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """trw_session_start always passes through even without prior session."""
        tool_result = FakeToolResult(
            content=[TextContent(type="text", text='{"status":"success","detail":"session started"}')]
        )
        trw_dir = _seed_compaction_marker(tmp_path)

        async def call_next(_ctx: Any) -> Any:
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_session_start"),
            fastmcp_context=session_ctx,
        )
        with (
            patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True),
            patch(
                "trw_mcp.middleware.ceremony._clear_compaction_gate_safe",
                side_effect=lambda *_ctx: (trw_dir / "context" / "pre_compact_state.json").unlink(),
            ),
        ):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert len(out.content) == 1
        assert _text(out.content[0]) == '{"status":"success","detail":"session started"}'
        assert is_session_active("test-session-gate")
        assert not (trw_dir / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_trw_tools_are_not_blocked_without_compaction(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Without a compaction marker, trw_* tools still execute."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="checkpoint ok")])
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_checkpoint"),
            fastmcp_context=session_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 1
        texts = [block.text for block in out.content if isinstance(block, TextContent)]
        assert "checkpoint ok" in texts

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_does_not_affect_non_trw_tools(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Non-trw_* tools (e.g. Read, Bash) should never be blocked by the gate."""
        tool_result = FakeToolResult(content=[TextContent(type="text", text="file contents")])
        call_count = 0

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="Read"),
            fastmcp_context=session_ctx,
        )
        out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 1, "Non-trw tool should still execute"
        texts = [b.text for b in out.content if isinstance(b, TextContent)]
        assert "file contents" in texts

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_blocks_multiple_trw_tools(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """Every trw_* tool is in the gate's scope — but the gate is BOUNDED.

        Originally this asserted all nine tools stay blocked forever. Audit C-5
        showed that unbounded form is a deadlock: under stdio, ``ctx.session_id``
        is shared by a parent agent and every sub-agent it spawns, and ten of
        eleven bundled agents have no ``trw_session_start`` to clear the gate
        with — so "blocked until session_start" meant "blocked for the life of
        the server" for those callers.

        The gate now hard-blocks the first ``_COMPACTION_GATE_MAX_BLOCKS`` calls
        (the guarantee: a capable agent is stopped and told exactly what to do)
        and then degrades to advisory, since repeated blocks with no intervening
        session_start are evidence the caller *cannot* satisfy it. This test
        pins both halves: no tool escapes scope, and no caller is trapped.
        """
        _seed_compaction_marker(tmp_path)
        # Evidence-recording tools are deliberately absent: they are exempt from
        # the gate entirely (COMPACTION_GATE_EXEMPT_TOOLS), and their exemption is
        # asserted by TestEvidenceRecordingToolsAreExempt below. Listing them
        # here would have made this test claim gate scope it no longer has.
        blocked_tools = [
            "trw_deliver",
            "trw_status",
            "trw_prd_create",
            "trw_prd_validate",
            "trw_init",
            "trw_recall",
        ]
        assert not (set(blocked_tools) & ceremony_module.COMPACTION_GATE_EXEMPT_TOOLS), (
            "this test's probes must all be inside the gate's scope"
        )

        for tool_name in blocked_tools:
            call_count = 0
            tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

            async def call_next(_ctx: Any) -> Any:
                nonlocal call_count
                call_count += 1
                return tool_result

            ctx = FakeMiddlewareContext(
                message=FakeMessage(name=tool_name),
                fastmcp_context=session_ctx,
            )
            with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
                out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

            attempt = blocked_tools.index(tool_name) + 1
            if attempt <= ceremony_module._COMPACTION_GATE_MAX_BLOCKS:
                assert call_count == 0, f"{tool_name} (attempt {attempt}) should be hard-blocked"
                assert out.structured_content is not None
                assert out.structured_content["error"] == "post_compaction_recovery_required", (
                    f"{tool_name} should return post_compaction_recovery_required error"
                )
            else:
                # Past the bound: the tool runs, but the recovery instruction is
                # still prepended — the nudge is never removed, only de-fanged.
                assert call_count == 1, f"{tool_name} (attempt {attempt}) must not be trapped past the bound"
                assert out.structured_content is None
                assert "trw_session_start" in getattr(out.content[0], "text", ""), (
                    "the recovery instruction must survive degradation"
                )

        # The obligation itself outlives the degradation: the on-disk marker is
        # deliberately left for a caller that CAN discharge it.
        assert (tmp_path / ".trw" / "context" / "pre_compact_state.json").exists()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_allows_ceremony_tools_without_session(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext
    ) -> None:
        """Only trw_session_start passes through before the gate is cleared."""
        ceremony_tools = ["trw_session_start"]

        for tool_name in ceremony_tools:
            reset_state()
            tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

            async def call_next(_ctx: Any) -> Any:
                return tool_result

            ctx = FakeMiddlewareContext(
                message=FakeMessage(name=tool_name),
                fastmcp_context=session_ctx,
            )
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

            assert _text(out.content[0]) == "ok", f"{tool_name} should pass through without blocking"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_error_response_structure(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The error response contains expected structured fields."""
        _seed_compaction_marker(tmp_path)
        tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name="trw_status"),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert out.structured_content is not None
        error_data = out.structured_content
        assert error_data["error"] == "post_compaction_recovery_required"
        assert "trw_session_start()" in error_data["message"]
        assert error_data["tool_attempted"] == "trw_status"


class TestEvidenceRecordingToolsAreExempt:
    """The gate must never block a tool whose only job is recording evidence.

    Operator-approved 2026-07-26, closing the decision audit C-5 deferred. The
    gate stops an agent ACTING on stale post-compaction context; these tools do
    not act, they record what already happened, and their content comes from the
    caller. Blocking them buys no context integrity and destroys evidence that
    cannot be reconstructed — measured: a delegated VALIDATE completed with no
    recorded ``trw_build_check`` and a checkpoint reporting ``recorded: false``.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.parametrize("tool_name", sorted(ceremony_module.COMPACTION_GATE_EXEMPT_TOOLS))
    async def test_evidence_tool_executes_while_the_gate_is_armed(
        self,
        middleware: CeremonyMiddleware,
        session_ctx: FakeContext,
        tmp_path: Path,
        tool_name: str,
    ) -> None:
        """The exempt tool reaches call_next on the FIRST call, with the gate armed."""
        _seed_compaction_marker(tmp_path)
        call_count = 0
        tool_result = FakeToolResult(content=[TextContent(type="text", text="recorded")])

        async def call_next(_ctx: Any) -> Any:
            nonlocal call_count
            call_count += 1
            return tool_result

        ctx = FakeMiddlewareContext(
            message=FakeMessage(name=tool_name),
            fastmcp_context=session_ctx,
        )
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert call_count == 1, f"{tool_name} must not be blocked — evidence loss is unrecoverable"
        assert (
            out.structured_content is None or out.structured_content.get("error") != "post_compaction_recovery_required"
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_exemption_does_not_consume_the_bounded_escape(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """An exempt call must not count toward the block budget of gated tools.

        If exempt calls incremented the counter, a delegate recording three
        pieces of evidence would silently exhaust the bound and de-fang the gate
        for the genuinely gated tools that follow.
        """
        _seed_compaction_marker(tmp_path)
        tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return tool_result

        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            for tool_name in sorted(ceremony_module.COMPACTION_GATE_EXEMPT_TOOLS):
                ctx = FakeMiddlewareContext(message=FakeMessage(name=tool_name), fastmcp_context=session_ctx)
                await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

            gated = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_ctx)
            out = await middleware.on_call_tool(gated, call_next)  # type: ignore[arg-type]

        assert out.structured_content is not None
        assert out.structured_content["error"] == "post_compaction_recovery_required", (
            "the first gated call after exempt calls must still be hard-blocked"
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_exemption_leaves_the_recovery_obligation_on_disk(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """Recording evidence never discharges the post-compaction obligation."""
        trw_dir = _seed_compaction_marker(tmp_path)
        tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return tool_result

        ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_build_check"), fastmcp_context=session_ctx)
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert (trw_dir / "context" / "pre_compact_state.json").exists()
        assert not is_session_active("test-session-gate")

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_block_message_names_a_remedy_a_delegate_can_perform(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The block text must be actionable by a caller lacking trw_session_start.

        Ten of eleven bundled agents hold no ``trw_session_start``. The original
        message named only that tool, so a compliant delegate read an impossible
        instruction and stopped — observed 2026-07-26 with blocks arriving in
        exact pairs against a bound of 2, one call short of the escape hatch
        built for it.
        """
        _seed_compaction_marker(tmp_path)
        tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            return tool_result

        ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_ctx)
        with patch("trw_mcp.middleware.ceremony._is_compaction_gate_required", return_value=True):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert out.structured_content is not None
        message = str(out.structured_content["message"])
        assert "trw_session_start()" in message, "the capable-caller remedy must survive"
        assert "retry" in message.lower(), "a delegate must be told retrying clears the gate"
        assert "dispatcher" in message.lower(), "recovery ownership must be named"


class TestTheGateNamesItsOwnCondition:
    """PRD-CORE-258-FR01: the error key describes the condition the gate tests.

    The gate fires on one thing — a pre-compaction marker on disk — and answered
    with a key naming session start — a check this branch has not performed
    since 2026-04-11. Every agent it stops has just lost its context, which is the
    moment it has the least material with which to check a claim, and the claim
    was false. Because the named remedy happens to work, the misdiagnosis was
    self-confirming.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_gate_error_names_post_compaction_recovery(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The key names post-compaction recovery, the message says 'compact', no alias."""
        trw_dir = _seed_compaction_marker(tmp_path)
        tool_result = FakeToolResult(content=[TextContent(type="text", text="ok")])

        async def call_next(_ctx: Any) -> Any:
            raise AssertionError("a blocked call must not reach the tool")

        ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_ctx)
        assert tool_result is not None
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            out = await middleware.on_call_tool(ctx, call_next)  # type: ignore[arg-type]

        assert out.structured_content is not None
        payload = out.structured_content
        assert payload["error"] == "post_compaction_recovery_required"
        assert "compact" in str(payload["message"]).lower()
        assert payload["remedy"] == "trw_session_start"
        # No alias key and no alias value anywhere in the payload (NFR04).
        rendered = json.dumps(payload)
        assert "session" + "_start_required" not in rendered
        assert "session" + "_start_required" not in _text(out.content[0])


class TestTheOldKeyStaysDeleted:
    """PRD-CORE-258-FR05 / OQ-03: a reintroduced alias must fail the suite.

    Lives in the package suite rather than in ``scripts/repo_hygiene.py``: the
    string is a package contract, the suite is what a contributor runs, and a
    hygiene-script home would also scan the historical documents this PRD
    deliberately preserves.
    """

    @pytest.mark.unit
    def test_the_old_error_key_is_absent_from_the_package(self) -> None:
        # Split so this guard never matches itself.
        needle = "session" + "_start_required"
        root = Path(__file__).resolve().parents[1]
        offenders: list[str] = []
        for base in ("src", "tests"):
            for path in sorted((root / base).rglob("*.py")):
                if needle in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(root)))
        assert offenders == [], f"the deleted error key reappeared in: {offenders}"
