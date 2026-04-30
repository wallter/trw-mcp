from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.models.config import TRWConfig


def _entry(
    entry_id: str = "L-001",
    content: str = "Summary",
    detail: str = "",
    tags: list[str] | None = None,
    importance: float = 0.5,
    evidence: list[str] | None = None,
) -> MemoryEntry:
    now = datetime.now(timezone.utc)
    return MemoryEntry(
        id=entry_id,
        content=content,
        detail=detail,
        tags=tags or [],
        evidence=evidence or [],
        importance=importance,
        status=MemoryStatus.ACTIVE,
        namespace="default",
        created_at=now,
        updated_at=now,
        merged_from=[],
        consolidated_from=[],
        metadata={},
    )


def _make_config(tmp_path: Path, **overrides: object) -> TRWConfig:
    kwargs: dict[str, object] = {
        "trw_dir": str(tmp_path / ".trw"),
        "knowledge_sync_threshold": 5,
        "knowledge_jaccard_threshold": 0.3,
        "knowledge_min_cluster_size": 2,
        "knowledge_output_dir": "knowledge",
    }
    kwargs.update(overrides)
    return TRWConfig(**kwargs)  # type: ignore[arg-type]
