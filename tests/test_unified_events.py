"""Tests for unified_events — PRD-HPO-MEAS-001 FR-3 unified jsonl writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.telemetry.event_base import (
    CeremonyEvent,
    HPOSessionStartEvent,
    MCPSecurityEvent,
    ToolCallEvent,
)
from trw_mcp.telemetry.unified_events import (
    UnifiedEventWriter,
    emit,
    get_default_writer,
    resolve_unified_events_path,
)


class TestResolveUnifiedEventsPath:
    def test_prefers_run_dir_meta_when_present(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-42"
        (run_dir / "meta").mkdir(parents=True)
        path = resolve_unified_events_path(run_dir=run_dir)
        assert path is not None
        assert path.parent == run_dir / "meta"
        assert path.name.startswith("events-")
        assert path.name.endswith(".jsonl")

    def test_auto_creates_run_dir_meta_when_missing(self, tmp_path: Path) -> None:
        """Bug fix: a not-yet-created meta/ must be created and used (run dir is
        the intended destination) rather than silently falling through to the
        fallback dir or, with no fallback, dropping the event."""
        run_dir = tmp_path / "run-no-meta"
        run_dir.mkdir()
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        path = resolve_unified_events_path(run_dir=run_dir, fallback_dir=fallback)
        assert path is not None
        # Lands under the run dir's meta/, NOT the fallback.
        assert path.parent == run_dir / "meta"
        assert (run_dir / "meta").is_dir()

    def test_no_silent_drop_when_run_dir_missing_meta_and_no_fallback(self, tmp_path: Path) -> None:
        """With run_dir set, a missing meta/ and no fallback must NOT return None
        (which would silently drop the event)."""
        run_dir = tmp_path / "run-only"
        run_dir.mkdir()
        path = resolve_unified_events_path(run_dir=run_dir, fallback_dir=None)
        assert path is not None
        assert path.parent == run_dir / "meta"

    def test_returns_none_when_nothing_resolvable(self) -> None:
        assert resolve_unified_events_path(run_dir=None) is None

    def test_filename_is_per_utc_date(self) -> None:
        fixed = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        p = resolve_unified_events_path(run_dir=None, fallback_dir=Path("/tmp"), now=fixed)
        assert p is not None
        assert p.name == "events-2026-04-23.jsonl"


class TestUnifiedEventWriter:
    def test_writes_jsonl_record(self, tmp_path: Path) -> None:
        writer = UnifiedEventWriter()
        path = tmp_path / "events.jsonl"
        event = CeremonyEvent(
            session_id="s1",
            run_id="r1",
            surface_snapshot_id="snap_a",
            payload={"phase": "IMPLEMENT"},
        )
        assert writer.write(event, path) is True
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event_type"] == "ceremony"
        assert record["session_id"] == "s1"
        assert record["run_id"] == "r1"
        assert record["surface_snapshot_id"] == "snap_a"
        assert record["payload"]["phase"] == "IMPLEMENT"

    def test_appends_multiple_events(self, tmp_path: Path) -> None:
        writer = UnifiedEventWriter()
        path = tmp_path / "events.jsonl"
        for i in range(3):
            writer.write(
                CeremonyEvent(session_id="s1", payload={"idx": i}),
                path,
            )
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3
        indices = [json.loads(ln)["payload"]["idx"] for ln in lines]
        assert indices == [0, 1, 2]

    def test_preserves_event_id_uniqueness(self, tmp_path: Path) -> None:
        writer = UnifiedEventWriter()
        path = tmp_path / "events.jsonl"
        e1 = HPOSessionStartEvent(session_id="s")
        e2 = HPOSessionStartEvent(session_id="s")
        writer.write(e1, path)
        writer.write(e2, path)
        ids = [json.loads(ln)["event_id"] for ln in path.read_text().strip().splitlines()]
        assert ids[0] != ids[1]


class TestEmitConvenience:
    def test_emit_writes_to_run_dir_when_resolvable(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-42"
        (run_dir / "meta").mkdir(parents=True)
        event = HPOSessionStartEvent(
            session_id="s1",
            run_id="run-42",
            surface_snapshot_id="snap_x",
        )
        assert emit(event, run_dir=run_dir) is True
        events_files = list((run_dir / "meta").glob("events-*.jsonl"))
        assert len(events_files) == 1

    def test_emit_returns_false_when_no_path(self) -> None:
        event = ToolCallEvent(session_id="s1")
        assert emit(event, run_dir=None, fallback_dir=None) is False

    def test_emit_projects_mcp_security_events_to_legacy_tool_call_surface(self, tmp_path: Path) -> None:
        event = MCPSecurityEvent(
            session_id="s1",
            payload={"decision": "shadow_anomaly", "tool": "read_file", "server": "filesystem"},
        )

        assert emit(event, run_dir=None, fallback_dir=tmp_path) is True

        projection = tmp_path / "tool_call_events.jsonl"
        assert projection.exists()
        rows = [json.loads(line) for line in projection.read_text().splitlines() if line]
        assert len(rows) == 1
        assert rows[0]["event_type"] == "mcp_security"
        assert rows[0]["payload"]["decision"] == "shadow_anomaly"


class TestDefaultWriter:
    def test_returns_singleton(self) -> None:
        a = get_default_writer()
        b = get_default_writer()
        assert a is b


def test_real_writer_wrapped_storage_failure_returns_false(
    tmp_path: Path, captured_structlog: list[dict[str, object]]
) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateWriter

    blocked = tmp_path / "not-a-directory"
    blocked.write_text("preserve")
    destination = blocked / "events.jsonl"
    # Prove the actual adapter boundary, not a mock raising raw OSError.
    with pytest.raises(StateError) as failure:
        FileStateWriter().append_jsonl(destination, {"event": "synthetic"})
    assert isinstance(failure.value.__cause__, OSError)
    event = CeremonyEvent(session_id="synthetic")
    assert UnifiedEventWriter().write(event, destination) is False
    assert blocked.read_text() == "preserve"
    assert not destination.exists()
    assert any(row.get("event") == "unified_event_write_failed" for row in captured_structlog)


def test_projection_failure_preserves_successful_primary(
    tmp_path: Path, captured_structlog: list[dict[str, object]]
) -> None:
    projection = tmp_path / "tool_call_events.jsonl"
    projection.mkdir()
    event = MCPSecurityEvent(session_id="synthetic", payload={"decision": "deny"})
    assert emit(event, run_dir=None, fallback_dir=tmp_path) is True
    files = list(tmp_path.glob("events-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["event_id"] == event.event_id
    assert rows[0]["payload"]["decision"] == "deny"
    assert projection.is_dir()
    assert list(projection.iterdir()) == []
    assert any(row.get("event") == "unified_projection_write_failed" for row in captured_structlog)


@pytest.mark.asyncio
async def test_security_listing_keeps_denial_when_real_telemetry_storage_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, object]]
) -> None:
    from fastmcp import FastMCP

    from tests.test_mcp_security_middleware import _make_middleware

    monkeypatch.setattr("trw_mcp.middleware.mcp_security.resolve_active_phase", lambda **_kwargs: "IMPLEMENT")
    middleware = _make_middleware(tmp_path)
    blocked = tmp_path / "blocked-context"
    blocked.write_text("preserve")
    middleware._fallback_dir = blocked
    server = FastMCP("storage-failure-security-test")

    @server.tool()
    def trw_recall() -> str:
        return "unused"

    @server.tool()
    def exec_shell() -> str:
        return "unused"

    assert {tool.name for tool in await server.list_tools()} == {"trw_recall", "exec_shell"}
    server.add_middleware(middleware)
    listed = await server.list_tools()
    assert {tool.name for tool in listed} == {"trw_recall"}
    assert blocked.read_text() == "preserve"
    failures = [row for row in captured_structlog if row.get("event") == "unified_event_write_failed"]
    assert failures  # Storage failure is witnessed independently of emission cardinality.
    assert all(Path(str(row["path"])).parent == blocked for row in failures)
