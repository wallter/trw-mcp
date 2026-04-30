from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager


def make_entry(
    entry_id: str = "test-entry-001",
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
        last_accessed_at=date.fromisoformat(last_accessed_at) if last_accessed_at else None,
        created=date.fromisoformat(created or today),
    )


def write_entry_yaml(
    entries_dir: Path,
    writer: FileStateWriter,
    entry_id: str,
    summary: str = "test summary",
    impact: float = 0.5,
    status: str = "active",
    last_accessed_at: str | None = None,
    created: str | None = None,
) -> Path:
    """Write a minimal learning entry YAML for testing."""
    today = datetime.now(tz=timezone.utc).date().isoformat()
    path = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(
        path,
        {
            "id": entry_id,
            "summary": summary,
            "detail": f"detail for {entry_id}",
            "tags": ["test"],
            "impact": impact,
            "status": status,
            "last_accessed_at": last_accessed_at,
            "created": created or today,
        },
    )
    return path


def make_tier_manager(tmp_path: Path, config: TRWConfig | None = None) -> TierManager:
    """Create a TierManager with an isolated .trw/ directory."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    return TierManager(
        trw_dir=trw_dir,
        reader=FileStateReader(),
        writer=FileStateWriter(),
        config=config,
    )


def days_ago(n: int) -> str:
    """Return ISO date string for N days ago."""
    return (datetime.now(tz=timezone.utc).date() - timedelta(days=n)).isoformat()
