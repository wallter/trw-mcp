"""PRD-CORE-258 residual coverage: reason enumeration, hook-shaped markers, races.

The implementer's own test files (``test_pre_compact_marker.py``,
``test_ceremony_middleware_gate_marker_lifecycle.py``,
``test_ceremony_gate_inherited_session.py``, ``test_deliver_unpinned_build_gate.py``,
``test_ceremony_middleware_gate_session_isolation.py``,
``test_middleware_ceremony_runtime.py``) already exercise every FR/NFR through
the real middleware path once each. This module fills the boundary cases those
files leave open:

* the full ``marker_unreadable_reason`` enumeration (NFR02) at the
  middleware+log level, not just the one reason ``missing_timestamp`` the
  existing ``TestGateLogAndPayloadAgree`` case covers;
* a marker deleted BETWEEN the gate's presence check and the payload's read —
  the literal ``missing_file_race`` scenario FR02's description names, produced
  here by two real middleware calls across a real filesystem mutation rather
  than a mocked reason string;
* the bundled PreCompact hook's own marker shape (the ``ts`` key, the trailing
  ``Z``, the literal ``unknown`` on a failed ``date`` call, no ``owner_pin_key``
  field) reaching the real gate and payload, not just the typed reader in
  isolation;
* an FR07 write failure at the OTHER point of the atomic sequence (the temp
  file is never created) than the one ``test_pre_compact_marker.py`` covers
  (temp file created, promotion raises).

No source file is modified here. Every test drives ``CeremonyMiddleware.on_call_tool``
or ``write_pre_compact_marker`` directly — nothing patches the unit under test
itself, only the filesystem/time seam around it.
"""

from __future__ import annotations

import json
from datetime import datetime
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
    middleware,  # noqa: F401
    session_ctx,  # noqa: F401
)
from trw_mcp.middleware.ceremony import CeremonyMiddleware, reset_state


@pytest.fixture(autouse=True)
def _clean_ceremony_state() -> Any:
    """This module is not a conftest, so the support module's autouse reset
    does not reach it -- every other split gate-test file re-declares this for
    the same reason."""
    reset_state()
    yield
    reset_state()


async def _blocked_call(middleware_: CeremonyMiddleware, ctx: FakeContext) -> Any:
    """Fire one ``trw_recall`` call and assert it never reaches the tool."""

    async def call_next(_ctx: Any) -> Any:
        raise AssertionError("a blocked call must not reach the tool")

    mw_ctx = FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=ctx)
    return await middleware_.on_call_tool(mw_ctx, call_next)  # type: ignore[arg-type]


