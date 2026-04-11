"""Compatibility adapters for historical ceremony nudge imports.

These helpers keep the old import surface available for callers that still
reference ``append_ceremony_nudge()`` while delegating the live behavior to the
production ceremony-status path. They do not maintain a separate nudge
selection pipeline.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state.ceremony_progress import CeremonyState
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _hydrate_files_modified(state: CeremonyState, trw_dir: Path) -> None:
    """Hydrate compatibility state from file-modified events."""

    try:
        from trw_mcp.state._paths import find_active_run

        run_dir = find_active_run()
        if run_dir is None:
            return

        events_path = Path(run_dir) / "meta" / "events.jsonl"
        if not events_path.exists():
            return

        events = FileStateReader().read_jsonl(events_path)
        threshold = state.last_checkpoint_ts or ""
        state.files_modified_since_checkpoint = sum(
            1
            for event in events
            if str(event.get("type", "")) == "file_modified" and str(event.get("ts", "")) > threshold
        )
    except Exception:  # justified: fail-open, compatibility hydration must not break callers
        logger.warning(
            "hydrate_files_modified_failed",
            component="legacy_nudge",
            op="hydrate_files_modified",
            outcome="fail_open",
            exc_info=True,
        )


def log_nudge_event(
    events_path: Path,
    learning_id: str,
    phase: str,
    is_fallback: bool,
    turn: int = 0,
    surface_type: str = "nudge",
) -> None:
    """Emit the historical ``nudge_shown`` event schema for compatibility."""

    try:
        from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

        writer = FileStateWriter()
        FileEventLogger(writer).log_event(
            events_path,
            "nudge_shown",
            {
                "data": {
                    "learning_id": learning_id,
                    "phase": phase,
                    "turn": turn,
                    "surface_type": surface_type,
                },
                "learning_id": learning_id,
                "phase": phase,
                "fallback": is_fallback,
            },
        )
        logger.debug(
            "nudge_event_logged",
            component="legacy_nudge",
            op="log_nudge_event",
            outcome="success",
            learning_id=learning_id,
            phase=phase,
            fallback=is_fallback,
        )
    except Exception:  # justified: fail-open, compatibility telemetry must not break callers
        logger.warning(
            "nudge_event_log_failed",
            component="legacy_nudge",
            op="log_nudge_event",
            outcome="fail_open",
            exc_info=True,
        )


def append_ceremony_nudge(
    response: dict[str, object],
    trw_dir: Path | None = None,
    available_learnings: int = 0,
    context: object | None = None,
) -> dict[str, object]:
    """Backward-compatible alias for the live ceremony-status decorator."""

    try:
        from trw_mcp.tools._ceremony_status import append_ceremony_status

        if available_learnings or context is not None:
            logger.debug(
                "legacy_nudge_compat_delegate",
                component="legacy_nudge",
                op="append_ceremony_nudge",
                outcome="delegated",
                available_learnings=available_learnings,
                has_context=context is not None,
            )
        return append_ceremony_status(response, trw_dir)
    except Exception:  # justified: fail-open, compatibility wrapper must never break callers
        logger.warning(
            "append_ceremony_nudge_failed",
            component="legacy_nudge",
            op="append_ceremony_nudge",
            outcome="fail_open",
            exc_info=True,
        )
        return response
