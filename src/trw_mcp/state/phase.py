"""Phase tracking utilities for automatic run phase updates.

Provides ``update_run_phase`` for direct use and ``try_update_phase`` as a
best-effort wrapper used by tool modules (DRY: single call replaces
identical try/except/pass blocks in build.py, ceremony.py, review.py,
and requirements.py).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.run import PHASE_ORDER, Phase
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)

# F13: Map a run ``Phase`` to the ``CeremonyState.phase`` vocabulary used by
# the nudge engine (``_nudge_rules._PHASE_APPLICABLE_STEPS``: early/implement/
# validate/review/deliver/done). RESEARCH and PLAN both collapse to ``early``
# so the early-phase nudge rules (only session_start/checkpoint applicable)
# fire correctly; the remaining phases map 1:1 by value.
_CEREMONY_PHASE_BY_RUN_PHASE: dict[Phase, str] = {
    Phase.RESEARCH: "early",
    Phase.PLAN: "early",
    Phase.IMPLEMENT: "implement",
    Phase.VALIDATE: "validate",
    Phase.REVIEW: "review",
    Phase.DELIVER: "deliver",
}


def _sync_ceremony_phase(new_phase: Phase) -> None:
    """Mirror the run phase into ``CeremonyState.phase`` (F13).

    Without this, ``CeremonyState.phase`` is stuck at its ``"early"`` default
    forever — the status line always emits ``phase=early`` and phase-aware
    nudge dedup (``is_nudge_eligible`` keyed on the current phase) never works.
    Resolution uses ``resolve_trw_dir()`` (respecting ``TRW_PROJECT_ROOT``) so
    the writer targets the same ``ceremony-state.json`` the status/nudge reader
    loads via ``read_ceremony_state``. Best-effort: never break a phase write.
    """
    try:
        from trw_mcp.state._ceremony_progress_state import set_ceremony_phase
        from trw_mcp.state._paths import resolve_trw_dir

        ceremony_phase = _CEREMONY_PHASE_BY_RUN_PHASE.get(new_phase, new_phase.value)
        set_ceremony_phase(resolve_trw_dir(), ceremony_phase)
    except Exception:  # justified: fail-open — ceremony mirror is best-effort
        logger.debug("ceremony_phase_sync_skipped", phase=new_phase.value, exc_info=True)


def _phase_skipped_for_tier(run_data: dict[str, object], phase_name: str) -> bool:
    """Return True if ``phase_name`` is skipped by this run's complexity tier.

    A MINIMAL/STANDARD tier intentionally skips some phases (e.g. RESEARCH,
    PLAN, REVIEW). The exit gate must NOT enforce a phase the active tier
    declares skippable, or a legitimate tier-based jump (MINIMAL
    IMPLEMENT→DELIVER) would be falsely blocked. Phase names in
    ``phase_requirements.skipped`` are persisted uppercase (PRD-CORE-060-FR04);
    compare case-insensitively against the lowercase ``Phase`` value.
    """
    reqs = run_data.get("phase_requirements")
    if not isinstance(reqs, dict):
        return False
    skipped = reqs.get("skipped")
    if not isinstance(skipped, list):
        return False
    return phase_name.lower() in {str(s).lower() for s in skipped}


def _enforce_exit_gate(run_path: Path, current_phase: str, run_data: dict[str, object]) -> None:
    """Run the exit gate for the phase being left, honoring tier + enforcement.

    Behavior keyed on ``config.phase_gate_enforcement``:
      - ``off``: no gate (early return).
      - ``lenient`` (DEFAULT): on a gate failure, log a warning and PROCEED —
        observable success/failure is unchanged, only a warning is added.
      - ``strict``: on a gate failure, raise ``StateError`` to BLOCK the write.

    Tier-awareness: phases the active complexity tier declares skipped are not
    enforced, so a legitimate tier-based phase jump is never blocked.

    A gate failure here is a *blocking* (``error``-severity) failure — advisory
    warnings/info produced by the checkers do not trip enforcement.
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    enforcement = config.phase_gate_enforcement
    if enforcement == "off":
        return

    # Do not enforce a phase the active tier intentionally skips.
    if _phase_skipped_for_tier(run_data, current_phase):
        logger.debug(
            "phase_exit_gate_skipped_for_tier",
            run_path=str(run_path),
            phase=current_phase,
        )
        return

    try:
        current = Phase(current_phase)
    except ValueError:
        # Unknown/legacy phase string — nothing to gate against.
        return

    from trw_mcp.state.validation import check_phase_exit

    result = check_phase_exit(current, run_path, config)
    if result.valid:
        return

    blocking = [f for f in result.failures if f.severity == "error"]
    messages = "; ".join(f"{f.field}: {f.message}" for f in blocking)
    if enforcement == "strict":
        raise StateError(
            f"Phase exit gate failed for '{current_phase}' phase: {messages}",
            suggestion="Satisfy the phase exit criteria, or set phase_gate_enforcement='lenient'.",
            from_phase=current_phase,
            failures=len(blocking),
        )
    # lenient (default): warn and proceed — non-breaking.
    logger.warning(
        "phase_exit_gate_unmet",
        run_path=str(run_path),
        from_phase=current_phase,
        failures=len(blocking),
        detail=messages,
    )


