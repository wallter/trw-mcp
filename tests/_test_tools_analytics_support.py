"""Shared support for split analytics tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import trw_mcp.state.analytics.report as analytics_mod
from trw_mcp.state.persistence import FileStateWriter


def _write_run(
    writer: FileStateWriter,
    base: Path,
    task: str,
    run_id: str,
    events: list[dict[str, object]] | None = None,
    run_yaml_content: dict[str, object] | None = None,
) -> Path:
    """Create a run directory with run.yaml and optional events.jsonl."""
    run_dir = base / ".trw" / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    yaml_data: dict[str, object] = run_yaml_content or {
        "run_id": run_id,
        "task": task,
        "status": "active",
        "phase": "implement",
    }
    writer.write_yaml(meta / "run.yaml", yaml_data)

    if events:
        events_path = meta / "events.jsonl"
        for evt in events:
            writer.append_jsonl(events_path, evt)

    return run_dir


@pytest.fixture
def writer() -> FileStateWriter:
    """Provide a FileStateWriter instance."""
    return FileStateWriter()


@pytest.fixture
def multi_run_project(
    tmp_path: Path,
    writer: FileStateWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Create three runs across two tasks under tmp_path/.trw/runs."""
    monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(analytics_mod._config, "runs_root", ".trw/runs")
    monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

    run_events_a1: list[dict[str, object]] = [
        {"ts": "2026-01-01T00:00:00Z", "event": "session_start"},
        {"ts": "2026-01-01T00:05:00Z", "event": "checkpoint"},
    ]
    run_events_a2: list[dict[str, object]] = [
        {"ts": "2026-01-02T00:00:00Z", "event": "session_start"},
        {"ts": "2026-01-02T00:01:00Z", "event": "reflection_complete"},
        {"ts": "2026-01-02T00:02:00Z", "event": "learn_recorded"},
        {"ts": "2026-01-02T00:03:00Z", "event": "build_check_complete", "tests_passed": "true"},
    ]
    run_events_b1: list[dict[str, object]] = [
        {"ts": "2026-01-03T00:00:00Z", "event": "session_start"},
    ]

    _write_run(writer, tmp_path, "task-a", "20260101T000000Z-aaaa1111", events=run_events_a1)
    _write_run(writer, tmp_path, "task-a", "20260102T000000Z-bbbb2222", events=run_events_a2)
    _write_run(writer, tmp_path, "task-b", "20260103T000000Z-cccc3333", events=run_events_b1)
    return tmp_path
