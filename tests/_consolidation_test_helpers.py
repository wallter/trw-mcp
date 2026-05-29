"""Shared helpers for consolidation test modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_mcp.state.persistence import FileStateWriter


def make_vec(x: float, y: float = 0.0, z: float = 0.0) -> list[float]:
    """Return a unit vector in 3D (normalized)."""
    import math

    mag = math.sqrt(x * x + y * y + z * z)
    if mag == 0.0:
        return [0.0, 0.0, 0.0]
    return [x / mag, y / mag, z / mag]


def write_entry(
    entries_dir: Path,
    writer: FileStateWriter,
    entry_id: str,
    summary: str = "test summary",
    detail: str = "test detail",
    status: str = "active",
    source_type: str | None = None,
    consolidated_into: str | None = None,
    impact: float = 0.5,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    recurrence: int = 1,
    q_value: float = 0.0,
) -> Path:
    """Write a minimal learning entry YAML for testing."""
    path = entries_dir / f"{entry_id}.yaml"
    data: dict[str, Any] = {
        "id": entry_id,
        "summary": summary,
        "detail": detail,
        "status": status,
        "impact": impact,
        "tags": tags or ["testing"],
        "evidence": evidence or [],
        "recurrence": recurrence,
        "q_value": q_value,
    }
    if source_type is not None:
        data["source_type"] = source_type
    if consolidated_into is not None:
        data["consolidated_into"] = consolidated_into
    writer.write_yaml(path, data)
    return path


def make_cluster(n: int = 3) -> list[dict[str, Any]]:
    """Create a simple cluster of n entry dicts for testing."""
    return [
        {
            "id": f"L-entry{i:03d}",
            "summary": f"summary {i}",
            "detail": f"detail {i}",
            "impact": 0.5 + i * 0.1,
            "tags": [f"tag{i}", "shared"],
            "evidence": [f"evidence{i}"],
            "recurrence": i + 1,
            "q_value": 0.1 * i,
        }
        for i in range(n)
    ]


def patch_trw_deliver_deps(trw_dir: Path) -> Any:
    """Return a context manager that patches all trw_deliver sub-operations."""
    from contextlib import ExitStack

    import trw_mcp.tools.ceremony as ceremony_mod

    stack = ExitStack()
    stack.enter_context(
        patch.object(
            ceremony_mod,
            "_do_reflect",
            return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0, "success_patterns": 0},
        )
    )
    stack.enter_context(patch.object(ceremony_mod, "find_active_run", return_value=None))
    stack.enter_context(patch.object(ceremony_mod, "resolve_trw_dir", return_value=trw_dir))
    stack.enter_context(
        patch.object(
            ceremony_mod,
            "_do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "total_lines": 0, "path": ""},
        )
    )
    stack.enter_context(patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value={"status": "success"}))
    stack.enter_context(patch("trw_mcp.tools._deferred_delivery._do_auto_progress", return_value={"status": "skipped"}))
    stack.enter_context(patch("trw_mcp.telemetry.publisher.publish_learnings", return_value={"status": "skipped"}))
    stack.enter_context(patch("trw_mcp.scoring.process_outcome_for_event", return_value=[]))
    stack.enter_context(patch("trw_mcp.state.recall_tracking.get_recall_stats", return_value={}))
    stack.enter_context(patch("trw_mcp.telemetry.client.TelemetryClient.from_config", return_value=MagicMock()))
    stack.enter_context(
        patch(
            "trw_mcp.telemetry.sender.BatchSender.from_config",
            return_value=MagicMock(send=MagicMock(return_value={"status": "skipped"})),
        )
    )
    return stack
