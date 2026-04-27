"""TRW auto-checkpoint tools — counter, pre-compact checkpoint.

PRD-CORE-053: Tool call counting and automatic checkpoint creation.
Extracted from ceremony.py for single-responsibility.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import CheckpointResultDict, PreCompactResultDict
from trw_mcp.models.typed_dicts._orchestration import CheckpointRecordDict
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
    resolve_pin_key,
    resolve_project_root,
)
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


# --- Auto-checkpoint state (PRD-CORE-053, Item 3 of PRD-FIX-030) ---


@dataclasses.dataclass(slots=True)
class _CheckpointState:
    """Mutable state for auto-checkpoint counter. Single-process only."""

    counter: int = 0


_checkpoint_state = _CheckpointState()


def _reset_tool_call_counter() -> None:
    """Reset the tool call counter (for testing)."""
    _checkpoint_state.counter = 0


_CEREMONY_OBLIGATIONS: list[tuple[str, str, str]] = [
    ("session_started", "trw_session_start()", "not yet called"),
    ("build_checked", "trw_build_check()", "required before delivery"),
    ("review_done", "trw_review()", "recommended before delivery"),
    ("delivered", "trw_deliver()", "required at session end"),
]


def _compute_pending_ceremony(ceremony_state: dict[str, object]) -> list[str]:
    """Return list of pending ceremony obligation descriptions."""
    return [f"{tool} — {desc}" for key, tool, desc in _CEREMONY_OBLIGATIONS if not ceremony_state.get(key)]


def _maybe_auto_checkpoint() -> CheckpointResultDict | None:
    """Increment tool call counter; create checkpoint at configured intervals.

    Returns checkpoint info dict if triggered, None otherwise.
    Best-effort: exceptions are swallowed.
    """
    try:
        cfg = get_config()
        if not cfg.auto_checkpoint_enabled:
            return None
        interval = cfg.auto_checkpoint_tool_interval
        if interval <= 0:
            return None

        _checkpoint_state.counter += 1
        if _checkpoint_state.counter % interval != 0:
            return None

        run_dir = find_active_run()
        if run_dir is None:
            return None

        count = _checkpoint_state.counter
        msg = f"auto-checkpoint after {count} tool calls"
        logger.info("auto_checkpoint_triggered", tool_call_count=count, threshold=interval)
        _do_checkpoint(run_dir, msg)
        logger.debug("checkpoint_created", tool_calls=count, run_dir=str(run_dir))
        return {"auto_checkpoint": True, "tool_calls": count}
    except Exception as _cp_exc:  # justified: fail-open, auto-checkpoint must not disrupt tool flow
        logger.warning("checkpoint_failed", run_id="", error=str(_cp_exc))
        logger.debug("auto_checkpoint_failed", exc_info=True)
        return None


def _do_checkpoint(run_dir: Path, message: str) -> None:
    """Append a checkpoint to the run's checkpoints.jsonl."""
    writer = FileStateWriter()
    events = FileEventLogger(writer)

    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    checkpoint_data: CheckpointRecordDict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    writer.append_jsonl(checkpoints_path, cast("dict[str, object]", checkpoint_data))

    events_path = run_dir / "meta" / "events.jsonl"
    if events_path.parent.exists():
        events.log_event(events_path, "checkpoint", {"message": message})

    logger.info(
        "checkpoint_ok",
        run_id=run_dir.name,
        message=message[:80],
    )
    logger.debug("checkpoint_detail", run_dir=str(run_dir))


def _read_pre_compact_state(run_dir: Path, project_root: Path) -> dict[str, object]:
    """Read PRD scope, phase, and other state from run.yaml."""
    reader = FileStateReader()
    prd_scope: list[str] = []
    phase = ""

    run_yaml = run_dir / "meta" / "run.yaml"
    if run_yaml.exists():
        run_data = reader.read_yaml(run_yaml)
        if isinstance(run_data, dict):
            raw_scope = run_data.get("prd_scope", [])
            if isinstance(raw_scope, list):
                prd_scope = [str(s) for s in raw_scope]
            phase = str(run_data.get("phase", ""))

    ownership_path = project_root / ".trw" / "context" / "file_ownership.yaml"
    file_ownership_path = str(ownership_path) if ownership_path.exists() else ""

    return {
        "prd_scope": prd_scope,
        "phase": phase,
        "file_ownership_path": file_ownership_path,
    }


def _read_last_events(events_path: Path) -> list[str]:
    """Extract last 5 event types from events.jsonl."""
    import json

    last_5_events: list[str] = []
    if events_path.exists():
        lines = events_path.read_text().strip().split("\n")
        for line in lines[-5:]:
            try:
                evt = json.loads(line)
                last_5_events.append(str(evt.get("event_type", "")))
            except Exception:  # per-item error handling: skip malformed JSONL lines, scan-resilience
                logger.debug("jsonl_line_parse_failed", exc_info=True)
    return last_5_events


def _read_failing_tests(project_root: Path) -> list[str]:
    """Extract failing tests from build-status.yaml."""
    reader = FileStateReader()
    failing_tests: list[str] = []

    build_status_path = project_root / ".trw" / "context" / "build-status.yaml"
    if build_status_path.exists():
        bs_data = reader.read_yaml(build_status_path)
        if isinstance(bs_data, dict):
            raw_ft = bs_data.get("failing_tests", [])
            if isinstance(raw_ft, list):
                failing_tests = [str(t) for t in raw_ft]
    return failing_tests


def _read_ceremony_state(project_root: Path) -> dict[str, object]:
    """Read ceremony state for recovery."""
    import json

    ceremony_state: dict[str, object] = {}
    ceremony_path = project_root / ".trw" / "context" / "ceremony-state.json"
    if ceremony_path.exists():
        try:
            ceremony_state = json.loads(ceremony_path.read_text(encoding="utf-8"))
        except Exception:  # justified: fail-open, ceremony state read
            logger.warning("ceremony_state_read_failed", exc_info=True)
    return ceremony_state


