"""Telemetry and ceremony feedback deferred delivery steps.

Sub-module of ``_deferred_delivery`` — contains steps for telemetry
event recording, batch sending, ceremony feedback, and checkpointing.

Test patches should still target the parent facade:
``patch("trw_mcp.tools._deferred_delivery._step_telemetry")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import (
    BatchSendResult,
    CeremonyFeedbackStepResult,
    TelemetryStepResult,
)
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _step_checkpoint(resolved_run: Path) -> dict[str, object]:
    """Step 2: Delivery state snapshot."""
    from trw_mcp.tools.checkpoint import _do_checkpoint

    _do_checkpoint(resolved_run, "delivery")
    return {"status": "success"}


def _step_telemetry(resolved_run: Path | None) -> TelemetryStepResult:
    """Step 7: Telemetry events (G3 + G4)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_installation_id
    from trw_mcp.state.analytics.report import compute_ceremony_score
    from trw_mcp.telemetry.client import TelemetryClient
    from trw_mcp.telemetry.models import CeremonyComplianceEvent, SessionEndEvent

    # Drain the telemetry pipeline (flushes remaining tool events to backend).
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        TelemetryPipeline.get_instance().stop(drain=True, timeout=10.0)
    except Exception:  # justified: fail-open -- pipeline drain must not block delivery
        logger.debug("pipeline_drain_failed", exc_info=True)

    cfg = get_config()
    inst_id = resolve_installation_id()
    tel_client = TelemetryClient.from_config()

    # Read events for tool count and ceremony score computation
    events: list[dict[str, object]] = []
    if resolved_run is not None:
        ev_path = resolved_run / "meta" / "events.jsonl"
        if ev_path.exists():
            from trw_mcp.state.persistence import FileStateReader as _FSR2

            ev_reader = _FSR2()
            events = ev_reader.read_jsonl(ev_path)

    # FIX-051-FR05: Pass trw_dir so compute_ceremony_score can also read
    # session-events.jsonl (where trw_session_start events land before trw_init).
    from trw_mcp.state._paths import resolve_trw_dir
    trw_dir_for_score = resolve_trw_dir()
    profile_weights = cfg.client_profile.ceremony_weights
    ceremony = compute_ceremony_score(events, trw_dir=trw_dir_for_score, weights=profile_weights)
    ceremony_score = int(str(ceremony.get("score", 0)))

    tel_client.record_event(
        SessionEndEvent(
            installation_id=inst_id,
            framework_version=cfg.framework_version,
            tools_invoked=len(events),
            ceremony_score=ceremony_score,
        )
    )
    run_id_str = str(resolved_run.name) if resolved_run else "unknown"
    tel_client.record_event(
        CeremonyComplianceEvent(
            installation_id=inst_id,
            framework_version=cfg.framework_version,
            run_id=run_id_str,
            score=ceremony_score,
        )
    )
    tel_client.flush()

    # Write session summary to session-events.jsonl for trw_quality_dashboard
    from trw_mcp.state.persistence import (
        FileEventLogger,
    )
    from trw_mcp.state.persistence import (
        FileStateReader as _BSR,
    )
    from trw_mcp.state.persistence import (
        FileStateWriter as _FSW,
    )

    try:
        from trw_mcp.state._paths import resolve_trw_dir as _resolve_trw_dir
        trw_dir = _resolve_trw_dir()
        context_dir = trw_dir / cfg.context_dir

        # Read run state for task/phase info
        run_state: dict[str, object] = {}
        if resolved_run is not None:
            run_yaml = resolved_run / "meta" / "run.yaml"
            if run_yaml.exists():
                run_state = _BSR().read_yaml(run_yaml)

        summary_data: dict[str, object] = {
            "ceremony_score": ceremony_score,
            "task_name": str(run_state.get("task", "")) if run_state else "",
            "phase": str(run_state.get("phase", "")) if run_state else "",
        }

        # Include build results if available from build-status.yaml
        build_status_path = trw_dir / cfg.context_dir / "build-status.yaml"
        if build_status_path.exists():
            bs_data = _BSR().read_yaml(build_status_path)
            if bs_data:
                if "coverage_pct" in bs_data:
                    summary_data["coverage_pct"] = bs_data["coverage_pct"]
                if "tests_passed" in bs_data:
                    summary_data["tests_passed"] = bs_data["tests_passed"]
                if "mypy_clean" in bs_data:
                    summary_data["mypy_clean"] = bs_data["mypy_clean"]

        summary_writer = _FSW()
        summary_events = FileEventLogger(summary_writer)
        summary_events.log_event(
            context_dir / "session-events.jsonl",
            "session_summary",
            summary_data,
        )
    except Exception:  # justified: fail-open, lock release cleanup
        logger.debug("session_summary_write_failed", exc_info=True)

    return {"status": "success", "events": 2, "ceremony_score": ceremony_score}


def _step_batch_send() -> BatchSendResult:
    """Step 8: Batch send (G2)."""
    from trw_mcp.telemetry.sender import BatchSender

    return cast("BatchSendResult", dict(BatchSender.from_config().send()))


