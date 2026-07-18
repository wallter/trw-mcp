"""Shared helpers for split analytics report tests."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


def _make_run_id_hours_ago(hours_ago: float) -> str:
    """Build a run_id whose embedded timestamp is `hours_ago` hours in the past."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    ts = dt.strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-abcd1234"


def _create_run(
    runs_root: Path,
    task_name: str,
    run_id: str,
    status: str = "active",
    phase: str = "implement",
) -> Path:
    """Create a run directory with run.yaml at the expected path."""
    run_dir = runs_root / task_name / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    _writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": run_id,
            "task": task_name,
            "status": status,
            "phase": phase,
        },
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return run_dir


def _add_checkpoint(run_dir: Path, hours_ago: float) -> None:
    """Add a checkpoint entry to a run's checkpoints.jsonl."""
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    checkpoint = {"ts": ts, "message": "test checkpoint", "state": {}}
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    line = json.dumps(checkpoint) + "\n"
    with checkpoints_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _add_events(run_dir: Path, count: int) -> None:
    """Add event entries to a run's events.jsonl."""
    events_path = run_dir / "meta" / "events.jsonl"
    for i in range(count):
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": f"test_event_{i}",
        }
        line = json.dumps(event) + "\n"
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def _patch_config_and_root(tmp_path: Path, ttl_hours: int = 48) -> tuple[object, object, object]:
    """Return context managers patching project root and config for tests."""
    cfg = TRWConfig(run_stale_ttl_hours=ttl_hours)
    return (
        nullcontext(),
        patch("trw_mcp.state.analytics.report.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state.analytics.report.get_config", return_value=cfg),
    )
