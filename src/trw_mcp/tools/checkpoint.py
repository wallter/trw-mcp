"""TRW auto-checkpoint tools — counter, pre-compact checkpoint.

PRD-CORE-053: Tool call counting and automatic checkpoint creation.
Extracted from ceremony.py for single-responsibility.
"""

from __future__ import annotations

import dataclasses
from typing import cast
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import CheckpointResultDict, PreCompactResultDict
from trw_mcp.models.typed_dicts._orchestration import CheckpointRecordDict
from trw_mcp.state._paths import find_active_run, resolve_project_root
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()


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
    return [
        f"{tool} — {desc}"
        for key, tool, desc in _CEREMONY_OBLIGATIONS
        if not ceremony_state.get(key)
    ]


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
        _do_checkpoint(run_dir, msg)
        logger.debug("checkpoint_created", tool_calls=count, run_dir=str(run_dir))
        return {"auto_checkpoint": True, "tool_calls": count}
    except Exception:  # justified: fail-open, auto-checkpoint must not disrupt tool flow
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
    writer.append_jsonl(checkpoints_path, cast(dict[str, object], checkpoint_data))

    events_path = run_dir / "meta" / "events.jsonl"
    if events_path.parent.exists():
        events.log_event(events_path, "checkpoint", {"message": message})


def register_checkpoint_tools(server: FastMCP) -> None:
    """Register checkpoint tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_pre_compact_checkpoint() -> PreCompactResultDict:
        """Create a safety checkpoint before context compaction (PRD-CORE-053).

        Called by the PreCompact hook to preserve state before the LLM
        context window compacts. Best-effort: failures return status but
        do not raise.
        """
        cfg = get_config()
        if not cfg.auto_checkpoint_pre_compact:
            return {"status": "skipped", "reason": "auto_checkpoint_pre_compact disabled"}

        try:
            run_dir = find_active_run()
            if run_dir is None:
                return {"status": "skipped", "reason": "no_active_run"}

            _do_checkpoint(run_dir, "pre-compaction safety checkpoint")

            # Enhanced state capture (PRD-CORE-066-FR05)
            import json

            reader = FileStateReader()

            result: PreCompactResultDict = {
                "status": "success",
                "run_path": str(run_dir),
            }

            # Read prd_scope from run.yaml
            run_yaml = run_dir / "meta" / "run.yaml"
            prd_scope: list[str] = []
            phase = ""
            if run_yaml.exists():
                run_data = reader.read_yaml(run_yaml)
                if isinstance(run_data, dict):
                    raw_scope = run_data.get("prd_scope", [])
                    if isinstance(raw_scope, list):
                        prd_scope = [str(s) for s in raw_scope]
                    phase = str(run_data.get("phase", ""))

            # Check file_ownership.yaml
            project_root = resolve_project_root()
            ownership_path = project_root / ".trw" / "context" / "file_ownership.yaml"
            file_ownership_path = str(ownership_path) if ownership_path.exists() else ""

            # Last 5 events
            events_path = run_dir / "meta" / "events.jsonl"
            last_5_events: list[str] = []
            if events_path.exists():
                lines = events_path.read_text().strip().split("\n")
                for line in lines[-5:]:
                    try:
                        evt = json.loads(line)
                        last_5_events.append(str(evt.get("event_type", "")))
                    except Exception:  # justified: scan-resilience, skip malformed JSONL lines
                        logger.debug("jsonl_line_parse_failed", exc_info=True)

            # Failing tests from build-status.yaml
            build_status_path = project_root / ".trw" / "context" / "build-status.yaml"
            failing_tests: list[str] = []
            if build_status_path.exists():
                bs_data = reader.read_yaml(build_status_path)
                if isinstance(bs_data, dict):
                    raw_ft = bs_data.get("failing_tests", [])
                    if isinstance(raw_ft, list):
                        failing_tests = [str(t) for t in raw_ft]

            # Read ceremony state for recovery (Sprint 68 gap fix)
            ceremony_state: dict[str, object] = {}
            ceremony_path = project_root / ".trw" / "context" / "ceremony-state.json"
            if ceremony_path.exists():
                try:
                    ceremony_state = json.loads(
                        ceremony_path.read_text(encoding="utf-8")
                    )
                except Exception:  # justified: fail-open, ceremony state read
                    logger.warning("ceremony_state_read_failed", exc_info=True)

            pending_ceremony = _compute_pending_ceremony(ceremony_state)

            # Write enhanced pre_compact_state.json
            state_file = project_root / ".trw" / "context" / "pre_compact_state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            _evt_text = events_path.read_text().strip() if events_path.exists() else ""
            state_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trigger": "mcp_tool",
                "run_path": str(run_dir),
                "phase": phase,
                "events_logged": len(_evt_text.split("\n")) if _evt_text else 0,
                "last_checkpoint": "pre-compaction safety checkpoint",
                "prd_scope": prd_scope,
                "file_ownership_path": file_ownership_path,
                "last_5_events": last_5_events,
                "failing_tests": failing_tests,
                "ceremony_state": ceremony_state,
                "pending_ceremony": pending_ceremony,
            }
            state_file.write_text(json.dumps(state_data, indent=2))

            # Write compact_instructions.txt (PRD-CORE-066-FR02)
            template = cfg.compact_instructions_template
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

            result["compact_instructions_path"] = str(instructions_path)
            result["prd_scope"] = prd_scope
            result["failing_tests"] = failing_tests
            return result
        except Exception as exc:  # justified: boundary, compact instructions generation may fail on I/O
            return {"status": "failed", "error": str(exc)}
