"""Shared test factories for trw-mcp tests.

Provides reusable factory functions for creating test data, reducing
duplication across test files. Import these instead of defining local
helpers in each test module.

Usage:
    from tests._factories import make_entry_data, write_entry, make_run_dir
    from tests._factories import make_run_dir_with_structure
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


def make_run_dir_with_structure(
    base: Path,
    *,
    task: str = "test-task",
    framework: str = "v24.0_TRW",
    run_id: str = "20260101T000000Z-test1234",
    writer: FileStateWriter | None = None,
    with_events: bool = False,
    with_scratch_orchestrator: bool = False,
) -> Path:
    """Create a run directory with full subdirectory structure.

    This is the shared factory that replaces the local ``_make_run_dir``
    helpers in test_orchestration_branches, test_validation_branches,
    and test_validation_gates.

    Args:
        base: Root path under which the run directory is created.
        task: Task name written to run.yaml.
        framework: Framework version written to run.yaml.
        run_id: Run identifier used for dir name and run.yaml.
        writer: Optional FileStateWriter; one is created if omitted.
        with_events: If True, write an initial ``run_init`` event to events.jsonl.
        with_scratch_orchestrator: If True, create ``scratch/_orchestrator/`` dir.

    Returns:
        The run directory Path.
    """
    w = writer or FileStateWriter()
    run_dir = base / "runs" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "shards").mkdir(parents=True, exist_ok=True)
    (run_dir / "reports").mkdir()

    if with_scratch_orchestrator:
        (run_dir / "scratch" / "_orchestrator").mkdir(parents=True)
    else:
        (run_dir / "scratch").mkdir(exist_ok=True)

    w.write_yaml(meta / "run.yaml", {
        "run_id": run_id,
        "task": task,
        "framework": framework,
        "status": "active",
        "phase": "research",
        "confidence": "medium",
    })

    if with_events:
        w.append_jsonl(meta / "events.jsonl", {
            "ts": "2026-02-06T12:00:00Z",
            "event": "run_init",
            "task": task,
        })

    return run_dir
