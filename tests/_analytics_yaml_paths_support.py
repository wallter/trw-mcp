"""Shared helpers for split analytics YAML path tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


def _setup_trw(tmp_path: Path) -> Path:
    """Create minimal .trw/ structure and return trw_dir."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(parents=True, exist_ok=True)
    return trw_dir


def _write_entry(
    entries_dir: Path,
    entry_id: str,
    *,
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    q_value: float = 0.0,
    q_observations: int = 0,
    access_count: int = 0,
    source_type: str = "agent",
    tags: list[str] | None = None,
    last_accessed_at: str | None = None,
) -> Path:
    """Write a YAML entry to disk."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    data: dict[str, object] = {
        "id": entry_id,
        "summary": summary,
        "detail": f"detail for {entry_id}",
        "tags": tags or ["test"],
        "impact": impact,
        "status": status,
        "source_type": source_type,
        "created": last_accessed_at or today,
        "last_accessed_at": last_accessed_at or today,
        "q_value": q_value,
        "q_observations": q_observations,
        "access_count": access_count,
    }
    path = entries_dir / f"{entry_id}.yaml"
    _writer.write_yaml(path, data)
    return path
