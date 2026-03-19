"""Deferred delivery infrastructure for trw_deliver.

Extracted from ceremony.py to reduce file complexity.  Contains:
- File-lock acquisition/release for concurrent deferred batches
- Audit logging of deferred step results
- The ``_run_deferred_steps`` orchestrator that executes all deferred steps
- ``_launch_deferred`` which starts the background daemon thread
- All ``_step_*`` helpers that implement individual deferred steps

This module is internal (``_``-prefixed) — external code should import
from ``trw_mcp.tools.ceremony`` which re-exports the public surface.

Note: ``_deferred_thread`` and ``_deferred_lock`` live in ``ceremony.py``
(the canonical location) so that existing test patches via
``monkeypatch.setattr(cer, "_deferred_thread", ...)`` continue to work.
``_launch_deferred`` accesses them via a late import of the ceremony
module to avoid circular import issues at module-load time.

Test patches for step functions (``_step_*``) should target this module
directly: ``patch("trw_mcp.tools._deferred_delivery._step_foo")``.
"""

from __future__ import annotations

import io
import json
import threading
import time

# Portability shim: fcntl is Unix-only. On Windows, advisory locking
# is a no-op (see persistence.py for rationale).
try:
    import fcntl as _fcntl

    def _lock_ex_nb(fd: int) -> None:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)

    def _lock_ex(fd: int) -> None:
        _fcntl.flock(fd, _fcntl.LOCK_EX)

    def _lock_un(fd: int) -> None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
except ImportError:

    def _lock_ex_nb(fd: int) -> None:
        pass

    def _lock_ex(fd: int) -> None:
        pass

    def _lock_un(fd: int) -> None:
        pass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import (
    AutoProgressStepResult,
    BatchSendResult,
    CeremonyFeedbackStepResult,
    ConsolidationStepResult,
    IndexSyncResult,
    OutcomeCorrelationStepResult,
    PublishLearningsResult,
    RecallOutcomeStepResult,
    TelemetryStepResult,
    TierSweepStepResult,
    TrustIncrementResult,
)
from trw_mcp.state.persistence import (
    FileStateReader,
)
from trw_mcp.tools._helpers import _run_step

logger = structlog.get_logger(__name__)


# _run_step is imported from trw_mcp.tools._helpers (shared with ceremony.py)


