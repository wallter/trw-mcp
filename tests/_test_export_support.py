"""Shared helpers for export test splits."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


def _make_entry(
    entries_dir: Path,
    *,
    entry_id: str = "",
    summary: str = "Test learning",
    impact: float = 0.8,
    status: str = "active",
    tags: list[str] | None = None,
    source_type: str = "agent",
) -> None:
    """Write a YAML learning entry file."""
    import uuid

    if not entry_id:
        entry_id = f"L-{uuid.uuid4().hex[:8]}"
    slug = summary.lower().replace(" ", "-")[:40]
    filename = f"2026-02-21-{slug}.yaml"
    _writer.write_yaml(
        entries_dir / filename,
        {
            "id": entry_id,
            "summary": summary,
            "detail": f"Detail for: {summary}",
            "impact": impact,
            "status": status,
            "tags": tags or ["test"],
            "source_type": source_type,
            "created": "2026-02-21T00:00:00Z",
            "updated": "2026-02-21T00:00:00Z",
            "q_value": 0.5,
            "access_count": 1,
        },
    )


def _setup_project(tmp_path: Path) -> Path:
    """Create minimal .trw structure for export tests."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    return tmp_path
