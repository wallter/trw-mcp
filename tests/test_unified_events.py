"""Tests for unified_events — PRD-HPO-MEAS-001 FR-3 unified jsonl writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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

    def test_falls_back_when_run_dir_missing_meta(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run-no-meta"
        run_dir.mkdir()
        fallback = tmp_path / "fallback"
        fallback.mkdir()
        path = resolve_unified_events_path(run_dir=run_dir, fallback_dir=fallback)
        assert path is not None
        assert path.parent == fallback

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
