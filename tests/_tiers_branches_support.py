from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state.persistence import FileStateWriter


def _make_entry(
    entry_id: str = "test-001",
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    last_accessed_at: str | None = None,
    created: str | None = None,
) -> LearningEntry:
    """Build a minimal LearningEntry for testing."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    return LearningEntry(
        id=entry_id,
        summary=summary,
        detail=f"detail for {entry_id}",
        tags=["test"],
        impact=impact,
        status=LearningStatus(status),
        source_type="agent",
        source_identity="test",
        created=date.fromisoformat(created or today),
        last_accessed_at=date.fromisoformat(last_accessed_at) if last_accessed_at else None,
    )


def _make_old_entry(entry_id: str = "old-001", days_ago: int = 60) -> LearningEntry:
    """Build a LearningEntry with an old last_accessed_at date."""
    old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=days_ago)).isoformat()
    return _make_entry(
        entry_id=entry_id,
        last_accessed_at=old_date,
        created=old_date,
        impact=0.1,
    )


def _setup_entries_dir(trw_dir: Path) -> Path:
    """Create standard learnings/entries dir structure."""
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    return entries_dir


def _write_yaml_entry(
    entries_dir: Path,
    entry_id: str,
    *,
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    last_accessed_at: str | None = None,
    q_observations: int = 0,
) -> Path:
    """Write a minimal YAML entry file to disk."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    data = {
        "id": entry_id,
        "summary": summary,
        "detail": f"detail for {entry_id}",
        "tags": ["test"],
        "impact": impact,
        "status": status,
        "source_type": "agent",
        "created": last_accessed_at or today,
        "last_accessed_at": last_accessed_at or today,
        "q_observations": q_observations,
    }
    writer = FileStateWriter()
    path = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(path, data)
    return path