def _write_compact_state(
    project_root: Path,
    run_dir: Path,
    events_path: Path,
    prd_scope: list[str],
    phase: str,
    file_ownership_path: str,
    failing_tests: list[str],
    ceremony_state: dict[str, object],
) -> None:
    """Write pre_compact_state.json with enhanced checkpoint metadata."""
    import json

    state_file = project_root / ".trw" / "context" / "pre_compact_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)

    _evt_text = events_path.read_text().strip() if events_path.exists() else ""
    pending_ceremony = _compute_pending_ceremony(ceremony_state)

    state_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": "mcp_tool",
        "run_path": str(run_dir),
        "phase": phase,
        "events_logged": len(_evt_text.split("\n")) if _evt_text else 0,
        "last_checkpoint": "pre-compaction safety checkpoint",
        "prd_scope": prd_scope,
        "file_ownership_path": file_ownership_path,
        "last_5_events": _read_last_events(events_path),
        "failing_tests": failing_tests,
        "ceremony_state": ceremony_state,
        "pending_ceremony": pending_ceremony,
    }
    state_file.write_text(json.dumps(state_data, indent=2))


def _write_compact_instructions(
    cfg: object,
    project_root: Path,
    run_dir: Path,
    phase: str,
    prd_scope: list[str],
    file_ownership_path: str,
    failing_tests: list[str],
    ceremony_state: dict[str, object],
) -> Path:
    """Write compact_instructions.txt with ceremony recovery guidance."""
    template = cfg.compact_instructions_template  # type: ignore[attr-defined]
    if not template:
        template = (
            "Preserve exactly:\n"
            "- TRW phase: {phase}\n"
            "- TRW run_id: {run_id}\n"
            "- TRW PRD scope: {prd_scope}\n"
            "- Last checkpoint: {last_checkpoint}\n"
            "- File ownership: {file_ownership_path}\n"
            "- Failing tests: {failing_tests}\n"
            "DO NOT summarize run artifacts — reference their file paths only.\n"
            "Reference .trw/context/pre_compact_state.json for full state.\n"
            "\n"
            "CEREMONY OBLIGATIONS (complete these before session ends):\n"
            "{ceremony_pending}"
        )
    pending_ceremony = _compute_pending_ceremony(ceremony_state)
    instructions = template.format(
        phase=phase,
        run_id=str(run_dir.name),
        prd_scope=", ".join(prd_scope) if prd_scope else "none",
        last_checkpoint="pre-compaction safety checkpoint",
        file_ownership_path=file_ownership_path or "not set",
        failing_tests=", ".join(failing_tests) if failing_tests else "none",
        ceremony_pending="\n".join(f"- {s}" for s in pending_ceremony) if pending_ceremony else "- all complete",
    )
    instructions_path = project_root / ".trw" / "context" / "compact_instructions.txt"
    instructions_path.write_text(instructions)
    return instructions_path


def _build_call_context(ctx: Context | None) -> TRWCallContext:
    """Construct a :class:`TRWCallContext` for pin-state helpers (PRD-CORE-141 FR03)."""
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )


def register_checkpoint_tools(server: FastMCP) -> None:
    """Register checkpoint tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_pre_compact_checkpoint(ctx: Context | None = None) -> PreCompactResultDict:
        """Capture a safety checkpoint before the context window compacts.

        Use when:
        - Invoked by the PreCompact hook on imminent context compaction.
        - You suspect compaction is near and want a clean resume point on disk.

        Best-effort: sub-step failures populate ``status`` but do not raise.

        Output: PreCompactResultDict with fields
        {status: "written"|"skipped"|"error", reason?: str,
         checkpoint_path?: str, instructions_path?: str, compact_state_path?: str}.
        """
        cfg = get_config()
        if not cfg.auto_checkpoint_pre_compact:
            return {"status": "skipped", "reason": "auto_checkpoint_pre_compact disabled"}

        try:
            # PRD-CORE-141 FR03/FR05: ctx-aware find_active_run suppresses
            # scan fallback for fresh sessions.
            run_dir = find_active_run(context=_build_call_context(ctx))
            if run_dir is None:
                return {"status": "skipped", "reason": "no_active_run"}

            _do_checkpoint(run_dir, "pre-compaction safety checkpoint")

            project_root = resolve_project_root()
            events_path = run_dir / "meta" / "events.jsonl"

            # Read state snapshots
            state_dict = _read_pre_compact_state(run_dir, project_root)
            prd_scope: list[str] = cast("list[str]", state_dict["prd_scope"])
            phase: str = cast("str", state_dict["phase"])
            file_ownership_path: str = cast("str", state_dict["file_ownership_path"])

            failing_tests = _read_failing_tests(project_root)
            ceremony_state = _read_ceremony_state(project_root)

            # Write artifacts
            _write_compact_state(
                project_root,
                run_dir,
                events_path,
                prd_scope,
                phase,
                file_ownership_path,
                failing_tests,
                ceremony_state,
            )
            instructions_path = _write_compact_instructions(
                cfg,
                project_root,
                run_dir,
                phase,
                prd_scope,
                file_ownership_path,
                failing_tests,
                ceremony_state,
            )

            result: PreCompactResultDict = {
                "status": "success",
                "run_path": str(run_dir),
                "compact_instructions_path": str(instructions_path),
                "prd_scope": prd_scope,
                "failing_tests": failing_tests,
            }
            return result
        except Exception as exc:  # justified: boundary, compact instructions generation may fail on I/O
            return {"status": "failed", "error": str(exc)}
