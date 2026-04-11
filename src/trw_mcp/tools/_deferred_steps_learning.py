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
    ReworkMetricsResult,
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


# ---------------------------------------------------------------------------
# Sprint 84: Delivery metrics (PRD-CORE-104)
# ---------------------------------------------------------------------------


def _read_run_events(resolved_run: Path | None) -> list[dict[str, object]]:
    """Read events.jsonl from a resolved run directory (fail-open)."""
    if resolved_run is None:
        return []
    events_path = resolved_run / "meta" / "events.jsonl"
    if not events_path.exists():
        return []
    try:
        reader = FileStateReader()
        return reader.read_jsonl(events_path)
    except Exception:  # justified: fail-open
        return []


def _step_delivery_metrics(trw_dir: Path, resolved_run: Path | None) -> dict[str, object]:
    """Step 12: Compute delivery metrics — rework rate, composite outcome, reward.

    PRD-CORE-104: Produces reward signals at deliver time by aggregating
    rework_rate, composite_outcome, learning_exposure, and normalized_reward.

    Fail-open: returns partial results if individual metrics fail.
    """
    result: dict[str, object] = {"status": "success"}

    # Rework rate (PRD-CORE-103-FR04) — based on git modified files
    try:
        import subprocess

        from trw_mcp.scoring.rework_rate import compute_rework_rate

        project_root = trw_dir.parent if trw_dir.name == ".trw" else trw_dir
        git_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],  # noqa: S607
            capture_output=True, text=True, timeout=5,
            cwd=str(project_root),
        )
        changed = [f.strip() for f in git_result.stdout.strip().split("\n") if f.strip()]
        rework = compute_rework_rate(changed, project_root=str(project_root))
        result["rework_rate"] = rework
    except Exception:  # justified: fail-open, individual metric failure
        logger.debug("delivery_metric_rework_rate_failed", exc_info=True)
        result["rework_rate"] = {"error": "computation_failed"}

    # Composite outcome score (PRD-CORE-104-FR01)
    try:
        from trw_mcp.scoring._correlation import compute_composite_outcome

        rework_val = 0.0
        rw = result.get("rework_rate")
        if isinstance(rw, dict) and "rework_rate" in rw:
            rework_val = float(rw["rework_rate"])

        # PRD-CORE-104 P0: Compute all 4 inputs for composite outcome
        p0_count = 0
        velocity_tasks = 0
        learning_count = 0
        session_events: list[dict[str, object]] = []
        try:
            session_events = _read_run_events(resolved_run)
            for evt in session_events:
                evt_type = str(evt.get("event", ""))
                evt_data = evt.get("data", {})
                if not isinstance(evt_data, dict):
                    evt_data = {}
                if evt_type == "review_complete":
                    p0_count += int(evt_data.get("critical_count", 0))
                elif evt_type in ("phase_gate_passed", "checkpoint"):
                    velocity_tasks += 1
                elif evt_type in ("learn", "trw_learn"):
                    learning_count += 1
                elif evt_type == "tool_invocation":
                    tool_name = str(evt_data.get("tool_name", ""))
                    if "trw_learn" in tool_name:
                        learning_count += 1
        except Exception:  # justified: fail-open, event scan is best-effort
            logger.debug("delivery_metric_event_scan_failed", exc_info=True)

        # Compute learning rate (learnings per hour, rough estimate)
        session_hours = max(0.1, len(session_events) / 60.0)
        learning_rate = learning_count / session_hours

        from trw_mcp.models.config import get_config as _get_cfg

        _cfg = _get_cfg()
        composite = compute_composite_outcome(
            rework_rate=rework_val,
            p0_defect_count=p0_count,
            velocity_tasks=velocity_tasks,
            learning_rate=learning_rate,
            weight_rework=getattr(_cfg, "outcome_weight_rework", -2.0),
            weight_p0_defects=getattr(_cfg, "outcome_weight_p0_defects", -1.5),
            weight_velocity=getattr(_cfg, "outcome_weight_velocity", 0.5),
            weight_learning_rate=getattr(_cfg, "outcome_weight_learning_rate", 0.3),
        )
        result["composite_outcome"] = composite
    except Exception:  # justified: fail-open
        logger.debug("delivery_metric_composite_outcome_failed", exc_info=True)
        result["composite_outcome"] = {"error": "computation_failed"}

    # Proximal reward detection (PRD-CORE-104-FR02) — from run events
    try:
        from trw_mcp.scoring.proximal_reward import detect_proximal_signals

        events: list[dict[str, object]] = []
        if resolved_run is not None:
            events_path = resolved_run / "meta" / "events.jsonl"
            if events_path.exists():
                reader = FileStateReader()
                events = reader.read_jsonl(events_path)
        signals = detect_proximal_signals(events)
        result["proximal_signals"] = [dict(s) for s in signals]
    except Exception:  # justified: fail-open
        logger.debug("delivery_metric_proximal_signals_failed", exc_info=True)
        result["proximal_signals"] = []

    # Learning exposure (recall pull rate from surface tracking)
    try:
        from trw_mcp.state.surface_tracking import compute_recall_pull_rate

        pull_rate, nudge_count = compute_recall_pull_rate(trw_dir)
        result["learning_exposure"] = {
            "recall_pull_rate": round(pull_rate, 4),
            "nudge_count": nudge_count,
        }
    except Exception:  # justified: fail-open
        logger.debug("delivery_metric_learning_exposure_failed", exc_info=True)
        result["learning_exposure"] = {"error": "computation_failed"}

    # PRD-CORE-104 P0: Safe default for normalized_reward before computation
    result["normalized_reward"] = 0.5

    # Normalized reward (sigmoid of composite outcome)
    try:
        from trw_mcp.scoring._correlation import sigmoid_normalize

        composite_val = result.get("composite_outcome")
        if isinstance(composite_val, dict) and "score" in composite_val:
            raw_score = float(composite_val["score"])
            result["normalized_reward"] = round(sigmoid_normalize(raw_score), 4)
        elif isinstance(composite_val, (int, float)):
            result["normalized_reward"] = round(sigmoid_normalize(float(composite_val)), 4)
    except Exception:  # justified: fail-open
        logger.debug("delivery_metric_normalized_reward_failed", exc_info=True)

    # PRD-CORE-104 P0: Add client_profile and model_family to session metrics
    try:
        from trw_mcp.models.config import get_config

        cfg = get_config()
        result["client_profile"] = (
            cfg.client_profile.client_id
            if hasattr(cfg.client_profile, "client_id")
            else ""
        )
        result["model_family"] = getattr(cfg, "model_family", "") or ""
    except Exception:  # justified: fail-open, metadata enrichment is best-effort
        logger.debug("delivery_metadata_enrichment_failed", exc_info=True)

    logger.info(
        "delivery_metrics_computed",
        metrics=[k for k in result if k != "status"],
    )
    return result


