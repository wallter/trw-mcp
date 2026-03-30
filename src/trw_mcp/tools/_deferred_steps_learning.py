"""Learning and knowledge deferred delivery steps.

Sub-module of ``_deferred_delivery`` — contains steps for learning
publishing, outcome correlation, recall tracking, trust increment,
index sync, and auto-progress.

Test patches should still target the parent facade:
``patch("trw_mcp.tools._deferred_delivery._step_publish_learnings")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import (
    AutoProgressStepResult,
    IndexSyncResult,
    OutcomeCorrelationStepResult,
    PublishLearningsResult,
    RecallOutcomeStepResult,
    TrustIncrementResult,
)

# PRD-FIX-061-FR02: Canonical definition moved to state/_session_events.py.
# Re-exported here for backward compatibility with existing consumers and tests.
from trw_mcp.state._session_events import _merge_session_events as _merge_session_events
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _step_publish_learnings() -> PublishLearningsResult:
    """Step 6: Publish high-impact learnings to platform backend."""
    from trw_mcp.telemetry.publisher import publish_learnings

    return cast("PublishLearningsResult", dict(publish_learnings()))


def _step_outcome_correlation() -> OutcomeCorrelationStepResult:
    """Step 6.5: Outcome correlation (G1)."""
    from trw_mcp.scoring import process_outcome_for_event

    outcome_ids = process_outcome_for_event("trw_deliver_complete")
    return {"status": "success", "updated": len(outcome_ids)}


def _step_recall_outcome(resolved_run: Path | None) -> RecallOutcomeStepResult:
    """Step 6.6: Recall outcome tracking (G6)."""
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.state.recall_tracking import get_recall_stats, record_outcome

    recall_stats = get_recall_stats()
    unique_ids = recall_stats.get("unique_learnings", 0)
    recalled_count = 0
    if unique_ids and resolved_run is not None:
        trw_dir_rt = resolve_trw_dir()
        tracking_path = trw_dir_rt / "logs" / "recall_tracking.jsonl"
        if tracking_path.exists():
            from trw_mcp.state.persistence import FileStateReader as _FSR

            rt_reader = _FSR()
            records_rt = rt_reader.read_jsonl(tracking_path)
            seen: set[str] = set()
            for rec in records_rt:
                lid = str(rec.get("learning_id", ""))
                if lid and rec.get("outcome") is None and lid not in seen:
                    record_outcome(lid, "positive")
                    seen.add(lid)
                    recalled_count += 1
    return {"status": "success", "recorded": recalled_count}


def _step_trust_increment(resolved_run: Path | None) -> TrustIncrementResult | None:
    """Step 9: Trust session increment (CORE-068-FR05).

    FR02 (PRD-FIX-053): Relaxed gate -- fires when EITHER:
      (a) build_check passed (existing behavior), OR
      (b) session has >= 3 learnings AND >= 1 checkpoint (productive_session).

    Also reads {trw_dir}/context/session-events.jsonl for events that landed
    before trw_init created the run directory (same pattern as ceremony scoring).
    """
    try:
        import os

        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.trust import increment_session_count

        reader = FileStateReader()

        # Collect all events: run-level events.jsonl + session-level session-events.jsonl
        run_events: list[dict[str, object]] = []
        events_path = resolved_run / "meta" / "events.jsonl" if resolved_run else None
        if events_path and events_path.exists():
            run_events.extend(reader.read_jsonl(events_path))

        trw_dir = resolve_trw_dir()
        all_events = _merge_session_events(run_events, trw_dir)

        # Check path (a): build_check passed
        build_passed = False
        for ev in all_events:
            ev_type = str(ev.get("event", ""))
            ev_data = ev.get("data", {})
            if not isinstance(ev_data, dict):
                ev_data = {}
            tool_name = str(ev_data.get("tool_name", ""))
            if (ev_type == "build_check_complete" or tool_name == "trw_build_check") and (
                ev_data.get("result") == "pass" or ev_data.get("build_passed") is True
            ):
                build_passed = True
                break

        if build_passed:
            agent_id = os.environ.get("TRW_AGENT_ID", "unknown")
            result = increment_session_count(trw_dir, agent_id)
            # Preserve existing return shape; add reason for observability
            if isinstance(result, dict) and "reason" not in result:
                result["reason"] = "build_check_passed"
            return cast("TrustIncrementResult", result)

        # Check path (b): productive session (>= 3 learnings AND >= 1 checkpoint)
        learn_count = 0
        checkpoint_count = 0
        for ev in all_events:
            ev_type = str(ev.get("event", ""))
            ev_data = ev.get("data", {})
            if not isinstance(ev_data, dict):
                ev_data = {}
            tool_name = str(ev_data.get("tool_name", ""))
            is_tool_invocation = ev_type == "tool_invocation"

            # Count learn events
            if ev_type in ("learn", "trw_learn") or (is_tool_invocation and "trw_learn" in tool_name):
                learn_count += 1

            # Count checkpoint events
            if ev_type in ("checkpoint", "trw_checkpoint") or (is_tool_invocation and "trw_checkpoint" in tool_name):
                checkpoint_count += 1

        # Thresholds: >= 3 learnings AND >= 1 checkpoint
        if learn_count >= 3 and checkpoint_count >= 1:
            agent_id = os.environ.get("TRW_AGENT_ID", "unknown")
            result = increment_session_count(trw_dir, agent_id)
            if isinstance(result, dict):
                result["reason"] = "productive_session"
            return cast("TrustIncrementResult", result)

        return {"skipped": True, "reason": "insufficient_session_activity"}

    except Exception as exc:  # justified: fail-open, session count increment is best-effort
        return {"skipped": True, "reason": str(exc)}


def _do_index_sync() -> IndexSyncResult:
    """Execute INDEX.md and ROADMAP.md sync from PRD frontmatter."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md
    from trw_mcp.state.persistence import FileStateWriter

    config = get_config()
    writer = FileStateWriter()
    project_root = resolve_project_root()
    prds_dir = project_root / Path(config.prds_relative_path)
    aare_dir = prds_dir.parent

    index_result = sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=writer)
    roadmap_result = sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=writer)

    return IndexSyncResult(
        status="success",
        index=cast("dict[str, object]", index_result),
        roadmap=cast("dict[str, object]", roadmap_result),
    )


def _do_auto_progress(run_dir: Path | None) -> AutoProgressStepResult:
    """Auto-progress PRD statuses for the deliver phase.

    Calls ``auto_progress_prds`` with phase="deliver" for all PRDs
    in the run's ``prd_scope``. Skipped if no active run.

    Note: ``resolve_project_root`` and ``get_config`` are resolved from
    the ceremony module at call time so that test patches on
    ``trw_mcp.tools.ceremony.*`` propagate correctly.
    """
    if run_dir is None:
        return {"status": "skipped", "reason": "no_active_run"}

    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.validation import auto_progress_prds

    config = get_config()
    project_root = resolve_project_root()
    prds_dir = project_root / Path(config.prds_relative_path)
    if not prds_dir.is_dir():
        return {"status": "skipped", "reason": "prds_dir_not_found"}

    progressions = auto_progress_prds(run_dir, "deliver", prds_dir, config)

    return {
        "status": "success",
        "total_evaluated": len(progressions),
        "applied": sum(1 for p in progressions if p.get("applied")),
        "progressions": progressions,
    }


def _step_auto_progress(resolved_run: Path | None) -> AutoProgressStepResult:
    """Step 5: Auto-progress PRD statuses."""
    return _do_auto_progress(resolved_run)
