"""Shared orchestration service — run scaffolding and checkpoint logic.

Extracted from ``trw_mcp.tools.orchestration`` (PRD-FIX-073) so the same
business logic is callable from both the MCP ``trw_init``/``trw_checkpoint``
tools and the ``trw-mcp local`` CLI subcommands.

This module has NO dependency on FastMCP, making it safe to import
from CLI entry points that run without a server.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import structlog

logger = structlog.get_logger(__name__)


class RunScaffoldResult(TypedDict):
    """Result of run directory scaffolding."""

    run_id: str
    run_path: str
    status: str


class CheckpointResult(TypedDict):
    """Result of a checkpoint write."""

    timestamp: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# FR02a: Run directory creation
# ---------------------------------------------------------------------------


def scaffold_run_directory(
    task_name: str,
    *,
    runs_root: Path | None = None,
    trw_dir: Path | None = None,
) -> RunScaffoldResult:
    """Create a minimal run directory structure for local ceremony fallback.

    This produces the same directory layout that the MCP ``trw_init`` tool
    creates, but without config resolution, complexity classification, or
    wave/artifact scanning.  Intended for offline/fallback use when the
    MCP server is unreachable.

    Args:
        task_name: Name of the task (becomes directory name).
        runs_root: Root directory for runs.  Defaults to ``<cwd>/.trw/runs``.
        trw_dir: Path to .trw directory.  Defaults to ``<cwd>/.trw``.

    Returns:
        Dict with ``run_id``, ``run_path``, and ``status``.
    """
    resolved_trw = trw_dir or (Path.cwd() / ".trw")
    resolved_runs = runs_root or (resolved_trw / "runs")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{timestamp}-{secrets.token_hex(4)}"

    run_root = resolved_runs / task_name / run_id

    # Scaffold subdirectories (matches MCP tool layout)
    for subdir in ("meta", "reports", "scratch/_orchestrator", "shards"):
        (run_root / subdir).mkdir(parents=True, exist_ok=True)

    # Write minimal run.yaml
    run_yaml_path = run_root / "meta" / "run.yaml"
    ts_iso = datetime.now(timezone.utc).isoformat()
    run_data = {
        "run_id": run_id,
        "task": task_name,
        "status": "active",
        "phase": "research",
        "created_at": ts_iso,
        "source": "local_cli",
    }
    run_yaml_path.write_text(
        json.dumps(run_data, indent=2) + "\n",
        encoding="utf-8",
    )

    # Write initial event
    events_path = run_root / "meta" / "events.jsonl"
    _append_event(events_path, "run_init", {"task": task_name, "source": "local_cli"})

    logger.info(
        "local_run_init_ok",
        run_id=run_id,
        task=task_name,
        run_path=str(run_root),
    )

    return RunScaffoldResult(
        run_id=run_id,
        run_path=str(run_root),
        status="initialized",
    )


# ---------------------------------------------------------------------------
# FR02b: Checkpoint writing
# ---------------------------------------------------------------------------


def write_checkpoint(
    message: str,
    *,
    run_path: Path | None = None,
    shard_id: str | None = None,
    wave_id: str | None = None,
) -> CheckpointResult:
    """Append a checkpoint record to checkpoints.jsonl.

    Works with any run directory that has a ``meta/`` subdirectory.
    If ``run_path`` is not provided, attempts to auto-detect the active run
    from ``.trw/runs/``.

    Args:
        message: Checkpoint description (what was done, what comes next).
        run_path: Explicit path to the run directory.
        shard_id: Optional shard identifier.
        wave_id: Optional wave identifier.

    Returns:
        Dict with ``timestamp``, ``status``, and ``message``.

    Raises:
        FileNotFoundError: If ``run_path`` is given but does not exist.
    """
    resolved = _resolve_run_path(run_path)
    meta_path = resolved / "meta"

    if not meta_path.exists():
        meta_path.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).isoformat()

    checkpoint: dict[str, object] = {
        "ts": ts,
        "message": message,
    }
    if shard_id:
        checkpoint["shard_id"] = shard_id
    if wave_id:
        checkpoint["wave_id"] = wave_id

    checkpoints_path = meta_path / "checkpoints.jsonl"
    _append_jsonl(checkpoints_path, checkpoint)

    event_data: dict[str, object] = {"message": message}
    if shard_id:
        event_data["shard_id"] = shard_id
    if wave_id:
        event_data["wave_id"] = wave_id
    _append_event(meta_path / "events.jsonl", "checkpoint", event_data)

    logger.info(
        "local_checkpoint_ok",
        message=message[:80],
        run_path=str(resolved),
    )

    return CheckpointResult(
        timestamp=ts,
        status="checkpoint_created",
        message=message,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_run_path(run_path: Path | None) -> Path:
    """Resolve run directory, defaulting to most recent active run."""
    if run_path is not None:
        p = Path(run_path)
        if not p.exists():
            raise FileNotFoundError(f"Run path does not exist: {p}")
        return p

    # Auto-detect: find the most recently modified run dir
    runs_root = Path.cwd() / ".trw" / "runs"
    if not runs_root.exists():
        raise FileNotFoundError(
            "No .trw/runs/ directory found. Run 'trw-mcp local init --task NAME' first."
        )

    # Walk runs/ looking for meta/run.yaml
    candidates: list[tuple[float, Path]] = []
    for run_yaml in runs_root.glob("**/meta/run.yaml"):
        candidates.append((run_yaml.stat().st_mtime, run_yaml.parent.parent))

    if not candidates:
        raise FileNotFoundError("No active runs found in .trw/runs/")

    candidates.sort(reverse=True)
    return candidates[0][1]


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    """Append a single JSON record to a JSONL file."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _append_event(
    events_path: Path,
    event_type: str,
    data: dict[str, object],
) -> None:
    """Append a timestamped event to events.jsonl."""
    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        **data,
    }
    _append_jsonl(events_path, record)
