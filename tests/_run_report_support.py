from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateWriter


@pytest.fixture
def report_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a rich run directory with events, checkpoints, and build status."""
    run_dir = tmp_path / "docs" / "analytics-task" / "runs" / "20260219T100000Z-aaaa1111"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260219T100000Z-aaaa1111",
            "task": "analytics-task",
            "framework": "v24.0_TRW",
            "status": "complete",
            "phase": "deliver",
            "confidence": "high",
            "run_type": "implementation",
            "prd_scope": ["PRD-CORE-030"],
        },
    )

    events = [
        {"ts": "2026-02-19T10:00:00Z", "event": "run_init", "task": "analytics-task"},
        {"ts": "2026-02-19T10:01:00Z", "event": "phase_enter", "phase": "research"},
        {"ts": "2026-02-19T10:15:00Z", "event": "phase_enter", "phase": "plan"},
        {"ts": "2026-02-19T10:30:00Z", "event": "phase_enter", "phase": "implement"},
        {"ts": "2026-02-19T11:00:00Z", "event": "checkpoint", "message": "mid-impl"},
        {"ts": "2026-02-19T11:30:00Z", "event": "tests_passed"},
        {"ts": "2026-02-19T11:45:00Z", "event": "phase_revert", "from_phase": "implement", "to_phase": "plan"},
        {"ts": "2026-02-19T12:00:00Z", "event": "phase_enter", "phase": "implement"},
        {"ts": "2026-02-19T12:30:00Z", "event": "phase_enter", "phase": "validate"},
        {"ts": "2026-02-19T13:00:00Z", "event": "phase_enter", "phase": "deliver"},
    ]
    for evt in events:
        writer.append_jsonl(meta / "events.jsonl", evt)

    writer.append_jsonl(meta / "checkpoints.jsonl", {"ts": "2026-02-19T11:00:00Z", "message": "mid"})
    writer.append_jsonl(meta / "checkpoints.jsonl", {"ts": "2026-02-19T12:30:00Z", "message": "val"})

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    writer.write_yaml(
        trw_dir / "context" / "build-status.yaml",
        {
            "tests_passed": True,
            "mypy_clean": True,
            "coverage_pct": 92.5,
            "test_count": 45,
            "duration_secs": 12.3,
        },
    )

    return run_dir


@pytest.fixture
def minimal_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with only run.yaml."""
    run_dir = tmp_path / "docs" / "minimal" / "runs" / "20260219T080000Z-bbbb2222"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260219T080000Z-bbbb2222",
            "task": "minimal",
            "framework": "v24.0_TRW",
            "status": "active",
            "phase": "research",
            "confidence": "medium",
        },
    )

    return run_dir
