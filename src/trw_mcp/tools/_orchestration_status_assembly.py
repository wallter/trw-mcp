"""trw_status result assembly — run summary, reversions, gate readiness.

Belongs to the ``orchestration.py`` facade. Extracted for module-size
compliance (350 effective-LOC gate); behavior is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import TrwStatusDict
from trw_mcp.state._decision_queue import blocked_decision_block
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._orchestration_gate_scan import (
    apply_deliver_gate_status as _apply_deliver_gate_status,
)
from trw_mcp.tools._orchestration_lifecycle import (
    _compute_last_activity_ts,
    _compute_reflection_metrics,
    _phase_duration_summary,
)
from trw_mcp.tools._orchestration_phase import _check_framework_version_staleness
from trw_mcp.tools._orchestration_time import status_time_block
from trw_mcp.tools._task_profile_observability import apply_task_profile_observability

logger = structlog.get_logger(__name__)

#: PRD-CORE-305-FR06 (B80-37), sol round-1 P1. EXPLICIT classification for
#: EVERY field name ``trw_status``'s default response can carry -- "run"
#: (this run's own run.yaml/events.jsonl/meta) or "project" (a
#: value that looks past the current run: a scan across every run
#: (``stale_count*``), the deployed framework version rather than this run's
#: recorded one (``version_warning``), a formation board built from sibling
#: runs' own directories (``formation*``), project config plus session-wide
#: ``ceremony_state.json`` rather than THIS run's own state
#: (``build_gate_ready``/``review_gate_ready``/``deliver_gate_summary`` --
#: see ``_orchestration_gate_scan.py``'s ``_build_gate_ready``/
#: ``_review_gate_ready``), or project-session/learnings-derived progress
#: (``ceremony_status``/``nudge_content``, attached AFTER this module runs by
#: ``_apply_ceremony_status``, shared by every tool -- ``ceremony_status``
#: already says so inline: "scope=project_aggregate (not current-run
#: evidence)")).
#:
#: DELIBERATELY POSITIVE, not a "project" set with an implicit "everything
#: else is run" fallback (round-1's gap): a key with NO entry here is
#: UNCLASSIFIED, not silently "run". ``tests/test_status_field_scope.py``
#: asserts this table covers every ``TrwStatusDict`` field plus
#: ``ceremony_status``/``nudge_content``, so a new field added without a
#: matching entry fails that test rather than defaulting silently.
_FIELD_SCOPE: dict[str, str] = {
    "run_id": "run",
    "task": "run",
    "phase": "run",
    "status": "run",
    "confidence": "run",
    "framework": "run",
    "task_type": "run",
    "capability_tier": "run",
    "recommended_effort": "run",
    "effort_source": "run",
    "effort_adapter_status": "run",
    "nudge_pool_weights": "run",
    "recall_policy": "run",
    "event_count": "run",
    "reflection": "run",
    "phase_durations": "run",
    "last_activity_ts": "run",
    "hours_since_activity": "run",
    # PRD-CORE-338-FR05: elapsed/forecast/drift from THIS run's own events.
    "time": "run",
    # This label describes the CALL that produced it, not a cross-run
    # aggregate -- run-scoped, and included so it covers its own self-scope.
    "field_scope": "run",
    "version_warning": "project",
    "stale_count": "project",
    "stale_runs_advisory": "project",
    "stale_count_error": "project",
    "build_gate_ready": "project",
    "review_gate_ready": "project",
    "deliver_gate_summary": "project",
    "formation": "project",
    "formation_error": "project",
    # PRD-CORE-311-FR08: the same project-wide sync-push coordinator state
    # `sync_health` surfaces in `trw-mcp doctor` -- not this run's own state.
    "sync_push": "project",
    "ceremony_status": "project",
    "nudge_content": "project",
    # PRD-CORE-329-FR02: reads the project-level decision queue, never
    # THIS run's own state.
    "blocked_decision": "project",
    "blocked_decisions_pending": "project",
}


def field_scope_label(result: dict[str, object]) -> dict[str, object]:
    """Compact source label for *result*'s own keys.

    Lists only the MINORITY "project" keys by name (a value that looks past
    the current run); every other CLASSIFIED present key is run-scoped, per
    the ``note`` -- repeating the full field list on every default
    ``trw_status`` call would cost real tokens for no signal a caller acts
    on (the response-token-budget rule). A key with no entry in
    :data:`_FIELD_SCOPE` is reported under ``unclassified`` instead of being
    folded silently into "run" (the round-1 gap this closes) or implied by a
    ``note`` that would otherwise (round-2) overclaim coverage of a field
    nobody has actually classified; omitted when empty, per the same budget
    rule.
    """
    project_fields: list[str] = []
    unclassified: list[str] = []
    for key in result:
        scope = _FIELD_SCOPE.get(key)
        if scope == "project":
            project_fields.append(key)
        elif scope != "run":
            unclassified.append(key)
    label: dict[str, object] = {
        "project": sorted(project_fields),
        "note": "every other CLASSIFIED field is run-scoped (current-run evidence)",
    }
    if unclassified:
        label["unclassified"] = sorted(unclassified)
    return label


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
    MUST NOT import or ingest any member's learnings or handoff text. A
    delegated agent's memory is untrusted data under ``docs/CONSTITUTION.md``,
    so promoting it into the orchestrator's knowledge base because two runs
    share a manifest would launder unverified content into the project.

    One explicit amendment (PRD-CORE-322 FR07/NFR03): ``handoffs`` DISPLAYS each
    open handoff's ``next_read`` pointer, read from the mailbox opened ``mode=ro``,
    capped at 20 rows (``handoffs_omitted`` counts the rest). ``next_read`` is
    UNTRUSTED PEER DATA -- bounded and line-safe (NFR02), shown verbatim, never
    followed, interpreted, or ingested into any memory store.

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
        "stalls": [finding.as_dict() for finding in board.stalls],
        "stall_measurement": board.stall_measurement,
        "activity_measurement": board.activity_measurement,
        "stall_scope": board.stall_scope,
        "handoffs": list(board.handoffs),
        "handoffs_omitted": board.handoffs_omitted,
        "handoff_measurement": board.handoff_measurement,
    }


def _apply_blocked_decision(result: TrwStatusDict) -> None:
    """Surface the oldest pending decision first (FR02), or nothing at all (NFR01).

    An unreadable queue (FR05) still surfaces a truthful, minimal
    ``{"status": "unreadable"}`` block rather than omitting the key — omission
    would read as "no pending decision", which is exactly the fallback defect
    this PRD exists to avoid.
    """
    block, pending_count = blocked_decision_block(resolve_trw_dir())
    if block is None:
        return
    result["blocked_decision"] = block
    if pending_count > 1:
        result["blocked_decisions_pending"] = pending_count


def _apply_sync_push_field(result: TrwStatusDict) -> None:
    """Attach ``sync_push`` only when the sync-push read is degraded (FR08).

    Reuses ``step_sync_health`` verbatim -- the same read `sync_health`
    (`trw-mcp doctor`'s FR07 row) performs -- so this is a new SURFACE, never
    new detection. ``step_sync_health`` does one local JSON read, so this stays
    cheap on the hot `trw_status` path.

    Deliberately OMITTED on both a healthy read AND a
    :data:`NOT_MEASURED <trw_mcp.tools._sync_health.NOT_MEASURED>` read (state
    file absent/unreadable): NOT_MEASURED is the default state for the common
    case where a project has never configured sync at all, so surfacing it on
    every ``trw_status`` call for nearly every install would violate the
    response-token-budget rule (a field paid on every call must carry signal
    a caller acts on) for a value that names "nothing was observed", not a
    failure. Only a genuine degraded push -- an active feature reporting
    failure -- earns the token cost. Fail-open: any read error leaves the
    field absent rather than breaking `trw_status`.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.tools._sync_health import step_sync_health

        health = step_sync_health(resolve_trw_dir(), get_config())
    except Exception as exc:  # justified: fail-open, sync_push is advisory only
        logger.warning("sync_push_status_check_failed", reason=type(exc).__name__, exc_info=True)
        return
    if not bool(health.get("degraded")):
        return
    result["sync_push"] = {
        "status": "degraded",
        "consecutive_failures": health.get("consecutive_failures", 0),
        "last_push_at": health.get("last_push_at"),
        "advisory": str(health.get("advisory") or ""),
    }
    if health.get("rejected"):
        result["sync_push"]["rejected"] = health["rejected"]


def _apply_nudge_pool_weights(result: TrwStatusDict, state_data: dict[str, object]) -> None:
    """Attach ``nudge_pool_weights`` when a run task profile or project override exists."""
    from trw_mcp.models.config import get_config
    from trw_mcp.models.task_profile import run_task_profile_pool_weights

    cfg = get_config()
    if cfg.nudge_pool_weights is None and not isinstance(state_data.get("task_profile"), dict):
        return
    effective = cfg.effective_nudge_pool_weights(run_task_profile_pool_weights(state_data))
    result["nudge_pool_weights"] = effective.model_dump()


def assemble_status_result(
    state_data: dict[str, object],
    events: list[dict[str, object]],
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

    # PRD-CORE-329-FR02: inserted immediately after the run-identity fields
    # above and before every other advisory/scope-classified field below.
    _apply_blocked_decision(result)

    # PRD-CORE-184-FR04 / PRD-CORE-335-FR04: surface the EFFECTIVE nudge pool
    # weights -- the same resolution select_pool routes by -- so operators (and
    # eval stratification) observe the active policy, not a disconnected tuple.
    task_profile_data = state_data.get("task_profile")
    _apply_nudge_pool_weights(result, state_data)
    if isinstance(task_profile_data, dict):
        recall_policy = task_profile_data.get("recall_policy")
        if recall_policy:
            result["recall_policy"] = str(recall_policy)
        apply_task_profile_observability(cast("dict[str, object]", result), task_profile_data)
    result["phase_durations"] = _phase_duration_summary(events, result["phase"])

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

    # PRD-CORE-338-FR05: tracked runs only; an untracked run gains no key (NFR02).
    try:
        time_block = status_time_block(state_data, events, meta_path / "events.jsonl")
    except Exception:  # justified: fail-open, the time block is advisory and must not break status
        logger.info("status_time_block_degraded", exc_info=True)
        time_block = None
    if time_block is not None:
        result["time"] = time_block

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

    _apply_sync_push_field(result)

    return result
