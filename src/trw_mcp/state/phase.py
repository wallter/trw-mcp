"""Phase tracking utilities for automatic run phase updates.

Provides ``update_run_phase`` for direct use and ``try_update_phase`` as a
best-effort wrapper used by tool modules (DRY: single call replaces
identical try/except/pass blocks in build.py, ceremony.py, review.py,
and requirements.py).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.run import PHASE_ORDER, Phase
from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

logger = structlog.get_logger()

_reader = FileStateReader()
_writer = FileStateWriter()
_event_logger = FileEventLogger(_writer)


def update_run_phase(run_path: Path, new_phase: Phase) -> bool:
    """Update phase in run.yaml with forward-only guard.

    Returns True if phase was updated, False if skipped (already at or past target).
    Logs a ``phase_enter`` event to the run's events.jsonl on success.
    """
    run_yaml = run_path / "meta" / "run.yaml"
    if not _reader.exists(run_yaml):
        return False

    data = _reader.read_yaml(run_yaml)
    current = str(data.get("phase", "research"))
    current_order = PHASE_ORDER.get(current, 0)
    new_order = PHASE_ORDER.get(new_phase.value, 0)

    if new_order <= current_order:
        return False  # Forward-only: don't revert

    data["phase"] = new_phase.value
    _writer.write_yaml(run_yaml, data)
    logger.info("phase_updated", run_path=str(run_path), old=current, new=new_phase.value)

    # Log phase_enter event (best-effort)
    events_path = run_path / "meta" / "events.jsonl"
    if events_path.parent.exists():
        try:
            _event_logger.log_event(events_path, "phase_enter", {
                "phase": new_phase.value,
                "previous_phase": current,
            })
        except (OSError, StateError):
            logger.debug("phase_event_log_failed", phase=new_phase.value)

    return True


def try_update_phase(run_path: Path | None, phase: Phase) -> None:
    """Best-effort phase update — silently swallows all errors.

    Convenience wrapper used by tool modules to avoid duplicating the
    try/except/pass pattern across build.py, ceremony.py, review.py,
    and requirements.py.
    """
    if run_path is None:
        return
    try:
        update_run_phase(run_path, phase)
    except Exception:  # broad catch: best-effort wrapper, never raises
        logger.debug("try_update_phase_failed", phase=phase.value)
