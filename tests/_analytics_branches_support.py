"""Shared fixtures and helpers for split analytics branch tests."""

from __future__ import annotations

from pathlib import Path

import pytest

import trw_mcp.state.analytics.report as analytics_mod
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw/ structure."""
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "reflections").mkdir()
    (d / "context").mkdir()
    return d


def _write_entry(
    entries_dir: Path,
    name: str,
    *,
    summary: str = "",
    impact: float = 0.5,
    status: str = "active",
    q_observations: int = 0,
    q_value: float = 0.5,
    access_count: int = 0,
    source_type: str = "agent",
    tags: list[str] | None = None,
    learning_id: str | None = None,
) -> None:
    """Write a learning entry YAML file.

    Summaries are always double-quoted to handle special chars like ':' safely.
    """
    lid = learning_id or f"L-{name}"
    if not summary:
        summary = f"Test learning {name}"
    escaped_summary = summary.replace('"', '\\"')
    tag_str = ", ".join(f'"{t}"' for t in (tags or []))
    (entries_dir / f"{name}.yaml").write_text(
        f'id: {lid}\nsummary: "{escaped_summary}"\ndetail: Detail\n'
        f"status: {status}\nimpact: {impact}\n"
        f"q_observations: {q_observations}\nq_value: {q_value}\n"
        f"access_count: {access_count}\nsource_type: {source_type}\n"
        f"source_identity: ''\ntags: [{tag_str}]\n"
        f"created: '2026-02-01'\n",
        encoding="utf-8",
    )


def _write_run(
    base: Path,
    task: str,
    run_id: str,
    events: list[dict[str, object]] | None = None,
    run_yaml_content: dict[str, object] | None = None,
) -> Path:
    """Create a run directory with run.yaml and optional events.jsonl."""
    run_dir = base / ".trw" / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    yaml_data: dict[str, object] = run_yaml_content or {
        "run_id": run_id,
        "task": task,
        "status": "active",
        "phase": "implement",
    }
    _writer.write_yaml(meta / "run.yaml", yaml_data)

    if events:
        events_path = meta / "events.jsonl"
        for evt in events:
            _writer.append_jsonl(events_path, evt)

    return run_dir