class TestMarkerUnreadableReasonEnumeration:
    """PRD-CORE-258-NFR02: the log-only reason names WHICH parse step failed.

    ``test_the_unreadable_reason_is_log_only`` in
    ``test_middleware_ceremony_runtime.py`` proves the mechanism (log carries
    it, payload never does) for exactly one reason, ``missing_timestamp``. The
    enumeration has five members in total (four from the marker module plus
    the payload builder's own ``read_raised``); this class is the one place
    that exercises all five through the real gate.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("body", "expected_reason"),
        [
            pytest.param("{not json at all", "invalid_json", id="invalid_json"),
            pytest.param("[1, 2, 3]", "invalid_json", id="non_dict_json"),
            pytest.param("{}", "missing_timestamp", id="missing_timestamp"),
            pytest.param('{"timestamp": "yesterday afternoon"}', "non_iso_timestamp", id="non_iso_timestamp"),
            pytest.param('{"timestamp": "unknown"}', "non_iso_timestamp", id="hook_date_failure_literal"),
        ],
    )
    async def test_each_unreadable_shape_logs_its_own_reason(
        self,
        middleware: CeremonyMiddleware,
        session_ctx: FakeContext,
        tmp_path: Path,
        body: str,
        expected_reason: str,
    ) -> None:
        trw_dir = _seed_compaction_marker(tmp_path, body=body)
        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            structlog.testing.capture_logs() as logs,
        ):
            out = await _blocked_call(middleware, session_ctx)

        assert out.structured_content is not None
        assert out.structured_content["marker_state"] == "unreadable"
        assert out.structured_content["compaction_marker_ts"] is None
        assert "marker_unreadable_reason" not in out.structured_content, (
            "the parse-step diagnostic must never widen the response contract (NFR02)"
        )

        blocked = [entry for entry in logs if entry.get("event") == "ceremony_gate_blocked"]
        assert blocked, f"the block must be observable, got {logs}"
        assert blocked[0]["marker_unreadable_reason"] == expected_reason

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_a_reader_that_raises_logs_read_raised_not_a_parse_reason(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """A raising reader performed no parse step; folding it into ``invalid_json``
        would assert a parse that never ran (Amendment 1, point 4)."""
        trw_dir = _seed_compaction_marker(tmp_path)
        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.state.pre_compact_marker.read_pre_compact_marker_detail",
                side_effect=RuntimeError("marker read exploded"),
            ),
            structlog.testing.capture_logs() as logs,
        ):
            out = await _blocked_call(middleware, session_ctx)

        assert out.structured_content is not None
        assert out.structured_content["marker_state"] == "unreadable"
        assert "marker_unreadable_reason" not in out.structured_content

        blocked = [entry for entry in logs if entry.get("event") == "ceremony_gate_blocked"]
        assert blocked and blocked[0]["marker_unreadable_reason"] == "read_raised"


class TestMissingFileRaceThroughRealMiddleware:
    """PRD-CORE-258-FR02: 'absent at read time because it was removed between
    the gate check and the payload build is reported as unreadable rather than
    as a third state.'

    The gate arms a session once and keeps it armed in memory regardless of
    whether the marker still exists on later calls (generation scoping does
    not re-check per call) -- so deleting the file after the FIRST block, on the
    SAME still-armed session, reproduces the exact race window the PRD
    describes without patching any classification logic.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_marker_deleted_after_arming_reports_missing_file_race(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        trw_dir = _seed_compaction_marker(tmp_path)
        marker_path = trw_dir / "context" / "pre_compact_state.json"

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            first = await _blocked_call(middleware, session_ctx)
            assert first.structured_content is not None
            assert first.structured_content["marker_state"] == "read", "sanity: the marker was readable first"

            marker_path.unlink()  # a race, not a clear: the session stays armed in memory
            assert not marker_path.exists()

            with structlog.testing.capture_logs() as logs:
                second = await _blocked_call(middleware, session_ctx)

        assert second.structured_content is not None
        assert second.structured_content["marker_state"] == "unreadable"
        assert second.structured_content["compaction_marker_ts"] is None
        assert second.structured_content["blocked_count"] == 2, "the same session, still counting"

        blocked = [entry for entry in logs if entry.get("event") == "ceremony_gate_blocked"]
        assert blocked and blocked[0]["marker_unreadable_reason"] == "missing_file_race"


class TestHookShapedMarkerThroughRealMiddleware:
    """PRD-CORE-258-FR04/FR10: the bundled PreCompact hook writes ``ts`` (not
    ``timestamp``), a trailing ``Z``, no ``owner_pin_key``, and the literal
    ``unknown`` when its ``date`` call fails. This is the ordinary Claude Code
    compaction path -- ``test_pre_compact_marker.py`` proves the typed reader
    handles this shape, but no test previously drove it through the real
    gate + payload + blanket-arming path a hook-written marker actually takes.
    """

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_hook_shaped_ts_key_arms_the_gate_and_reports_read(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        hook_body = json.dumps({"ts": "2026-09-04T21:49:47Z", "trigger": "PreCompact"})
        trw_dir = _seed_compaction_marker(tmp_path, body=hook_body)
        expected_instant = datetime.fromisoformat("2026-09-04T21:49:47Z").isoformat()

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            out = await _blocked_call(middleware, session_ctx)

        assert out.structured_content is not None
        assert out.structured_content["marker_state"] == "read"
        assert out.structured_content["compaction_marker_ts"] == expected_instant
        assert out.structured_content["error"] == "post_compaction_recovery_required"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_hook_date_failure_literal_unknown_is_rejected_not_parsed(
        self, middleware: CeremonyMiddleware, session_ctx: FakeContext, tmp_path: Path
    ) -> None:
        """The hook writes the literal string ``unknown`` when ``date`` fails --
        this must never be treated as a plausible instant."""
        hook_body = json.dumps({"ts": "unknown", "trigger": "PreCompact"})
        trw_dir = _seed_compaction_marker(tmp_path, body=hook_body)

        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            structlog.testing.capture_logs() as logs,
        ):
            out = await _blocked_call(middleware, session_ctx)

        assert out.structured_content is not None
        assert out.structured_content["marker_state"] == "unreadable"
        assert out.structured_content["compaction_marker_ts"] is None
        blocked = [entry for entry in logs if entry.get("event") == "ceremony_gate_blocked"]
        assert blocked and blocked[0]["marker_unreadable_reason"] == "non_iso_timestamp"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_hook_shaped_marker_with_no_owner_field_arms_the_whole_generation(
        self, middleware: CeremonyMiddleware, tmp_path: Path
    ) -> None:
        """RISK-006: a hook-written marker names no owner. Combined with the
        ``ts`` spelling (not ``timestamp``), this is the literal shape the
        bundled hook produces on the ordinary compaction path -- two sessions
        known before the rising edge must BOTH be gated, exactly as an
        unowned ``timestamp``-keyed marker gates both in
        ``test_a_marker_with_no_owner_arms_the_whole_generation_as_it_does_at_head``.
        """
        trw_dir = tmp_path / ".trw"
        session_a = FakeContext(request_context=FakeRequestContext(session_id="hook-session-a"))
        session_b = FakeContext(request_context=FakeRequestContext(session_id="hook-session-b"))

        async def call_next(_ctx: Any) -> Any:
            return FakeToolResult(content=[TextContent(type="text", text="tool ok")])

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            # Both sessions pre-date the rising edge, so generation scoping
            # gates both; anything that exempted one would be an owner check,
            # and this marker names none.
            for pre in (session_a, session_b):
                await middleware.on_call_tool(
                    FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=pre),
                    call_next,
                )  # type: ignore[arg-type]

            hook_body = json.dumps({"ts": "2026-09-04T21:49:47Z", "trigger": "PreCompact"})
            _seed_compaction_marker(tmp_path, body=hook_body)

            out_a = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_a),
                call_next,
            )  # type: ignore[arg-type]
            out_b = await middleware.on_call_tool(
                FakeMiddlewareContext(message=FakeMessage(name="trw_recall"), fastmcp_context=session_b),
                call_next,
            )  # type: ignore[arg-type]

        for out in (out_a, out_b):
            assert out.structured_content is not None
            assert out.structured_content["error"] == "post_compaction_recovery_required"
            assert out.structured_content["marker_state"] == "read"