def update_run_phase(run_path: Path, new_phase: Phase) -> bool:
    """Update phase in run.yaml with forward-only guard.

    Returns True if phase was updated, False if skipped (already at or past target).
    Logs a ``phase_enter`` event to the run's events.jsonl on success.
    """
    reader = FileStateReader()
    writer = FileStateWriter()
    event_logger = FileEventLogger(writer)

    run_yaml = run_path / "meta" / "run.yaml"
    if not reader.exists(run_yaml):
        return False

    data = reader.read_yaml(run_yaml)
    current = str(data.get("phase", "research"))
    current_order = PHASE_ORDER.get(current, 0)
    new_order = PHASE_ORDER.get(new_phase.value, 0)

    if new_order <= current_order:
        logger.warning(
            "phase_transition_invalid",
            run_path=str(run_path),
            from_phase=current,
            to_phase=new_phase.value,
            reason="not_forward",
        )
        return False  # Forward-only: don't revert

    # Enforce exit criteria for the phase being LEFT before committing the
    # write. In strict mode an unmet gate raises StateError (blocks the
    # transition); in lenient (default) it only warns. Tier-skipped phases
    # are not enforced. (Activates the previously-inert phase gate.)
    _enforce_exit_gate(run_path, current, data)

    data["phase"] = new_phase.value
    writer.write_yaml(run_yaml, data)
    logger.info("phase_updated", run_path=str(run_path), old=current, new=new_phase.value)

    # F13: Mirror the committed phase into CeremonyState.phase so the status
    # line and phase-aware nudge dedup track the real phase (instead of the
    # permanent 'early' default). Done AFTER the run.yaml write succeeds.
    _sync_ceremony_phase(new_phase)

    # Log phase_enter event (best-effort)
    phase_event: dict[str, object] = {
        "phase": new_phase.value,
        "previous_phase": current,
    }
    events_path = run_path / "meta" / "events.jsonl"
    if events_path.parent.exists():
        try:
            event_logger.log_event(events_path, "phase_enter", phase_event)
        except (OSError, StateError):
            logger.debug("phase_event_log_failed", phase=new_phase.value)

    # Route phase transition to the telemetry pipeline so the backend
    # can track phase progression across all sessions.
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        TelemetryPipeline.get_instance().enqueue(
            {
                "event_type": "phase_transition",
                "phase": new_phase.value,
                "previous_phase": current,
            }
        )
    except Exception:  # justified: fail-open, pipeline may not be initialized
        logger.debug(
            "phase_transition_telemetry_skipped", exc_info=True
        )  # justified: fail-open, pipeline may not be initialized

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
    except Exception:  # justified: boundary, best-effort wrapper never raises
        logger.debug("try_update_phase_failed", phase=phase.value, exc_info=True)