# ---------------------------------------------------------------------------
# PRD-QUAL-056-FR09: Rework metrics for delivery report
# ---------------------------------------------------------------------------


def _step_collect_rework_metrics(
    run_path: Path | None,
    reader: FileStateReader,
) -> ReworkMetricsResult:
    """Collect audit rework metrics from events.jsonl for the delivery report.

    Scans events.jsonl for:
    - audit_cycle_complete events (counted per PRD ID)
    - First verdict per PRD determines first-pass compliance

    Returns dict with:
    - audit_cycles: dict mapping PRD ID to cycle count
    - first_pass_compliance: dict mapping PRD ID to bool
    - sprint_avg_audit_cycles: float average across all PRDs
    - sprint_first_pass_compliance_rate: float (0.0-1.0)
    """
    empty: ReworkMetricsResult = {
        "audit_cycles": {},
        "first_pass_compliance": {},
        "finding_categories": {},
        "sprint_avg_audit_cycles": 0.0,
        "sprint_first_pass_compliance_rate": 0.0,
    }

    if run_path is None:
        return empty

    events_path = run_path / "meta" / "events.jsonl"
    if not events_path.exists():
        return empty

    try:
        events = reader.read_jsonl(events_path)
    except Exception:  # justified: fail-open, metrics are best-effort
        logger.debug("rework_metrics_read_failed", exc_info=True)
        return empty

    # Track per-PRD: cycle count and first verdict
    audit_cycles: dict[str, int] = {}
    first_verdict: dict[str, str] = {}
    finding_categories: dict[str, int] = {}

    for ev in events:
        ev_type = str(ev.get("event", ""))
        if ev_type != "audit_cycle_complete":
            continue

        ev_data = _extract_event_data(ev)

        prd_id = str(ev_data.get("prd_id", ""))
        if not prd_id:
            continue

        verdict = str(ev_data.get("verdict", "")).upper()

        audit_cycles[prd_id] = audit_cycles.get(prd_id, 0) + 1
        for category in _extract_finding_categories(ev_data):
            finding_categories[category] = finding_categories.get(category, 0) + 1

        # Record first verdict for first-pass compliance
        if prd_id not in first_verdict:
            first_verdict[prd_id] = verdict

    if not audit_cycles:
        return empty

    # Compute first-pass compliance per PRD
    first_pass_compliance: dict[str, bool] = {
        prd_id: first_verdict.get(prd_id, "") == "PASS"
        for prd_id in audit_cycles
    }

    # Sprint-level aggregates
    total_cycles = sum(audit_cycles.values())
    prd_count = len(audit_cycles)
    sprint_avg = total_cycles / prd_count if prd_count > 0 else 0.0
    compliant_count = sum(1 for v in first_pass_compliance.values() if v)
    compliance_rate = compliant_count / prd_count if prd_count > 0 else 0.0

    return {
        "audit_cycles": audit_cycles,
        "first_pass_compliance": first_pass_compliance,
        "finding_categories": finding_categories,
        "sprint_avg_audit_cycles": sprint_avg,
        "sprint_first_pass_compliance_rate": compliance_rate,
    }


def _extract_event_data(event: dict[str, object]) -> dict[str, object]:
    """Return normalized event payload for flat or nested event records."""
    nested = event.get("data")
    if isinstance(nested, dict):
        return nested
    return event


def _extract_finding_categories(event_data: dict[str, object]) -> list[str]:
    """Extract normalized finding categories from an audit-cycle event payload."""
    candidates = event_data.get("finding_categories", event_data.get("categories"))
    if isinstance(candidates, dict):
        expanded: list[str] = []
        for category, count in candidates.items():
            try:
                repeats = max(int(str(count)), 0)
            except ValueError:
                repeats = 0
            expanded.extend([str(category)] * repeats)
        return expanded
    if isinstance(candidates, list):
        return [str(category) for category in candidates if str(category)]
    if isinstance(candidates, str) and candidates:
        return [candidates]

    findings = event_data.get("findings")
    if isinstance(findings, list):
        extracted = [
            str(finding.get("category", ""))
            for finding in findings
            if isinstance(finding, dict) and str(finding.get("category", ""))
        ]
        if extracted:
            return extracted

    category = str(event_data.get("finding_category", ""))
    return [category] if category else []
