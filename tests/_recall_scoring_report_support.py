"""Shared helpers for split recall/scoring/report coverage tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.persistence import FileStateWriter


def make_recall_tracking_log(
    tmp_path: Path,
    writer: FileStateWriter,
    records: list[dict[str, object]],
) -> Path:
    """Create ``.trw/logs/recall_tracking.jsonl`` with the provided records."""
    trw_dir = tmp_path / ".trw"
    logs_dir = trw_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "recall_tracking.jsonl"
    for record in records:
        writer.append_jsonl(log_path, record)
    return trw_dir


def patch_scoring_runs_root(runs_root: str = "tasks"):
    """Patch scoring config to use a temp runs root for session correlation tests."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()
    object.__setattr__(cfg, "runs_root", runs_root)
    object.__setattr__(cfg, "task_root", runs_root)
    return patch("trw_mcp.scoring._correlation.get_config", return_value=cfg)