class TestAtomicWriteFailsBeforeTheTempFileExists:
    """PRD-CORE-258-FR07: ``test_pre_compact_marker.py`` covers a raise AFTER the
    temp file is created (``os.replace`` explodes). This covers the OTHER
    failure point in the same atomic sequence -- the write into the temp file
    itself never completes -- so both halves of the try/finally are pinned.
    """

    @pytest.mark.unit
    def test_a_failed_temp_write_leaves_no_temp_file_and_the_previous_marker_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.state import pre_compact_marker as module

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True, exist_ok=True)
        path = module.pre_compact_marker_path(trw_dir)
        module.write_pre_compact_marker({"timestamp": "2026-09-04T21:49:47+00:00", "trigger": "first"}, trw_dir)

        def _explode(self: Path, *_args: object, **_kwargs: object) -> int:
            raise OSError("disk full before the temp file could be written")

        monkeypatch.setattr(Path, "write_text", _explode)
        with pytest.raises(OSError, match="disk full"):
            module.write_pre_compact_marker({"timestamp": "2026-09-04T22:00:00+00:00", "trigger": "second"}, trw_dir)

        # write_text is patched process-wide for the assertion window below, so
        # read via read_text (unpatched) rather than any helper that writes.
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["trigger"] == "first", "the previous complete document must survive"
        assert list(path.parent.glob("*.tmp")) == [], "no temp file may exist when the write never completed"
