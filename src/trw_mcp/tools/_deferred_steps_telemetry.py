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
from trw_mcp.state._helpers import read_jsonl_resilient
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

    # Read events for tool count and ceremony score computation. events.jsonl is
    # an append-only advisory log here (it feeds only tools_invoked and the
    # ceremony-score inputs; authoritative run state is read from run.yaml
    # below), so a torn concurrent append must drop that one line rather than
    # StateError-abort the whole telemetry step — which _run_step would record
    # as failed, wiping tools_invoked, the ceremony score, and the
    # session_summary write that drives trw_quality_dashboard. Use the resilient
    # reader, matching the trw_status / _do_reflect seams over this same log.
    events: list[dict[str, object]] = []
    if resolved_run is not None:
        ev_path = resolved_run / "meta" / "events.jsonl"
        events = read_jsonl_resilient(ev_path)

    # FIX-051-FR05: Pass trw_dir so compute_ceremony_score can also read
    # session-events.jsonl (where trw_session_start events land before trw_init).
    from trw_mcp.state._paths import resolve_trw_dir

    trw_dir_for_score = resolve_trw_dir()
    profile_weights = cfg.client_profile.ceremony_weights
    ceremony = compute_ceremony_score(events, trw_dir=trw_dir_for_score, weights=profile_weights)
    ceremony_score = int(str(ceremony.get("score", 0)))
    # FIX-052: surface the build gate so the downstream ceremony_feedback
    # step records a real ``build_passed`` instead of a constant False.
    build_passed = bool(ceremony.get("build_passed", False))
    # FIX-052: coverage signal for the ceremony_feedback step. build-status.yaml
    # records the absolute coverage_pct of the last build_check; we forward it
    # as coverage_delta (a positive coverage figure is a positive ceremony
    # signal — there is no stored baseline to diff against here).
    coverage_delta = 0.0
    try:
        _bs_path = trw_dir_for_score / cfg.context_dir / "build-status.yaml"
        if _bs_path.exists():
            _bs = FileStateReader().read_yaml(_bs_path)
            _cov = _bs.get("coverage_pct") if isinstance(_bs, dict) else None
            if _cov is not None:
                coverage_delta = float(str(_cov))
    except Exception:  # justified: fail-open -- coverage is best-effort enrichment
        logger.debug("telemetry_coverage_read_failed", exc_info=True)

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
                if "static_checks_clean" in bs_data:
                    summary_data["static_checks_clean"] = bs_data["static_checks_clean"]
                elif "mypy_clean" in bs_data:
                    summary_data["static_checks_clean"] = bs_data["mypy_clean"]
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

    return {
        "status": "success",
        "events": 2,
        "ceremony_score": ceremony_score,
        "build_passed": build_passed,
        "coverage_delta": coverage_delta,
    }


def _step_batch_send() -> BatchSendResult:
    """Step 8: Batch send (G2)."""
    from trw_mcp.telemetry.sender import BatchSender

    return cast("BatchSendResult", dict(BatchSender.from_config().send()))


def _extract_ceremony_metrics(deliver_results: dict[str, object]) -> tuple[float, bool, float, int]:
    """Extract ceremony score, build pass, coverage delta, and critical findings.

    FIX-052: the ceremony score, build gate, and coverage are read from the
    ``telemetry`` deferred step (the only producer of a real, computed
    ceremony score). Earlier the caller passed the PRE-deferred snapshot,
    which never contained a ``telemetry`` key, so ``score`` was always 0.0
    and the adaptive-ceremony feedback loop had no gradient. The
    ``build_check`` key is kept as a fallback for any caller that does
    populate it.
    """
    telemetry_data = deliver_results.get("telemetry", {})
    build_check_data = deliver_results.get("build_check", {})

    # Ceremony score (real 0-100 value computed by the telemetry step).
    score = 0.0
    if isinstance(telemetry_data, dict):
        score = float(telemetry_data.get("ceremony_score", 0))

    # Build passed: prefer the telemetry step's build gate, fall back to a
    # caller-supplied build_check result if present.
    build_passed = False
    if isinstance(telemetry_data, dict) and "build_passed" in telemetry_data:
        build_passed = bool(telemetry_data.get("build_passed", False))
    elif isinstance(build_check_data, dict):
        build_passed = bool(build_check_data.get("build_passed", False)) or bool(
            build_check_data.get("tests_passed", False)
        )

    # Coverage delta: prefer the telemetry step's value, fall back to build_check.
    coverage_delta = 0.0
    raw_delta: object = None
    if isinstance(telemetry_data, dict) and "coverage_delta" in telemetry_data:
        raw_delta = telemetry_data.get("coverage_delta")
    elif isinstance(build_check_data, dict):
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
