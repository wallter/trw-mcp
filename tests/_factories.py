"""Shared test factories for trw-mcp tests.

Provides reusable factory functions for creating test data, reducing
duplication across test files. Import these instead of defining local
helpers in each test module.

Usage:
    from tests._factories import make_entry_data, write_entry, make_run_dir
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trw_mcp.state.persistence import FileStateReader, FileStateWriter


def make_entry_data(
    entry_id: str = "L-test001",
    summary: str = "Test summary",
    detail: str = "Test detail",
    *,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    impact: float = 0.5,
    status: str = "active",
    recurrence: int = 1,
    created: str = "2026-01-01",
    updated: str = "2026-01-01",
    merged_from: list[str] | None = None,
    **extra: Any,
) -> dict[str, object]:
    """Create a learning entry data dict with sensible defaults.

    All fields can be overridden. Extra keyword arguments are merged in.
    """
    data: dict[str, object] = {
        "id": entry_id,
        "summary": summary,
        "detail": detail,
        "tags": tags if tags is not None else ["test"],
        "evidence": evidence if evidence is not None else [],
        "impact": impact,
        "status": status,
        "recurrence": recurrence,
        "created": created,
        "updated": updated,
        "merged_from": merged_from if merged_from is not None else [],
    }
    data.update(extra)
    return data


def write_entry(
    entries_dir: Path,
    writer: FileStateWriter,
    entry_id: str = "L-test001",
    summary: str = "Test summary",
    detail: str = "Test detail",
    **kwargs: Any,
) -> Path:
    """Write a learning entry YAML file and return its path.

    Accepts all make_entry_data kwargs for customization.
    """
    data = make_entry_data(entry_id, summary, detail, **kwargs)
    path = entries_dir / f"{entry_id}.yaml"
    writer.write_yaml(path, data)
    return path


def make_merge_scenario(
    tmp_path: Path,
    *,
    existing_id: str = "L-merge01",
    existing_tags: list[str] | None = None,
    existing_evidence: list[str] | None = None,
    existing_impact: float = 0.5,
    existing_detail: str = "existing detail",
    existing_recurrence: int = 1,
    existing_merged_from: list[str] | None = None,
    new_id: str = "L-new01",
    new_tags: list[str] | None = None,
    new_evidence: list[str] | None = None,
    new_impact: float = 0.7,
    new_detail: str = "longer new detail with more info",
    new_merged_from: list[str] | None = None,
) -> tuple[Path, dict[str, object], FileStateReader, FileStateWriter]:
    """Set up a merge scenario with existing entry on disk + new entry data.

    Returns (existing_path, new_data, reader, writer) ready for merge_entries().
    """
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir(exist_ok=True)
    reader = FileStateReader()
    writer = FileStateWriter()

    existing_path = write_entry(
        entries_dir, writer, existing_id,
        summary="summary", detail=existing_detail,
        tags=existing_tags or [],
        evidence=existing_evidence or [],
        impact=existing_impact,
        recurrence=existing_recurrence,
        merged_from=existing_merged_from or [],
    )

    new_data = make_entry_data(
        new_id, "summary", new_detail,
        tags=new_tags or [],
        evidence=new_evidence or [],
        impact=new_impact,
        merged_from=new_merged_from or [],
    )

    return existing_path, new_data, reader, writer


def make_run_dir(
    tmp_path: Path,
    *,
    phase: str = "implement",
    status: str = "active",
    task_name: str = "test-task",
) -> Path:
    """Create a minimal run directory with run.yaml and events.jsonl.

    Returns the run_path.
    """
    import json

    run_path = tmp_path / "runs" / "test-run"
    meta = run_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)

    writer = FileStateWriter()
    writer.write_yaml(meta / "run.yaml", {
        "task_name": task_name,
        "phase": phase,
        "status": status,
        "run_id": "test-run-001",
        "created": "2026-01-01T00:00:00Z",
    })

    events_path = meta / "events.jsonl"
    events_path.write_text(
        json.dumps({"event": "run_initialized", "timestamp": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )

    return run_path
