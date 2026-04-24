"""Tests for tools.query_tools — PRD-HPO-MEAS-001 FR-7 + FR-8."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from trw_mcp.tools.query_tools import query_events, surface_diff


def _make_run(
    trw_dir: Path, task: str, run_id: str, events: list[dict[str, object]]
) -> Path:
    run_dir = trw_dir / "runs" / task / run_id
    (run_dir / "meta").mkdir(parents=True)
    events_file = run_dir / "meta" / "events-2026-04-23.jsonl"
    with events_file.open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    return run_dir


def _make_snapshot(run_dir: Path, snapshot_id: str, artifacts: list[dict[str, object]]) -> None:
    payload = {
        "snapshot_id": snapshot_id,
        "trw_mcp_version": "0.0.0",
        "framework_version": "v24.6",
        "generated_at": "2026-04-23T12:00:00+00:00",
        "artifacts": artifacts,
    }
    (run_dir / "meta" / "run_surface_snapshot.yaml").write_text(yaml.safe_dump(payload))


class TestQueryEvents:
    def test_empty_runs_root_returns_zero(self, tmp_path: Path) -> None:
        (tmp_path / "runs").mkdir()
        out = query_events(session_id="s1", trw_dir=tmp_path)
        assert out["count"] == 0
        assert out["events"] == []

    def test_merges_across_runs(self, tmp_path: Path) -> None:
        _make_run(
            tmp_path, "taskA", "run1",
            [{"event_type": "ceremony", "session_id": "s1", "ts": "2026-04-23T10:00:00"}],
        )
        _make_run(
            tmp_path, "taskB", "run2",
            [{"event_type": "tool_call", "session_id": "s1", "ts": "2026-04-23T11:00:00"}],
        )
        out = query_events(session_id="s1", trw_dir=tmp_path)
        assert out["count"] == 2
        assert {e["event_type"] for e in out["events"]} == {"ceremony", "tool_call"}

    def test_session_id_filter(self, tmp_path: Path) -> None:
        _make_run(
            tmp_path, "taskA", "run1",
            [
                {"event_type": "ceremony", "session_id": "s1", "ts": "t1"},
                {"event_type": "ceremony", "session_id": "s2", "ts": "t2"},
            ],
        )
        out = query_events(session_id="s1", trw_dir=tmp_path)
        assert out["count"] == 1
        assert out["events"][0]["session_id"] == "s1"

    def test_event_type_filter(self, tmp_path: Path) -> None:
        _make_run(
            tmp_path, "t", "r",
            [
                {"event_type": "ceremony", "session_id": "s", "ts": "t1"},
                {"event_type": "tool_call", "session_id": "s", "ts": "t2"},
            ],
        )
        out = query_events(
            session_id=None,
            filters={"event_type": "tool_call"},
            trw_dir=tmp_path,
        )
        assert out["count"] == 1
        assert out["events"][0]["event_type"] == "tool_call"

    def test_malformed_jsonl_line_skipped(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "runs" / "t" / "r"
        (run_dir / "meta").mkdir(parents=True)
        events_file = run_dir / "meta" / "events-2026-04-23.jsonl"
        events_file.write_text(
            '{"event_type": "ok", "session_id": "s", "ts": "t1"}\n'
            '{INVALID JSON HERE\n'
            '{"event_type": "ok2", "session_id": "s", "ts": "t2"}\n'
        )
        out = query_events(session_id=None, trw_dir=tmp_path)
        assert out["count"] == 2  # malformed line skipped

    def test_chronological_sort(self, tmp_path: Path) -> None:
        _make_run(
            tmp_path, "t", "r",
            [
                {"event_type": "a", "session_id": "s", "ts": "2026-04-23T12:00:00"},
                {"event_type": "b", "session_id": "s", "ts": "2026-04-23T10:00:00"},
                {"event_type": "c", "session_id": "s", "ts": "2026-04-23T11:00:00"},
            ],
        )
        out = query_events(session_id="s", trw_dir=tmp_path)
        types = [e["event_type"] for e in out["events"]]
        assert types == ["b", "c", "a"]


class TestSurfaceDiff:
    def test_snapshot_not_found_returns_error(self, tmp_path: Path) -> None:
        (tmp_path / "runs").mkdir()
        out = surface_diff(snapshot_id_a="a", snapshot_id_b="b", trw_dir=tmp_path)
        assert out["error"] == "snapshot_not_found"
        assert out["a_found"] is False
        assert out["b_found"] is False

    def test_identical_snapshots_yield_empty_diff(self, tmp_path: Path) -> None:
        run_a = _make_run(tmp_path, "t", "r1", [])
        run_b = _make_run(tmp_path, "t", "r2", [])
        arts = [
            {"surface_id": "agents:a.md", "content_hash": "h1"},
            {"surface_id": "agents:b.md", "content_hash": "h2"},
        ]
        _make_snapshot(run_a, "snap_A", arts)
        _make_snapshot(run_b, "snap_B", arts)
        out = surface_diff(snapshot_id_a="snap_A", snapshot_id_b="snap_B", trw_dir=tmp_path)
        assert out["added"] == []
        assert out["removed"] == []
        assert out["changed"] == []

    def test_added_artifacts(self, tmp_path: Path) -> None:
        run_a = _make_run(tmp_path, "t", "r1", [])
        run_b = _make_run(tmp_path, "t", "r2", [])
        _make_snapshot(run_a, "snap_A", [{"surface_id": "agents:a.md", "content_hash": "h"}])
        _make_snapshot(
            run_b, "snap_B",
            [
                {"surface_id": "agents:a.md", "content_hash": "h"},
                {"surface_id": "agents:new.md", "content_hash": "h2"},
            ],
        )
        out = surface_diff(snapshot_id_a="snap_A", snapshot_id_b="snap_B", trw_dir=tmp_path)
        assert out["added"] == ["agents:new.md"]
        assert out["removed"] == []
        assert out["changed"] == []

    def test_removed_artifacts(self, tmp_path: Path) -> None:
        run_a = _make_run(tmp_path, "t", "r1", [])
        run_b = _make_run(tmp_path, "t", "r2", [])
        _make_snapshot(
            run_a, "snap_A",
            [
                {"surface_id": "agents:a.md", "content_hash": "h"},
                {"surface_id": "agents:gone.md", "content_hash": "h2"},
            ],
        )
        _make_snapshot(run_b, "snap_B", [{"surface_id": "agents:a.md", "content_hash": "h"}])
        out = surface_diff(snapshot_id_a="snap_A", snapshot_id_b="snap_B", trw_dir=tmp_path)
        assert out["added"] == []
        assert out["removed"] == ["agents:gone.md"]

    def test_changed_content_hash(self, tmp_path: Path) -> None:
        run_a = _make_run(tmp_path, "t", "r1", [])
        run_b = _make_run(tmp_path, "t", "r2", [])
        _make_snapshot(run_a, "snap_A", [{"surface_id": "agents:x.md", "content_hash": "old"}])
        _make_snapshot(run_b, "snap_B", [{"surface_id": "agents:x.md", "content_hash": "new"}])
        out = surface_diff(snapshot_id_a="snap_A", snapshot_id_b="snap_B", trw_dir=tmp_path)
        assert out["changed"] == ["agents:x.md"]
        assert out["added"] == []
        assert out["removed"] == []
