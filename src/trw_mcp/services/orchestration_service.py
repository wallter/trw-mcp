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

import structlog
from typing_extensions import TypedDict

from trw_mcp.services._local_run_identity import resolve_owned_run_path

_logger = structlog.get_logger(__name__)


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


class LocalStatusResult(TypedDict):
    """Result of local run status resolution."""

    run_id: str
    run_path: str
    task: str
    status: str
    phase: str
    checkpoints: int
    events: int


# ---------------------------------------------------------------------------
# FR02a: Run directory creation
# ---------------------------------------------------------------------------


def scaffold_run_directory(
    task_name: str,
    *,
    runs_root: Path | None = None,
    trw_dir: Path | None = None,
    run_id: str | None = None,
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

    if run_id is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{timestamp}-{secrets.token_hex(4)}"

    run_root = resolved_runs / task_name / run_id

    # PRD-QUAL-110-FR02 follow-up: harden the .trw root + standard state
    # subdirs (runs/, learnings/, logs/ …) to 0700 BEFORE scaffolding the run
    # tree, so a fresh install matches the README "0700" security claim. The
    # run subdirs created below inherit the hardened parent; harden the .trw
    # root explicitly since it may have been created at the default umask.
    from trw_mcp.state._paths_permissions import harden_dir_mode, harden_trw_tree

    harden_trw_tree(resolved_trw, create_subdirs=True)
    harden_dir_mode(resolved_runs, create=True)

    # Scaffold subdirectories (matches MCP tool layout)
    for subdir in ("meta", "reports", "scratch/_orchestrator", "shards"):
        (run_root / subdir).mkdir(parents=True, exist_ok=True)

    # Write minimal run.yaml
    run_yaml_path = run_root / "meta" / "run.yaml"
    ts_iso = datetime.now(timezone.utc).isoformat()
    run_data: dict[str, object] = {
        "run_id": run_id,
        "task": task_name,
        "status": "active",
        "phase": "research",
        "created_at": ts_iso,
        "source": "local_cli",
    }
    from trw_mcp.state.persistence import FileStateWriter

    FileStateWriter().write_yaml(run_yaml_path, run_data)

    # Write initial event
    events_path = run_root / "meta" / "events.jsonl"
    _append_event(events_path, "run_init", {"task": task_name, "source": "local_cli"})

    _logger.info(
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
    When *run_path* is omitted the run is resolved from this session's pin
    (PRD-FIX-132); a session with no pin gets a refusal, never a guess.

    Args:
        message: Checkpoint description (what was done, what comes next).
        run_path: Explicit path to the run directory.
        shard_id: Optional shard identifier.
        wave_id: Optional wave identifier.

    Returns:
        Dict with ``timestamp``, ``status``, and ``message``.

    Raises:
        FileNotFoundError: If ``run_path`` is given but does not exist, or --
        as :class:`~trw_mcp.services._local_run_identity.LocalRunIdentityError`
        -- if it is omitted and no pin resolves. Nothing is written in that case.
    """
    resolved = resolve_owned_run_path(run_path)
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

    _logger.info(
        "local_checkpoint_ok",
        message=message[:80],
        run_path=str(resolved),
    )

    return CheckpointResult(
        timestamp=ts,
        status="checkpoint_created",
        message=message,
    )


def write_local_learning(
    summary: str,
    detail: str,
    *,
    trw_dir: Path | None = None,
    tags: list[str] | None = None,
) -> dict[str, object]:
    """Write a local learning through the same learn implementation used by MCP.

    **The write is marked twice, with deliberately different lifetimes**
    (PRD-CORE-247-FR04):

    - ``source_identity="local_cli"`` — durable provenance. The field is a plain
      string on the storage model with no whitelist validator, so it survives the
      write intact and answers "where did this come from" permanently.
    - the ``trw-reconcile-pending`` tag — a transient queue entry, removed by the
      next successful ``trw_session_start`` once it has reported the row.

    The former ``source_type="local_cli"`` argument is **gone**, not preserved.
    ``_learning_helpers._validate_source_type`` coerces any value outside
    ``VALID_SOURCES`` to ``"agent"`` before storage, so it was a silently erased
    marker sitting beside a working one — the same "unevaluated gate that reads
    like a passed gate" shape :func:`mark_local_delivered` already corrected.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._constants import LOCAL_CLI_SOURCE_IDENTITY, RECONCILE_PENDING_TAG
    from trw_mcp.tools._learn_impl import execute_learn

    if not summary:
        raise ValueError("summary is required")
    if not detail:
        raise ValueError("detail is required")
    marked_tags = list(tags or [])
    if RECONCILE_PENDING_TAG not in marked_tags:
        marked_tags.append(RECONCILE_PENDING_TAG)
    return dict(
        execute_learn(
            summary=summary,
            detail=detail,
            trw_dir=trw_dir or (Path.cwd() / ".trw"),
            config=get_config(),
            tags=marked_tags,
            source_identity=LOCAL_CLI_SOURCE_IDENTITY,
        )
    )


def read_local_status(*, run_path: Path | None = None) -> LocalStatusResult:
    """Read status for the active local run without requiring MCP transport."""
    resolved = resolve_owned_run_path(run_path)
    meta = resolved / "meta"
    from trw_mcp.state.persistence import FileStateReader

    run_data = FileStateReader().read_yaml(meta / "run.yaml")
    checkpoints = _count_jsonl(meta / "checkpoints.jsonl")
    events = _count_jsonl(meta / "events.jsonl")
    return LocalStatusResult(
        run_id=str(run_data.get("run_id", resolved.name)),
        run_path=str(resolved),
        task=str(run_data.get("task", resolved.parent.name)),
        status=str(run_data.get("status", "unknown")),
        phase=str(run_data.get("phase", "unknown")),
        checkpoints=checkpoints,
        events=events,
    )


def mark_local_delivered(
    message: str = "local delivery",
    *,
    run_path: Path | None = None,
) -> LocalStatusResult:
    """Mark the active local run delivered and append a delivery event.

    **This path evaluates no deliver gate, and now says so in the record.**

    The MCP ``trw_deliver`` runs a six-gate table with three override policies and
    validates a structured ``AcceptableFailureRecord``. This offline fallback does
    none of that: it sets ``status`` and stamps a time. Measured — ``local init``
    followed immediately by ``local deliver`` exits 0 with no warning and leaves a
    run marked ``delivered`` while still in ``phase: research``.

    That is defensible as a fallback; what was not defensible is that the resulting
    ``run.yaml`` was **byte-identical** to a gated delivery. An unevaluated gate
    that reads exactly like a passed gate is the same shape this codebase has now
    fixed in a shell allow/deny gate, a PRD proof-path check, a config-consumer
    check and a sidecar refresh count. Recording ``gate_evaluated: false`` makes
    the two distinguishable by anyone reading the run afterwards.

    It does NOT weaken the obligation. CONSTITUTION §1.a binds the agent whichever
    surface records the delivery; this stamp is what lets a reader tell which
    surface did.
    """
    resolved = resolve_owned_run_path(run_path)
    meta = resolved / "meta"
    run_yaml = meta / "run.yaml"
    from trw_mcp.models.run import RunStatus
    from trw_mcp.state.persistence import FileStateReader, FileStateWriter

    run_data = FileStateReader().read_yaml(run_yaml)
    run_data["status"] = RunStatus.DELIVERED.value
    run_data["delivered_at"] = datetime.now(timezone.utc).isoformat()
    # Explicit False, never omitted: an absent key is indistinguishable from an
    # older record, and "we did not check" has to be positively stated.
    run_data["gate_evaluated"] = False
    run_data["delivery_surface"] = "local_cli"
    FileStateWriter().write_yaml(run_yaml, run_data)
    write_checkpoint(message, run_path=resolved)
    _append_event(
        meta / "events.jsonl",
        "deliver",
        {"message": message, "source": "local_cli", "gate_evaluated": False},
    )
    return read_local_status(run_path=resolved)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    """Append a single JSON record to a JSONL file."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _append_event(
    events_path: Path,
    event_type: str,
    data: dict[str, object],
) -> None:
    """Append a timestamped event to events.jsonl."""
    record: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "event_type": event_type,
        "type": event_type,
        **data,
    }
    _append_jsonl(events_path, record)