def _try_acquire_deferred_lock(trw_dir: Path) -> io.TextIOWrapper | None:
    """Try to acquire the deferred-deliver file lock (non-blocking).

    Returns the lock file handle on success, or None if another
    deferred batch is already running.  Caller MUST call
    ``_release_deferred_lock(fd)`` when done.
    """
    lock_path = trw_dir / "deliver-deferred.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("w", encoding="utf-8")
    try:
        _lock_ex_nb(fd.fileno())
        # Write PID + timestamp as valid JSON so operators can inspect
        import os as _os

        fd.write(json.dumps({"pid": _os.getpid(), "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
        fd.flush()
        return fd
    except Exception:  # justified: cleanup, lock acquisition failure releases fd and returns None
        fd.close()
        return None


def _release_deferred_lock(fd: object) -> None:
    """Release the deferred-deliver file lock."""
    try:
        import io as _io

        if isinstance(fd, _io.TextIOWrapper):
            _lock_un(fd.fileno())
            fd.close()
    except Exception:  # justified: fail-open, lock release cleanup
        # justified: lock release is best-effort cleanup — failing here
        # only means the lock file persists until process exit.
        logger.debug("lock_release_failed", exc_info=True)


def _log_deferred_result(
    trw_dir: Path,
    results: dict[str, object],
    errors: list[str],
) -> None:
    """Append deferred step results to an audit log."""
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": {k: v for k, v in results.items() if k != "timestamp"},
        "errors": errors,
        "success": len(errors) == 0,
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            _lock_ex(f.fileno())
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
            _lock_un(f.fileno())
    except Exception:  # justified: fail-open, deferred log is diagnostic only
        logger.debug("deferred_log_write_failed", exc_info=True)


def _run_deferred_steps(
    trw_dir: Path,
    resolved_run: Path | None,
    critical_results: dict[str, object],
    *,
    skip_index_sync: bool = False,
) -> None:
    """Execute deferred delivery steps in the background.

    Acquires a non-blocking file lock to prevent concurrent deferred batches.
    Each step is fail-open — failures are logged but don't block other steps.

    Test patches should target this module directly:
    ``patch("trw_mcp.tools._deferred_delivery._step_foo")``.
    """
    lock_fd = _try_acquire_deferred_lock(trw_dir)
    if lock_fd is None:
        logger.info("deferred_deliver_skipped", reason="another_batch_running")
        return

    # Heterogeneous accumulator — each key holds a different TypedDict shape:
    #   "auto_prune"         → dict[str, object] | None  (auto-prune result)
    #   "consolidation"      → ConsolidationStepResult
    #   "tier_sweep"         → TierSweepStepResult
    #   "index_sync"         → IndexSyncResult
    #   "auto_progress"      → AutoProgressStepResult
    #   "publish_learnings"  → PublishLearningsResult
    #   "outcome_correlation"→ OutcomeCorrelationStepResult
    #   "recall_outcome"     → RecallOutcomeStepResult
    #   "telemetry"          → TelemetryStepResult
    #   "batch_send"         → BatchSendResult
    #   "trust_increment"    → TrustIncrementResult | None
    #   "ceremony_feedback"  → CeremonyFeedbackStepResult | None
    results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    t0 = time.monotonic()

    def _timed_step(name: str, fn: object) -> None:
        """Run a deferred step with per-step timing and structured logging."""
        _t = time.monotonic()
        _pre_errors = len(errors)
        _run_step(name, fn, results, errors)  # type: ignore[arg-type]
        _duration_ms = round((time.monotonic() - _t) * 1000, 1)
        _step_result = results.get(name)
        if len(errors) > _pre_errors:
            _last_err = errors[-1]
            logger.error("deferred_step_failed", step=name, error=_last_err)
        elif _step_result is None or (isinstance(_step_result, dict) and _step_result.get("status") == "skipped"):
            logger.warning("deferred_step_skip", step=name, reason=str(_step_result))
        else:
            logger.info("deferred_step_ok", step=name, duration_ms=_duration_ms)

    try:
        # Step 2.5: Auto-prune excess learnings
        _timed_step("auto_prune", lambda: _step_auto_prune(trw_dir))

        # Step 2.6: Memory consolidation (PRD-CORE-044)
        _timed_step("consolidation", lambda: _step_consolidation(trw_dir))

        # Step 2.7: Tier lifecycle sweep (PRD-CORE-043)
        _timed_step("tier_sweep", lambda: _step_tier_sweep(trw_dir))

        # Step 4: INDEX.md / ROADMAP.md sync
        if not skip_index_sync:
            _timed_step("index_sync", lambda: _do_index_sync())
        else:
            results["index_sync"] = {"status": "skipped"}
            logger.warning("deferred_step_skip", step="index_sync", reason="skip_index_sync=True")

        # Step 5: Auto-progress PRD statuses
        _timed_step("auto_progress", lambda: _step_auto_progress(resolved_run))

        # Step 6: Publish high-impact learnings
        _timed_step("publish_learnings", lambda: _step_publish_learnings())

        # Step 6.5: Outcome correlation
        _timed_step("outcome_correlation", lambda: _step_outcome_correlation())

        # Step 6.6: Recall outcome tracking
        _timed_step("recall_outcome", lambda: _step_recall_outcome(resolved_run))

        # Step 7: Telemetry events
        _timed_step("telemetry", lambda: _step_telemetry(resolved_run))

        # Step 8: Batch send telemetry
        _timed_step("batch_send", lambda: _step_batch_send())

        # Step 9: Trust increment
        _timed_step("trust_increment", lambda: _step_trust_increment(resolved_run))

        # Step 10: Ceremony feedback
        _timed_step("ceremony_feedback", lambda: _step_ceremony_feedback(resolved_run, critical_results))

        steps_ok = sum(
            1 for k, v in results.items()
            if k not in ("timestamp", "elapsed_seconds")
            and isinstance(v, dict)
            and v.get("status") not in ("skipped", "error", None)
        )
        steps_failed = len(errors)
        elapsed = time.monotonic() - t0
        results["elapsed_seconds"] = round(elapsed, 2)
        logger.info(
            "deferred_delivery_complete",
            steps_ok=steps_ok,
            steps_failed=steps_failed,
        )
        logger.info(
            "deferred_deliver_complete",
            steps=len(results) - 2,  # minus timestamp and elapsed
            errors=len(errors),
            elapsed=round(elapsed, 2),
        )
    except Exception as exc:  # justified: fail-open, deferred delivery must never crash the background thread
        # Catch-all: should never reach here since _run_step is fail-open
        errors.append(f"deferred_fatal: {exc}")
        logger.warning("deferred_deliver_fatal", error=str(exc), exc_info=True)
    finally:
        _log_deferred_result(trw_dir, results, errors)
        _release_deferred_lock(lock_fd)


def _launch_deferred(
    trw_dir: Path,
    resolved_run: Path | None,
    critical_results: dict[str, object],
    *,
    skip_index_sync: bool = False,
) -> str:
    """Launch deferred steps on a daemon thread.

    Returns a status string indicating what happened:
    - "launched": new background thread started
    - "skipped_already_running": a previous deferred batch is still active

    Note: ``_deferred_thread`` and ``_deferred_lock`` are read from/written
    to the ``ceremony`` module (late import) so that test patches via
    ``monkeypatch.setattr(cer, "_deferred_thread", ...)`` work correctly.
    """
    import trw_mcp.tools.ceremony as _cer  # late import to avoid circular

    with _cer._deferred_lock:
        if _cer._deferred_thread is not None and _cer._deferred_thread.is_alive():
            logger.info("deferred_launch_skipped", reason="thread_still_alive")
            return "skipped_already_running"

        _cer._deferred_thread = threading.Thread(
            target=_run_deferred_steps,
            args=(trw_dir, resolved_run, critical_results),
            kwargs={"skip_index_sync": skip_index_sync},
            name="trw-deliver-deferred",
            daemon=True,
        )
        _cer._deferred_thread.start()
        return "launched"


# --- Deliver step helpers ---


def _step_checkpoint(resolved_run: Path) -> dict[str, object]:
    """Step 2: Delivery state snapshot."""
    from trw_mcp.tools.checkpoint import _do_checkpoint

    _do_checkpoint(resolved_run, "delivery")
    return {"status": "success"}


def _step_auto_prune(trw_dir: Path) -> dict[str, object] | None:
    """Step 2.5: Auto-prune excess learnings."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.analytics import auto_prune_excess_entries

    config = get_config()
    if not config.learning_auto_prune_on_deliver:
        return None

    prune_result = auto_prune_excess_entries(
        trw_dir,
        max_entries=config.learning_auto_prune_cap,
    )
    pruned = int(str(prune_result.get("actions_taken", 0)))
    return prune_result if pruned > 0 else None


def _step_consolidation(trw_dir: Path) -> ConsolidationStepResult:
    """Step 2.6: Memory consolidation (PRD-CORE-044)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.consolidation import consolidate_cycle

    config = get_config()
    if not config.memory_consolidation_enabled:
        return cast("ConsolidationStepResult", {"status": "skipped", "reason": "disabled"})

    return cast(
        "ConsolidationStepResult",
        dict(
            consolidate_cycle(
                trw_dir,
                max_entries=config.memory_consolidation_max_per_cycle,
            )
        ),
    )


def _step_tier_sweep(trw_dir: Path) -> TierSweepStepResult:
    """Step 2.7: Tier lifecycle sweep (PRD-CORE-043) + impact tier assignment (PRD-FIX-052-FR07)."""
    from trw_mcp.state.persistence import FileStateWriter
    from trw_mcp.state.tiers import TierManager

    reader = FileStateReader()
    writer = FileStateWriter()
    tier_mgr = TierManager(trw_dir, reader, writer)
    sweep_result = tier_mgr.sweep()

    # PRD-FIX-052-FR07: assign impact_tier labels to all active entries post-sweep
    tier_distribution = tier_mgr.assign_impact_tiers(trw_dir)

    return {
        "status": "success",
        "promoted": sweep_result.promoted,
        "demoted": sweep_result.demoted,
        "purged": sweep_result.purged,
        "errors": sweep_result.errors,
        "impact_tier_distribution": tier_distribution,
    }


def _step_auto_progress(resolved_run: Path | None) -> AutoProgressStepResult:
    """Step 5: Auto-progress PRD statuses."""
    return _do_auto_progress(resolved_run)


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
    except Exception:  # justified: fail-open — pipeline drain must not block delivery
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


def _merge_session_events(
    run_events: list[dict[str, object]],
    trw_dir: Path,
) -> list[dict[str, object]]:
    """Merge run-level and session-level events from fallback path.

    FIX-051-FR01/FR05 & FIX-053-FR02: trw_session_start fires before trw_init
    creates the run directory, so its events land in session-events.jsonl instead
    of events.jsonl. This helper merges both sources for ceremony scoring and
    trust increment checks.

    Args:
        run_events: Events from run-level events.jsonl.
        trw_dir: Path to .trw directory for session-events.jsonl lookup.

    Returns:
        Merged event list (session events prepended).
    """
    all_events = list(run_events)
    session_events_path = trw_dir / "context" / "session-events.jsonl"
    if session_events_path.exists():
        try:
            reader = FileStateReader()
            session_events = reader.read_jsonl(session_events_path)
            all_events = list(session_events) + all_events
        except Exception:  # justified: fail-open, session-events read is best-effort
            logger.debug("session_events_merge_failed", path=str(session_events_path))
    return all_events


def _step_trust_increment(resolved_run: Path | None) -> TrustIncrementResult | None:
    """Step 9: Trust session increment (CORE-068-FR05).

    FR02 (PRD-FIX-053): Relaxed gate — fires when EITHER:
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

        # FIX-051-FR03: De-escalation path — generate proposal and persist to disk.
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
