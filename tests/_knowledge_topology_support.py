from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.models.config import TRWConfig


def _make_entry(
    entry_id: str = "L-test001",
    content: str = "Test summary",
    detail: str = "Test detail",
    tags: list[str] | None = None,
    importance: float = 0.5,
    status: MemoryStatus = MemoryStatus.ACTIVE,
) -> MemoryEntry:
    """Create a MemoryEntry for testing."""
    now = datetime.now(timezone.utc)
    return MemoryEntry(
        id=entry_id,
        content=content,
        detail=detail,
        tags=tags or [],
        evidence=[],
        importance=importance,
        status=status,
        namespace="default",
        created_at=now,
        updated_at=now,
        merged_from=[],
        consolidated_from=[],
        metadata={},
    )


def _make_config(tmp_path: Path, **overrides: object) -> TRWConfig:
    """Create a TRWConfig with temp dir and optional overrides."""
    kwargs: dict[str, object] = {
        "trw_dir": str(tmp_path / ".trw"),
        "knowledge_sync_threshold": 50,
        "knowledge_jaccard_threshold": 0.3,
        "knowledge_min_cluster_size": 3,
        "knowledge_output_dir": "knowledge",
    }
    kwargs.update(overrides)
    return TRWConfig(**kwargs)  # type: ignore[arg-type]
