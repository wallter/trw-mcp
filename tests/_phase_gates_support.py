from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state.persistence import FileStateWriter


def _make_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with run.yaml present."""
    run_dir = tmp_path / "runs" / "20260101T000000Z-pg1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "scratch" / "_orchestrator").mkdir(parents=True)
    (run_dir / "shards").mkdir()
    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260101T000000Z-pg1234",
            "task": "phase-gates-test",
            "framework": "v24.0_TRW",
            "status": "active",
            "phase": "research",
            "confidence": "medium",
        },
    )
    return run_dir


def _write_events(meta_path: Path, events: list[dict]) -> None:
    """Write events to events.jsonl."""
    meta_path.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event) + "\n" for event in events]
    (meta_path / "events.jsonl").write_text("".join(lines), encoding="utf-8")
