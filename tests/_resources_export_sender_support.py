from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.conftest import get_resources_sync
from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


def _write_learning(entries_dir: Path, name: str, data: dict[str, object]) -> None:
    _writer.write_yaml(entries_dir / name, data)


def _get_learnings_resource() -> Any:
    from fastmcp import FastMCP

    from trw_mcp.resources.config import register_config_resources

    srv = FastMCP("test")
    register_config_resources(srv)
    return get_resources_sync(srv)["trw://learnings/summary"].fn


def _setup_project(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_entry(
    entries_dir: Path,
    *,
    summary: str = "Test learning",
    impact: float = 0.8,
    tags: list[str] | None = None,
    created: str = "2026-02-21T00:00:00Z",
) -> None:
    import uuid

    entry_id = f"L-{uuid.uuid4().hex[:8]}"
    slug = summary.lower().replace(" ", "-")[:40]
    _writer.write_yaml(
        entries_dir / f"2026-02-21-{slug}.yaml",
        {
            "id": entry_id,
            "summary": summary,
            "detail": f"Detail for: {summary}",
            "impact": impact,
            "status": "active",
            "tags": tags or ["test"],
            "source_type": "agent",
            "created": created,
            "updated": "2026-02-21T00:00:00Z",
            "q_value": 0.5,
            "access_count": 1,
        },
    )


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def _make_sender(
    tmp_path: Path,
    *,
    platform_url: str = "https://api.example.com",
    batch_size: int = 100,
    max_retries: int = 1,
    backoff_base: float = 0.0,
) -> tuple[Any, Path]:
    from trw_mcp.telemetry.sender import BatchSender

    input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
    urls = [platform_url] if platform_url else []
    sender = BatchSender(
        platform_urls=urls,
        input_path=input_path,
        batch_size=batch_size,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )
    return sender, input_path
