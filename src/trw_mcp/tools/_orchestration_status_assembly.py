"""trw_status result assembly — run summary, waves, reversions, gate readiness.

Belongs to the ``orchestration.py`` facade. Extracted for module-size
compliance (350 effective-LOC gate); behavior is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import TrwStatusDict, WriterPressureDict
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._orchestration_gate_scan import (
    apply_deliver_gate_status as _apply_deliver_gate_status,
)
from trw_mcp.tools._orchestration_lifecycle import (
    _compute_last_activity_ts,
    _compute_reflection_metrics,
    _phase_duration_summary,
)
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness,
    _compute_reversion_metrics,
    _compute_wave_progress,
)
from trw_mcp.tools._task_profile_observability import apply_task_profile_observability

logger = structlog.get_logger(__name__)


def _stale_close_remedy() -> str:
    """Return the stale-run remedy clause, or none when auto-close is off.

    The advisory used to state flatly that ``trw_session_start`` would
    "auto-close them". ``_ceremony_helpers`` only runs ``auto_close_stale_runs``
    when ``config.run_auto_close_enabled`` is set, so with it disabled the
    sentence directed the agent to a call that would demonstrably not do the
    thing it promised — a consequence the config does not enforce (HB-1).

    The count itself is always surfaced; only the REMEDY is conditional, so
    turning auto-close off makes TRW quieter about the fix, never blind to the
    staleness. Fail-open to no clause: an unreadable config cannot prove the
    remedy applies, and an unproven remedy must not be asserted.
    """
    try:
        from trw_mcp.models.config import get_config

        if get_config().run_auto_close_enabled:
            return " Use trw_session_start to auto-close them."
    # INFO, not debug: a default install passes no --debug, where debug events are
    # dropped before any processor runs — and the caller cannot tell a suppressed
    # remedy (auto-close off) from an unresolvable one (config unreadable).
    except Exception:  # justified: fail-open, advisory wording must not break status
        logger.info("stale_close_remedy_degraded", exc_info=True)
    return ""


def _apply_formation_block(result: TrwStatusDict, resolved_path: Path) -> None:
    """Attach the read-only formation board when one is active (FR07).

    STRICTLY READ-ONLY, AND DELIBERATELY MEMORY-BLIND. The rows are derived from
    each member's OWN run directory; nothing here opens a memory store, and it
    MUST NOT read, import, or ingest any member's learnings or handoff text. A
    delegated agent's memory is untrusted data under ``docs/CONSTITUTION.md``,
    so promoting it into the orchestrator's knowledge base because two runs
    share a manifest would launder unverified content into the project.

    Absence and breakage are different outcomes: no formation omits the block
    entirely, while an unreadable or invalid manifest sets ``formation_error``
    naming the file and the parse error (NFR02). Neither ever fails the status
    call — a status check that crashed on a coordination artifact would be a
    worse outage than the missing board.
    """
    try:
        from trw_mcp.formation import FormationError
        from trw_mcp.formation import status as formation_status

        board = formation_status(run_path=resolved_path)
    except FormationError as exc:
        result["formation_error"] = str(exc)
        logger.info("formation_status_error", run=str(resolved_path), reason=str(exc))
        return
    except Exception as exc:  # fail-open: the board is observability
        logger.warning("formation_status_degraded", run=str(resolved_path), reason=str(exc), exc_info=True)
        return
    if board is None:
        return
    result["formation"] = {
        "formation_id": board.formation_id,
        "revision": board.revision,
        "manifest_path": board.manifest_path,
        "members": [row.as_dict() for row in board.rows],
        "non_terminal": [
            {"member_id": member, "status": member_status} for member, member_status in board.non_terminal
        ],
    }


def _degraded_writer_pressure_block() -> WriterPressureDict:
    """The block ``trw_status`` must ALWAYS carry, typed as degraded.

    PRD-CORE-257 audit row 5: the field is ``Required`` on ``TrwStatusDict``,
    so any exception building the real block — config/path resolution,
    census, ledger projection, or conversion — must still leave the key
    present rather than silently omitted. A caller reading ``under_pressure``
    without reading ``census_state``/``ledger_state`` is reading an unsafe
    default either way, so this block reports the least-trusting values.
    """
    return WriterPressureDict(
        writer_count=0,
        peer_writer_count=0,
        threshold=0,
        under_pressure=False,
        census_state="unreadable",
        ledger_state="degraded",
        heartbeat_state="unavailable",
        identity_state="unverified",
        deferred_steps={},
    )


def _writer_pressure_block() -> WriterPressureDict:
    """Measure writer pressure and project the deferral ledger (PRD-CORE-257-FR05).

    Resolved inside the function rather than added to the signature so every
    existing caller and monkeypatch keeps working. Fail-open, but never
    fail-quiet: a registry that cannot be scanned reports
    ``census_state="unreadable"`` with counts held at 0 for shape stability and
    logs at WARNING, so a consumer can tell "nothing measured" from "measured
    nothing". The counts are shape padding, not evidence.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.state.deferral_ledger import deferred_steps_summary
    from trw_mcp.state.memory_pressure import take_writer_census

    config = get_config()
    trw_dir = resolve_trw_dir()
    census = take_writer_census(
        trw_dir,
        threshold=config.session_start_writer_pressure_threshold,
        pin_ttl_hours=config.pin_ttl_hours,
    )
    if census.census_state == "unreadable":
        logger.warning("writer_census_unreadable", surface="trw_status", threshold=census.threshold)
    summary, ledger_state = deferred_steps_summary(trw_dir)
    return WriterPressureDict(
        writer_count=census.writer_count,
        peer_writer_count=census.peer_writer_count,
        threshold=census.threshold,
        under_pressure=census.under_pressure,
        census_state=census.census_state,
        ledger_state=ledger_state,
        heartbeat_state=census.heartbeat_state,
        identity_state=census.identity_state,
        deferred_steps={
            step: {"age_hours": float(values["age_hours"]), "deferred_count": int(values["deferred_count"])}
            for step, values in summary.items()
        },
    )


