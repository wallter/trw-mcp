"""Deferred delivery infrastructure for trw_deliver.

Extracted from ceremony.py to reduce file complexity.  Contains:
- File-lock acquisition/release for concurrent deferred batches
- Audit logging of deferred step results
- The ``_run_deferred_steps`` orchestrator that executes all deferred steps
- ``_launch_deferred`` which starts the background daemon thread
- All ``_step_*`` helpers that implement individual deferred steps
- ``_resolve_installation_id`` helper shared by telemetry steps

This module is internal (``_``-prefixed) — external code should import
from ``trw_mcp.tools.ceremony`` which re-exports the public surface.

Note: ``_deferred_thread`` and ``_deferred_lock`` live in ``ceremony.py``
(the canonical location) so that existing test patches via
``monkeypatch.setattr(cer, "_deferred_thread", ...)`` continue to work.
``_launch_deferred`` accesses them via a late import of the ceremony
module to avoid circular import issues at module-load time.
"""

from __future__ import annotations

import fcntl
import io
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.state.persistence import (
    FileStateReader,
)

logger = structlog.get_logger()


def _run_step(
    name: str,
    fn: Callable[[], dict[str, object] | None],
    results: dict[str, object],
    errors: list[str],
) -> None:
    """Execute a delivery step with fail-open error handling.

    If ``fn`` returns a dict, it is stored in ``results[name]``.
    If ``fn`` returns None, nothing is stored (used for conditional steps).
    Exceptions are appended to ``errors`` and a failure dict is stored.
    """
    try:
        step_result = fn()
        if step_result is not None:
            results[name] = step_result
    except Exception as exc:  # justified: fail-open, individual delivery step must not block others
        errors.append(f"{name}: {exc}")
        results[name] = {"status": "failed", "error": str(exc)}


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
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write PID + timestamp so operators can inspect
        fd.write(f"{{'pid': {__import__('os').getpid()}, 'ts': '{datetime.now(timezone.utc).isoformat()}'}}\n")
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
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
    except Exception:
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
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
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

    Note: step functions are resolved from ``ceremony`` module at call time
    so that test patches on ``trw_mcp.tools.ceremony._step_*`` are visible.
    """
    # Late import — tests patch step functions on the ceremony module, so we
    # must resolve them from there rather than from our own module scope.
    import trw_mcp.tools.ceremony as _cer  # noqa: F811

    lock_fd = _try_acquire_deferred_lock(trw_dir)
    if lock_fd is None:
        logger.info("deferred_deliver_skipped", reason="another_batch_running")
        return

    results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    t0 = time.monotonic()

    try:
        # Step 2.5: Auto-prune excess learnings
        _run_step("auto_prune", lambda: _cer._step_auto_prune(trw_dir), results, errors)

        # Step 2.6: Memory consolidation (PRD-CORE-044)
        _run_step("consolidation", lambda: _cer._step_consolidation(trw_dir), results, errors)

        # Step 2.7: Tier lifecycle sweep (PRD-CORE-043)
        _run_step("tier_sweep", lambda: _cer._step_tier_sweep(trw_dir), results, errors)

        # Step 4: INDEX.md / ROADMAP.md sync
        if not skip_index_sync:
            _run_step("index_sync", lambda: _cer._do_index_sync(), results, errors)
        else:
            results["index_sync"] = {"status": "skipped"}

        # Step 5: Auto-progress PRD statuses
        _run_step("auto_progress", lambda: _cer._step_auto_progress(resolved_run), results, errors)

        # Step 6: Publish high-impact learnings
        _run_step("publish_learnings", lambda: _cer._step_publish_learnings(), results, errors)

        # Step 6.5: Outcome correlation
        _run_step("outcome_correlation", lambda: _cer._step_outcome_correlation(), results, errors)

        # Step 6.6: Recall outcome tracking
        _run_step("recall_outcome", lambda: _cer._step_recall_outcome(resolved_run), results, errors)

        # Step 7: Telemetry events
        _run_step("telemetry", lambda: _cer._step_telemetry(resolved_run), results, errors)

        # Step 8: Batch send telemetry
        _run_step("batch_send", lambda: _cer._step_batch_send(), results, errors)

        # Step 9: Trust increment
        _run_step(
            "trust_increment",
            lambda: _cer._step_trust_increment(resolved_run),
            results,
            errors,
        )

        # Step 10: Ceremony feedback
        _run_step(
            "ceremony_feedback",
            lambda: _cer._step_ceremony_feedback(resolved_run, critical_results),
            results,
            errors,
        )

        elapsed = time.monotonic() - t0
        results["elapsed_seconds"] = round(elapsed, 2)
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
    """Step 2: Delivery state snapshot.

    Note: ``_do_checkpoint`` is resolved from ceremony module at call time
    so that test patches on ``trw_mcp.tools.ceremony._do_checkpoint`` work.
    """
    import trw_mcp.tools.ceremony as _cer  # late import for test compat
    _cer._do_checkpoint(resolved_run, "delivery")
    return {"status": "success"}


def _step_auto_prune(trw_dir: Path) -> dict[str, object] | None:
    """Step 2.5: Auto-prune excess learnings."""
    import trw_mcp.tools.ceremony as _cer  # late import for test compat
    config = _cer.get_config()
    if not config.learning_auto_prune_on_deliver:
        return None
    from trw_mcp.state.analytics import auto_prune_excess_entries
    prune_result = auto_prune_excess_entries(
        trw_dir,
        max_entries=config.learning_auto_prune_cap,
    )
    pruned = int(str(prune_result.get("actions_taken", 0)))
    return prune_result if pruned > 0 else None


def _step_consolidation(trw_dir: Path) -> dict[str, object]:
    """Step 2.6: Memory consolidation (PRD-CORE-044)."""
    import trw_mcp.tools.ceremony as _cer  # late import for test compat
    config = _cer.get_config()
    if not config.memory_consolidation_enabled:
        return {"status": "skipped", "reason": "disabled"}
    from trw_mcp.state.consolidation import consolidate_cycle
    return dict(consolidate_cycle(
        trw_dir,
        max_entries=config.memory_consolidation_max_per_cycle,
    ))


def _step_tier_sweep(trw_dir: Path) -> dict[str, object]:
    """Step 2.7: Tier lifecycle sweep (PRD-CORE-043)."""
    from trw_mcp.state.tiers import TierManager
    from trw_mcp.state.persistence import FileStateWriter
    reader = FileStateReader()
    writer = FileStateWriter()
    tier_mgr = TierManager(trw_dir, reader, writer)
    sweep_result = tier_mgr.sweep()
    return {
        "status": "success",
        "promoted": sweep_result.promoted,
        "demoted": sweep_result.demoted,
        "purged": sweep_result.purged,
        "errors": sweep_result.errors,
    }


def _step_auto_progress(resolved_run: Path | None) -> dict[str, object]:
    """Step 5: Auto-progress PRD statuses."""
    return _do_auto_progress(resolved_run)


def _step_publish_learnings() -> dict[str, object]:
    """Step 6: Publish high-impact learnings to platform backend."""
    from trw_mcp.telemetry.publisher import publish_learnings
    return dict(publish_learnings())


def _step_outcome_correlation() -> dict[str, object]:
    """Step 6.5: Outcome correlation (G1)."""
    from trw_mcp.scoring import process_outcome_for_event
    outcome_ids = process_outcome_for_event("trw_deliver_complete")
    return {"status": "success", "updated": len(outcome_ids)}


def _step_recall_outcome(resolved_run: Path | None) -> dict[str, object]:
    """Step 6.6: Recall outcome tracking (G6)."""
    from trw_mcp.state.recall_tracking import get_recall_stats, record_outcome
    recall_stats = get_recall_stats()
    unique_ids = recall_stats.get("unique_learnings", 0)
    recalled_count = 0
    if unique_ids and resolved_run is not None:
        import trw_mcp.tools.ceremony as _cer  # late import for test compat
        trw_dir_rt = _cer.resolve_trw_dir()
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


def _resolve_installation_id() -> str:
    """Resolve installation ID from config, never falling back to 'local'."""
    import trw_mcp.tools.ceremony as _cer  # late import for test compat
    cfg = _cer.get_config()
    iid = cfg.installation_id.strip() if cfg.installation_id else ""
    if iid:
        return iid
    # Generate a stable ID from the project root path
    import hashlib
    project_root = str(_cer.resolve_project_root())
    return "inst-" + hashlib.sha256(project_root.encode()).hexdigest()[:12]


def _step_telemetry(resolved_run: Path | None) -> dict[str, object]:
    """Step 7: Telemetry events (G3 + G4)."""
    import trw_mcp.tools.ceremony as _cer  # late import for test compat
    from trw_mcp.state.analytics.report import compute_ceremony_score
    from trw_mcp.telemetry.client import TelemetryClient
    from trw_mcp.telemetry.models import CeremonyComplianceEvent, SessionEndEvent

    cfg = _cer.get_config()
    inst_id = _resolve_installation_id()
    tel_client = TelemetryClient.from_config()

    # Read events for tool count and ceremony score computation
    events: list[dict[str, object]] = []
    if resolved_run is not None:
        ev_path = resolved_run / "meta" / "events.jsonl"
        if ev_path.exists():
            from trw_mcp.state.persistence import FileStateReader as _FSR2
            ev_reader = _FSR2()
            events = ev_reader.read_jsonl(ev_path)

    # Compute actual ceremony score from run events
    ceremony = compute_ceremony_score(events)
    ceremony_score = int(str(ceremony.get("score", 0)))

    tel_client.record_event(SessionEndEvent(
        installation_id=inst_id,
        framework_version=cfg.framework_version,
        tools_invoked=len(events),
        ceremony_score=ceremony_score,
    ))
    run_id_str = str(resolved_run.name) if resolved_run else "unknown"
    tel_client.record_event(CeremonyComplianceEvent(
        installation_id=inst_id,
        framework_version=cfg.framework_version,
        run_id=run_id_str,
        score=ceremony_score,
    ))
    tel_client.flush()
    return {"status": "success", "events": 2, "ceremony_score": ceremony_score}


def _step_batch_send() -> dict[str, object]:
    """Step 8: Batch send (G2)."""
    from trw_mcp.telemetry.sender import BatchSender
    return dict(BatchSender.from_config().send())


def _step_trust_increment(resolved_run: Path | None) -> dict[str, object] | None:
    """Step 9: Trust session increment (CORE-068-FR05)."""
    try:
        from trw_mcp.state.trust import increment_session_count

        events_path = resolved_run / "meta" / "events.jsonl" if resolved_run else None
        build_passed = False
        if events_path and events_path.exists():
            reader = FileStateReader()
            events = reader.read_jsonl(events_path)
            for ev in events:
                ev_type = str(ev.get("event", ""))
                ev_data = ev.get("data", {})
                if not isinstance(ev_data, dict):
                    ev_data = {}
                tool_name = str(ev_data.get("tool_name", ""))
                if ev_type == "build_check_complete" or tool_name == "trw_build_check":
                    if ev_data.get("result") == "pass" or ev_data.get("build_passed") is True:
                        build_passed = True
                        break

        if build_passed:
            import os
            import trw_mcp.tools.ceremony as _cer  # late import for test compat
            agent_id = os.environ.get("TRW_AGENT_ID", "unknown")
            trw_dir = _cer.resolve_trw_dir()
            return increment_session_count(trw_dir, agent_id)
        return {"skipped": True, "reason": "build_check_not_passed"}
    except Exception as exc:  # justified: fail-open, session count increment is best-effort
        return {"skipped": True, "reason": str(exc)}


def _step_ceremony_feedback(
    resolved_run: Path | None,
    deliver_results: dict[str, object],
) -> dict[str, object] | None:
    """Step 10: Ceremony feedback recording (CORE-069-FR02)."""
    try:
        from trw_mcp.state.ceremony_feedback import (
            apply_auto_escalation,
            check_auto_escalation,
            classify_task_class,
            read_feedback_data,
            record_session_outcome,
        )

        import trw_mcp.tools.ceremony as _cer  # late import for test compat
        trw_dir = _cer.resolve_trw_dir()
        run_state: dict[str, object] = {}
        if resolved_run is not None:
            run_yaml = resolved_run / "meta" / "run.yaml"
            if run_yaml.exists():
                reader = FileStateReader()
                run_state = reader.read_yaml(run_yaml)

        task_name = str(run_state.get("task_name", ""))
        ceremony_score_val = deliver_results.get("telemetry", {})
        if isinstance(ceremony_score_val, dict):
            score = float(ceremony_score_val.get("ceremony_score", 0))
        else:
            score = 0.0

        build_passed = False
        tel_data = deliver_results.get("telemetry", {})
        if isinstance(tel_data, dict):
            build_passed = bool(tel_data.get("build_passed", False))

        current_tier = "STANDARD"
        if run_state.get("complexity_class"):
            current_tier = str(run_state["complexity_class"])

        session_id = str(run_state.get("run_id", "unknown"))
        run_p = str(resolved_run) if resolved_run else ""

        entry = record_session_outcome(
            trw_dir=trw_dir,
            task_name=task_name,
            ceremony_score=score,
            build_passed=build_passed,
            coverage_delta=0.0,
            critical_findings=0,
            mutation_score_ok=True,
            current_tier=current_tier,
            run_path=run_p,
            session_id=session_id,
        )

        task_class = classify_task_class(task_name)
        data = read_feedback_data(trw_dir)
        escalation = check_auto_escalation(task_class.value, data)
        if escalation:
            apply_auto_escalation(trw_dir, task_class.value, escalation)
            return {"recorded": True, "auto_escalation": escalation}
        return {"recorded": True}
    except Exception as exc:  # justified: fail-open, ceremony feedback is best-effort enrichment
        return {"skipped": True, "reason": str(exc)}


def _do_index_sync() -> dict[str, object]:
    """Execute INDEX.md and ROADMAP.md sync from PRD frontmatter.

    Note: ``resolve_project_root`` and ``get_config`` are resolved from
    the ceremony module at call time so that test patches on
    ``trw_mcp.tools.ceremony.resolve_project_root`` propagate correctly.
    """
    import trw_mcp.tools.ceremony as _cer  # late import for test compat

    from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md
    from trw_mcp.state.persistence import FileStateWriter

    config = _cer.get_config()
    writer = FileStateWriter()
    project_root = _cer.resolve_project_root()
    prds_dir = project_root / Path(config.prds_relative_path)
    aare_dir = prds_dir.parent

    index_result = sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=writer)
    roadmap_result = sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=writer)

    return {
        "status": "success",
        "index": index_result,
        "roadmap": roadmap_result,
    }


def _do_auto_progress(run_dir: Path | None) -> dict[str, object]:
    """Auto-progress PRD statuses for the deliver phase.

    Calls ``auto_progress_prds`` with phase="deliver" for all PRDs
    in the run's ``prd_scope``. Skipped if no active run.

    Note: ``resolve_project_root`` and ``get_config`` are resolved from
    the ceremony module at call time so that test patches on
    ``trw_mcp.tools.ceremony.*`` propagate correctly.
    """
    if run_dir is None:
        return {"status": "skipped", "reason": "no_active_run"}

    import trw_mcp.tools.ceremony as _cer  # late import for test compat

    from trw_mcp.state.validation import auto_progress_prds

    config = _cer.get_config()
    project_root = _cer.resolve_project_root()
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
