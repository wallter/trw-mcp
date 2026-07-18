"""trw_status result assembly — run summary, waves, reversions, gate readiness.

Belongs to the ``orchestration.py`` facade. Extracted for module-size
compliance (350 effective-LOC gate); behavior is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import TrwStatusDict
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
            result["stale_runs_advisory"] = f"{stale} stale run(s) detected. Use trw_session_start to auto-close them."
    except Exception:  # justified: fail-open, stale run count is advisory only
        result["stale_count_error"] = True
        logger.warning("stale_count_scan_failed", exc_info=True)

    return result