def assemble_status_result(
    state_data: dict[str, object],
    events: list[dict[str, object]],
    wave_data: dict[str, object],
    resolved_path: Path,
    reader: FileStateReader,
    meta_path: Path,
) -> TrwStatusDict:
    """Build the trw_status response payload from already-read run state."""
    result: TrwStatusDict = {
        # Placeholder for the ``Required`` key below; overwritten unconditionally
        # (with a real or degraded value) before this function returns.
        "writer_pressure": _degraded_writer_pressure_block(),
        "run_id": str(state_data.get("run_id", "unknown")),
        "task": str(state_data.get("task", "unknown")),
        "phase": str(state_data.get("phase", "unknown")),
        "status": str(state_data.get("status", "unknown")),
        "confidence": str(state_data.get("confidence", "unknown")),
        "framework": str(state_data.get("framework", "unknown")),
        # PRD-CORE-184-FR05: surface task_type in the run summary block.
        "task_type": str(state_data.get("task_type", "unknown")),
        "event_count": len(events),
        "reflection": _compute_reflection_metrics(events),
    }

    # PRD-CORE-184-FR04: surface effective per-task-type nudge pool weights
    # so operators (and eval stratification) can observe the active policy.
    task_profile_data = state_data.get("task_profile")
    if isinstance(task_profile_data, dict):
        weights = task_profile_data.get("nudge_pool_weights")
        if isinstance(weights, (list, tuple)) and len(weights) == 4:
            result["nudge_pool_weights"] = {
                "workflow": int(weights[0]),
                "learnings": int(weights[1]),
                "ceremony": int(weights[2]),
                "context": int(weights[3]),
            }
        recall_policy = task_profile_data.get("recall_policy")
        if recall_policy:
            result["recall_policy"] = str(recall_policy)
        apply_task_profile_observability(cast("dict[str, object]", result), task_profile_data)
    result["phase_durations"] = _phase_duration_summary(events, result["phase"])

    if wave_data:
        raw_waves = wave_data.get("waves", [])
        result["waves"] = raw_waves if isinstance(raw_waves, list) else []

        wave_progress = _compute_wave_progress(
            wave_data,
            resolved_path,
        )
        if wave_progress:
            result["wave_progress"] = wave_progress

    wave_status = state_data.get("wave_status")
    if isinstance(wave_status, dict) and wave_status:
        result["wave_status"] = wave_status

    reversion_metrics = _compute_reversion_metrics(events)
    # Compact the healthy/no-revert case: drop the empty ``by_trigger`` dict
    # and the ``latest: null`` field, which are pure null-noise re-emitted on
    # every status check for the life of the run. ``count``/``rate``/
    # ``classification`` stay unconditional (callers/tests depend on them).
    if not reversion_metrics.get("by_trigger"):
        reversion_metrics.pop("by_trigger", None)
    if reversion_metrics.get("latest") is None:
        reversion_metrics.pop("latest", None)
    result["reversions"] = reversion_metrics

    # PRD-QUAL-105: surface deliver-gate readiness at status-check time so an
    # agent can answer "can I deliver now?" without a deliver-then-fail-then-
    # retry cycle. Reuses the already-read ``events`` list (FR01 build gate)
    # plus ceremony_state.json (FR02 review gate). Fail-open per FR04 inside
    # the helper — the three fields are simply omitted on any scan error.
    _apply_deliver_gate_status(cast("dict[str, object]", result), events, resolved_path)

    _apply_formation_block(result, resolved_path)

    last_ts, hours_since = _compute_last_activity_ts(reader, meta_path, events)
    if last_ts:
        result["last_activity_ts"] = last_ts
    if hours_since is not None:
        result["hours_since_activity"] = hours_since

    version_warning = _check_framework_version_staleness(
        str(state_data.get("framework", "")),
    )
    if version_warning:
        result["version_warning"] = version_warning

    try:
        # Resolve via the orchestration facade so existing test monkeypatches
        # on ``trw_mcp.tools.orchestration.count_stale_runs`` keep working.
        from trw_mcp.tools import orchestration as _orch

        stale = _orch.count_stale_runs()
        result["stale_count"] = stale
        # Prose hint is one-time-useful; after it has been surfaced once for
        # this run, later status checks carry only the bare ``stale_count``.
        if stale > 0 and _orch.stale_advisory_first_time(resolved_path):
            result["stale_runs_advisory"] = f"{stale} stale run(s) detected.{_stale_close_remedy()}"
    except Exception:  # justified: fail-open, stale run count is advisory only
        result["stale_count_error"] = True
        logger.warning("stale_count_scan_failed", exc_info=True)

    try:
        result["writer_pressure"] = _writer_pressure_block()
    except Exception:  # justified: fail-open, the pressure block must not break status
        logger.warning("writer_pressure_block_failed", exc_info=True)
        # Audit row 5: omitting the key entirely (the old behavior) violates
        # the ``Required`` status contract — a caller cannot distinguish
        # "healthy, no pressure" from "the whole block crashed" if both look
        # like an absent key. Degraded-but-present beats silently missing.
        result["writer_pressure"] = _degraded_writer_pressure_block()

    return result