def _extract_ceremony_metrics(deliver_results: dict[str, object]) -> tuple[float, bool, float, int]:
    """Extract ceremony score, build pass, coverage delta, and critical findings."""
    # Ceremony score
    ceremony_score_val = deliver_results.get("telemetry", {})
    score = 0.0
    if isinstance(ceremony_score_val, dict):
        score = float(ceremony_score_val.get("ceremony_score", 0))

    # Build passed
    build_check_data = deliver_results.get("build_check", {})
    build_passed = False
    if isinstance(build_check_data, dict):
        build_passed = bool(build_check_data.get("build_passed", False)) or bool(
            build_check_data.get("tests_passed", False)
        )

    # Coverage delta
    coverage_delta = 0.0
    if isinstance(build_check_data, dict):
        raw_delta = build_check_data.get("coverage_delta")
        if raw_delta is not None:
            try:
                coverage_delta = float(str(raw_delta))
            except (ValueError, TypeError):
                coverage_delta = 0.0

    # Critical findings
    review_data = deliver_results.get("review", {})
    critical_findings = 0
    if isinstance(review_data, dict):
        raw_cf = review_data.get("critical_findings")
        if raw_cf is not None:
            try:
                critical_findings = int(str(raw_cf))
            except (ValueError, TypeError):
                critical_findings = 0

    return score, build_passed, coverage_delta, critical_findings


def _extract_run_metadata(resolved_run: Path | None) -> tuple[dict[str, object], str, str]:
    """Extract run.yaml metadata and task details."""
    run_state: dict[str, object] = {}
    task_name = ""
    task_description = ""

    if resolved_run is None:
        return run_state, task_name, task_description

    run_yaml = resolved_run / "meta" / "run.yaml"
    if run_yaml.exists():
        reader = FileStateReader()
        run_state = reader.read_yaml(run_yaml)

    # FIX-050-FR03 / FIX-051-FR02: RunState model uses field "task", not "task_name".
    task_name = str(run_state.get("task", ""))
    # FIX-051-FR06: Pass objective for improved classification accuracy.
    task_description = str(run_state.get("objective", ""))

    return run_state, task_name, task_description


def _process_ceremony_proposal(
    trw_dir: Path,
    task_class_str: str,
    proposal: dict[str, object],
) -> None:
    """Persist ceremony reduction proposal to disk."""
    from trw_mcp.state.ceremony_feedback import (
        _overrides_path,
        read_overrides,
        register_proposal,
    )
    from trw_mcp.state.persistence import FileStateWriter as _FSW

    register_proposal(proposal)
    # Persist pending proposals to ceremony-overrides.yaml so
    # trw_ceremony_status (on main thread) can read them on restart.
    overrides = read_overrides(trw_dir)
    pending_proposals = overrides.get("_pending_proposals", {})
    if not isinstance(pending_proposals, dict):
        pending_proposals = {}
    pid = str(proposal.get("proposal_id", ""))
    pending_proposals[pid] = proposal
    overrides["_pending_proposals"] = pending_proposals
    _FSW().write_yaml(_overrides_path(trw_dir), overrides)


def _step_ceremony_feedback(
    resolved_run: Path | None,
    deliver_results: dict[str, object],
) -> CeremonyFeedbackStepResult | None:
    """Step 10: Ceremony feedback recording (CORE-069-FR02)."""
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.ceremony_feedback import (
            _derive_agent_id,
            apply_auto_escalation,
            check_auto_escalation,
            generate_reduction_proposal,
            read_feedback_data,
            record_session_outcome,
        )

        trw_dir = resolve_trw_dir()

        # Extract run metadata
        run_state, task_name, task_description = _extract_run_metadata(resolved_run)

        # Extract ceremony metrics
        score, build_passed, coverage_delta, critical_findings = _extract_ceremony_metrics(deliver_results)

        current_tier = "STANDARD"
        if run_state.get("complexity_class"):
            current_tier = str(run_state["complexity_class"])

        # FIX-050-FR05: Use _derive_agent_id instead of always "unknown".
        session_id = str(run_state.get("run_id", ""))
        agent_id = _derive_agent_id(run_id=session_id or None)
        run_p = str(resolved_run) if resolved_run else ""

        entry = record_session_outcome(
            trw_dir=trw_dir,
            task_name=task_name,
            ceremony_score=score,
            build_passed=build_passed,
            coverage_delta=coverage_delta,
            critical_findings=critical_findings,
            mutation_score_ok=True,  # OQ-002: keep True until mutmut integration
            current_tier=current_tier,
            run_path=run_p,
            session_id=session_id or agent_id,
            task_description=task_description,
        )

        # FIX-051-FR06: Use the task_class from the recorded entry (now objective-enriched).
        task_class_str = str(entry.get("task_class", "documentation"))
        data = read_feedback_data(trw_dir)

        # FIX-051-FR03: De-escalation path -- generate proposal and persist to disk.
        # Proposals are persisted (not just in _pending_proposals memory) because
        # this runs in a daemon thread invisible to the main MCP thread.
        proposal = generate_reduction_proposal(task_class_str, data)
        if proposal:
            _process_ceremony_proposal(trw_dir, task_class_str, proposal)

        escalation = check_auto_escalation(task_class_str, data)
        if escalation:
            apply_auto_escalation(trw_dir, task_class_str, cast("dict[str, object]", escalation))
            return cast(
                "CeremonyFeedbackStepResult",
                {"recorded": True, "auto_escalation": escalation, "proposal": proposal},
            )
        return cast("CeremonyFeedbackStepResult", {"recorded": True, "proposal": proposal})
    except Exception as exc:  # justified: fail-open, ceremony feedback is best-effort enrichment
        return cast("CeremonyFeedbackStepResult", {"skipped": True, "reason": str(exc)})
