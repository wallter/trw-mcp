"""TRW auto-checkpoint tools — counter, pre-compact checkpoint.

PRD-CORE-053: Tool call counting and automatic checkpoint creation.
Extracted from ceremony.py for single-responsibility.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import find_active_run
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_writer = FileStateWriter()
_events = FileEventLogger(_writer)


# --- Auto-checkpoint state (PRD-CORE-053, Item 3 of PRD-FIX-030) ---


@dataclasses.dataclass(slots=True)
class _CheckpointState:
    """Mutable state for auto-checkpoint counter. Single-process only."""

    counter: int = 0


_checkpoint_state = _CheckpointState()


def _reset_tool_call_counter() -> None:
    """Reset the tool call counter (for testing)."""
    _checkpoint_state.counter = 0


def _maybe_auto_checkpoint() -> dict[str, object] | None:
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
    except Exception:
        logger.debug("auto_checkpoint_failed", exc_info=True)
        return None


def _do_checkpoint(run_dir: Path, message: str) -> None:
    """Append a checkpoint to the run's checkpoints.jsonl."""
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    checkpoint_data: dict[str, object] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    _writer.append_jsonl(checkpoints_path, checkpoint_data)

    events_path = run_dir / "meta" / "events.jsonl"
    if events_path.parent.exists():
        _events.log_event(events_path, "checkpoint", {"message": message})


def register_checkpoint_tools(server: FastMCP) -> None:
    """Register checkpoint tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_pre_compact_checkpoint() -> dict[str, object]:
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
            return {"status": "success", "run_path": str(run_dir)}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}
