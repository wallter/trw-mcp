"""TRW session ceremony tools — trw_session_start, trw_deliver.

PRD-CORE-019: Composite tools that reduce ceremony from 7 manual calls
to 2, with partial-failure resilience on each sub-operation.
PRD-CORE-049: Phase-contextual auto-recall in trw_session_start.

Review tool: trw_mcp.tools.review (PRD-QUAL-022)
Checkpoint tools: trw_mcp.tools.checkpoint (PRD-CORE-053)
"""

from __future__ import annotations

import fcntl
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import find_active_run, pin_active_run, resolve_project_root, resolve_trw_dir
from trw_mcp.state.analytics import (
    find_success_patterns,
    update_analytics,
)
from trw_mcp.state.claude_md import execute_claude_md_sync
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_events = FileEventLogger(FileStateWriter())


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    if name == "_config":
        return get_config()
    if name == "_reader":
        return FileStateReader()
    if name == "_writer":
        return FileStateWriter()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Re-export checkpoint helpers for backward compatibility with tests/hooks
from trw_mcp.tools.checkpoint import (  # noqa: E402
    _do_checkpoint,
    _maybe_auto_checkpoint,
    _reset_tool_call_counter,
)

# Suppress unused import warnings — these are re-exports
__all__ = ["_do_checkpoint", "_maybe_auto_checkpoint", "_reset_tool_call_counter"]


def _get_run_status(run_dir: Path) -> dict[str, object]:
    """Extract status summary from a run directory."""
    reader = FileStateReader()
    result: dict[str, object] = {"active_run": str(run_dir)}
    try:
        run_yaml = run_dir / "meta" / "run.yaml"
        if run_yaml.exists():
            data = reader.read_yaml(run_yaml)
            result["phase"] = str(data.get("phase", "unknown"))
            result["status"] = str(data.get("status", "unknown"))
            result["task_name"] = str(data.get("task_name", ""))
            if "owner_session_id" in data:
                result["owner_session_id"] = data["owner_session_id"]
            # INFRA-036-FR05: Include wave status in session start
            wave_status = data.get("wave_status")
            if wave_status and isinstance(wave_status, dict):
                result["wave_status"] = wave_status
    except (StateError, OSError, ValueError):
        result["status"] = "error_reading"
    return result


def _mark_run_complete(run_dir: Path) -> None:
    """Mark a run as complete by updating status in run.yaml."""
    reader = FileStateReader()
    writer = FileStateWriter()
    run_yaml = run_dir / "meta" / "run.yaml"
    if not run_yaml.exists():
        return
    try:
        data = reader.read_yaml(run_yaml)
        data["status"] = "complete"
        writer.write_yaml(run_yaml, data)
    except Exception:
        # justified: marking run complete is best-effort — failure must not
        # block session_start or deliver.
        logger.warning(
            "mark_run_complete_failed",
            exc_info=True,
            run_dir=str(run_dir),
        )


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
    except Exception as exc:
        errors.append(f"{name}: {exc}")
        results[name] = {"status": "failed", "error": str(exc)}


# --- Deferred delivery infrastructure ---

# Global tracker for the background thread — lets callers check status
_deferred_thread: threading.Thread | None = None
_deferred_lock = threading.Lock()


def _try_acquire_deferred_lock(trw_dir: Path) -> int | None:
    """Try to acquire the deferred-deliver file lock (non-blocking).

    Returns the lock file descriptor on success, or None if another
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
        return fd  # type: ignore[return-value]
    except Exception:
        fd.close()
        return None


def _release_deferred_lock(fd: object) -> None:
    """Release the deferred-deliver file lock."""
    try:
        import io
        if isinstance(fd, io.TextIOWrapper):
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
    except Exception:
        # justified: lock release is best-effort cleanup — failing here
        # only means the lock file persists until process exit.
        pass


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
    except Exception:
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
    """
    lock_fd = _try_acquire_deferred_lock(trw_dir)
    if lock_fd is None:
        logger.info("deferred_deliver_skipped", reason="another_batch_running")
        return

    results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    t0 = time.monotonic()

    try:
        # Step 2.5: Auto-prune excess learnings
        _run_step("auto_prune", lambda: _step_auto_prune(trw_dir), results, errors)

        # Step 2.6: Memory consolidation (PRD-CORE-044)
        _run_step("consolidation", lambda: _step_consolidation(trw_dir), results, errors)

        # Step 2.7: Tier lifecycle sweep (PRD-CORE-043)
        _run_step("tier_sweep", lambda: _step_tier_sweep(trw_dir), results, errors)

        # Step 4: INDEX.md / ROADMAP.md sync
        if not skip_index_sync:
            _run_step("index_sync", lambda: _do_index_sync(), results, errors)
        else:
            results["index_sync"] = {"status": "skipped"}

        # Step 5: Auto-progress PRD statuses
        _run_step("auto_progress", lambda: _step_auto_progress(resolved_run), results, errors)

        # Step 6: Publish high-impact learnings
        _run_step("publish_learnings", lambda: _step_publish_learnings(), results, errors)

        # Step 6.5: Outcome correlation
        _run_step("outcome_correlation", lambda: _step_outcome_correlation(), results, errors)

        # Step 6.6: Recall outcome tracking
        _run_step("recall_outcome", lambda: _step_recall_outcome(resolved_run), results, errors)

        # Step 7: Telemetry events
        _run_step("telemetry", lambda: _step_telemetry(resolved_run), results, errors)

        # Step 8: Batch send telemetry
        _run_step("batch_send", lambda: _step_batch_send(), results, errors)

        # Step 9: Trust increment
        _run_step(
            "trust_increment",
            lambda: _step_trust_increment(resolved_run),
            results,
            errors,
        )

        # Step 10: Ceremony feedback
        _run_step(
            "ceremony_feedback",
            lambda: _step_ceremony_feedback(resolved_run, critical_results),
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
    except Exception as exc:
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
    """
    global _deferred_thread
    with _deferred_lock:
        if _deferred_thread is not None and _deferred_thread.is_alive():
            logger.info("deferred_launch_skipped", reason="thread_still_alive")
            return "skipped_already_running"

        _deferred_thread = threading.Thread(
            target=_run_deferred_steps,
            args=(trw_dir, resolved_run, critical_results),
            kwargs={"skip_index_sync": skip_index_sync},
            name="trw-deliver-deferred",
            daemon=True,
        )
        _deferred_thread.start()
        return "launched"


# --- Deliver step helpers ---


def _step_checkpoint(resolved_run: Path) -> dict[str, object]:
    """Step 2: Delivery state snapshot."""
    _do_checkpoint(resolved_run, "delivery")
    return {"status": "success"}


def _step_auto_prune(trw_dir: Path) -> dict[str, object] | None:
    """Step 2.5: Auto-prune excess learnings."""
    config = get_config()
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
    config = get_config()
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


def _resolve_installation_id() -> str:
    """Resolve installation ID from config, never falling back to 'local'."""
    from trw_mcp.models.config import get_config as _get_cfg
    cfg = _get_cfg()
    iid = cfg.installation_id.strip() if cfg.installation_id else ""
    if iid:
        return iid
    # Generate a stable ID from the project root path
    import hashlib
    project_root = str(resolve_project_root())
    return "inst-" + hashlib.sha256(project_root.encode()).hexdigest()[:12]


def _step_telemetry(resolved_run: Path | None) -> dict[str, object]:
    """Step 7: Telemetry events (G3 + G4)."""
    from trw_mcp.models.config import get_config as _get_cfg
    from trw_mcp.state.analytics_report import compute_ceremony_score
    from trw_mcp.telemetry.client import TelemetryClient
    from trw_mcp.telemetry.models import CeremonyComplianceEvent, SessionEndEvent

    cfg = _get_cfg()
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
            agent_id = os.environ.get("TRW_AGENT_ID", "unknown")
            trw_dir = resolve_trw_dir()
            return increment_session_count(trw_dir, agent_id)
        return {"skipped": True, "reason": "build_check_not_passed"}
    except Exception as exc:
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

        trw_dir = resolve_trw_dir()
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
    except Exception as exc:
        return {"skipped": True, "reason": str(exc)}


def register_ceremony_tools(server: FastMCP) -> None:
    """Register session ceremony composite tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_session_start(query: str = "") -> dict[str, object]:
        """Load your prior learnings and any active run — gives you full context before writing code.

        Recalls high-impact learnings (patterns, gotchas, architecture decisions) and
        checks for an active run (phase, progress, last checkpoint). Without this context,
        you risk re-implementing solved problems or repeating mistakes from prior sessions.

        Partial-failure resilient: if recall fails, run status is still returned and vice versa.

        Args:
            query: Search query for focused hybrid recall (keywords matched against
                summaries/details). When provided, performs two recalls — one focused
                on your query domain and one baseline high-impact — then merges and
                deduplicates. Empty string or "*" uses default wildcard behavior.
        """
        from trw_mcp.tools._ceremony_helpers import (
            perform_session_recalls,
            run_auto_maintenance,
        )

        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()
        results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
        errors: list[str] = []
        is_focused = query.strip() not in ("", "*")

        # Step 1: Recall learnings via SQLite adapter (compact mode)
        try:
            trw_dir = resolve_trw_dir()
            learnings, _auto_recalled, extra = perform_session_recalls(
                trw_dir, query, config, reader,
            )
            results["learnings"] = learnings
            results["learnings_count"] = len(learnings)
            results.update(extra)
        except Exception as exc:
            errors.append(f"recall: {exc}")
            results["learnings"] = []
            results["learnings_count"] = 0

        # Step 2: Check active run status (and pin it for this process)
        run_dir: Path | None = None
        try:
            run_dir = find_active_run()
            if run_dir is not None:
                pin_active_run(run_dir)
                results["run"] = _get_run_status(run_dir)
            else:
                results["run"] = {"active_run": None, "status": "no_active_run"}
        except Exception as exc:
            errors.append(f"status: {exc}")
            results["run"] = {"active_run": None, "status": "error"}

        # Step 3: Log session_start event (FR01, PRD-CORE-031)
        try:
            event_data: dict[str, object] = {
                "learnings_recalled": int(str(results.get("learnings_count", 0))),
                "run_detected": run_dir is not None,
                "query": query if is_focused else "*",
            }
            if run_dir is not None:
                events_path = run_dir / "meta" / "events.jsonl"
                if events_path.parent.exists():
                    _events.log_event(events_path, "session_start", event_data)
            else:
                trw_dir_path = resolve_trw_dir()
                context_path = trw_dir_path / config.context_dir
                writer.ensure_dir(context_path)
                fallback_path = context_path / "session-events.jsonl"
                _events.log_event(fallback_path, "session_start", event_data)
        except Exception:
            logger.debug("session_event_write_failed", exc_info=True)

        # Step 3b: Queue SessionStartEvent for telemetry publishing
        try:
            from trw_mcp.telemetry.client import TelemetryClient
            from trw_mcp.telemetry.models import SessionStartEvent
            inst_id = _resolve_installation_id()
            tel_client = TelemetryClient.from_config()
            tel_client.record_event(SessionStartEvent(
                installation_id=inst_id,
                framework_version=config.framework_version,
                learnings_loaded=int(str(results.get("learnings_count", 0))),
                run_id=str(run_dir.name) if run_dir else None,
            ))
            tel_client.flush()
            # Fire-and-forget batch send so new installations appear immediately
            import threading
            def _bg_send() -> None:
                try:
                    from trw_mcp.telemetry.sender import BatchSender
                    BatchSender.from_config().send()
                except Exception:
                    # justified: fail-open telemetry — batch send is fire-and-forget
                    # on a daemon thread; failure must never block session start.
                    pass
            threading.Thread(target=_bg_send, daemon=True).start()
        except Exception:
            logger.debug("session_telemetry_failed", exc_info=True)

        # Steps 4-5, 7: Auto-maintenance (upgrade, stale runs, embeddings)
        try:
            maintenance = run_auto_maintenance(
                resolve_trw_dir(), config, run_dir,
            )
            results.update(maintenance)
        except Exception:
            logger.debug("session_maintenance_failed", exc_info=True)

        # Step 6: Phase-contextual auto-recall (PRD-CORE-049)
        try:
            if config.auto_recall_enabled:
                from trw_mcp.tools._ceremony_helpers import _phase_contextual_recall

                trw_dir_ar = resolve_trw_dir()
                run_status_obj = results.get("run", {})
                rs = run_status_obj if isinstance(run_status_obj, dict) else None
                phase_recalled = _phase_contextual_recall(
                    trw_dir_ar, query, config, run_dir, rs,
                )
                if phase_recalled:
                    results["auto_recalled"] = phase_recalled
                    results["auto_recall_count"] = len(phase_recalled)
        except Exception:
            logger.debug("session_auto_recall_failed", exc_info=True)

        results["errors"] = errors
        results["success"] = len(errors) == 0

        logger.info(
            "trw_session_start_complete",
            learnings=results.get("learnings_count", 0),
            errors=len(errors),
        )
        return results

    @server.tool()
    @log_tool_call
    def trw_deliver(
        run_path: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
    ) -> dict[str, object]:
        """Persist your learnings and progress for future sessions — without this, your work is invisible to the next agent.

        Runs critical steps synchronously (reflect, checkpoint, CLAUDE.md sync),
        then launches housekeeping steps in the background (consolidation, publish,
        telemetry, tier sweep, etc.). Background steps are concurrency-safe — if
        another deliver's background work is already running, it is skipped rather
        than queued.

        Args:
            run_path: Path to run directory (auto-detected if None).
            skip_reflect: Skip reflection step (e.g., if already reflected).
            skip_index_sync: Skip INDEX/ROADMAP sync step.
        """
        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()
        t0 = time.monotonic()
        results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
        errors: list[str] = []
        trw_dir = resolve_trw_dir()

        # Resolve run path
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        results["run_path"] = str(resolved_run) if resolved_run else None

        # Auto-update phase to DELIVER
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(resolved_run, Phase.DELIVER)

        # Steps 0, 0b, premature guard: extracted to helper
        from trw_mcp.tools._ceremony_helpers import check_delivery_gates

        gate_result = check_delivery_gates(resolved_run, reader)
        results.update(gate_result)

        # Step 0c: Copy compliance artifacts (INFRA-027-FR05)
        from trw_mcp.tools._ceremony_helpers import copy_compliance_artifacts
        compliance_result = copy_compliance_artifacts(resolved_run, trw_dir, config, reader, writer)
        results.update(compliance_result)

        # Block delivery if integration review has blocking verdict
        if gate_result.get("integration_review_block"):
            errors.append(str(gate_result["integration_review_block"]))
            results["errors"] = errors
            results["success"] = False
            return results

        # ── CRITICAL PATH (synchronous) ──
        # These 3 steps must complete before returning — they produce the
        # artifacts the next session depends on.

        # Step 1: Reflect (extract learnings from events)
        if not skip_reflect:
            _run_step("reflect", lambda: _do_reflect(trw_dir, resolved_run), results, errors)
        else:
            results["reflect"] = {"status": "skipped"}

        # Step 2: Checkpoint (delivery state snapshot)
        if resolved_run is not None:
            _run_step("checkpoint", lambda: _step_checkpoint(resolved_run), results, errors)
        else:
            results["checkpoint"] = {"status": "skipped", "reason": "no_active_run"}

        # Step 3: CLAUDE.md sync
        _run_step("claude_md_sync", lambda: _do_claude_md_sync(trw_dir), results, errors)

        critical_elapsed = round(time.monotonic() - t0, 2)
        results["critical_elapsed_seconds"] = critical_elapsed

        # ── DEFERRED PATH (background thread) ──
        # Housekeeping, analytics, publishing, and telemetry — these don't
        # affect the next session's startup and can run after we return.
        # Concurrency-safe: file lock prevents overlapping deferred batches.
        deferred_status = _launch_deferred(
            trw_dir, resolved_run, results,
            skip_index_sync=skip_index_sync,
        )
        results["deferred"] = deferred_status

        # Count only critical steps for immediate success evaluation
        critical_step_count = 3  # reflect + checkpoint + claude_md_sync
        results["errors"] = errors
        results["success"] = len(errors) == 0
        results["critical_steps_completed"] = critical_step_count - len(errors)
        results["deferred_steps"] = 11  # launched in background

        # Log trw_deliver_complete to events.jsonl so hooks can detect it
        if resolved_run is not None and (resolved_run / "meta").exists():
            _events.log_event(
                resolved_run / "meta" / "events.jsonl",
                "trw_deliver_complete",
                {
                    "critical_steps_completed": results.get("critical_steps_completed"),
                    "deferred": deferred_status,
                    "critical_elapsed_seconds": critical_elapsed,
                    "errors": len(errors),
                },
            )

        logger.info(
            "trw_deliver_complete",
            critical_steps=results.get("critical_steps_completed"),
            deferred=deferred_status,
            critical_elapsed=critical_elapsed,
            errors=len(errors),
        )
        return results

def _do_reflect(
    trw_dir: Path,
    run_dir: Path | None,
) -> dict[str, object]:
    """Execute reflection logic — extract learnings from events.

    Simplified version of the full trw_reflect tool, focused on
    mechanical extraction for delivery ceremony.
    """
    from trw_mcp.state.analytics import (
        extract_learnings_mechanical,
        find_repeated_operations,
        is_error_event,
    )

    config = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()
    writer.ensure_dir(trw_dir / config.learnings_dir / config.entries_dir)
    writer.ensure_dir(trw_dir / config.reflections_dir)

    events: list[dict[str, object]] = []

    if run_dir:
        events_path = run_dir / "meta" / "events.jsonl"
        if reader.exists(events_path):
            events = reader.read_jsonl(events_path)

    error_events = [e for e in events if is_error_event(e)]
    repeated_ops = find_repeated_operations(events)
    success_patterns = find_success_patterns(events)

    new_learnings = extract_learnings_mechanical(
        error_events, repeated_ops, trw_dir,
        max_errors=5, max_repeated=3,
    )

    # Success patterns are analytics data only — do NOT create learning entries
    # (PRD-FIX-021: suppress telemetry noise from "Success: X (Nx)" entries).

    if run_dir and (run_dir / "meta").exists():
        _events.log_event(run_dir / "meta" / "events.jsonl", "reflection_complete", {
            "reflection_id": "delivery",
            "scope": "delivery",
            "learnings_produced": len(new_learnings),
        })

    update_analytics(trw_dir, len(new_learnings))

    return {
        "status": "success",
        "events_analyzed": len(events),
        "learnings_produced": len(new_learnings),
        "success_patterns": len(success_patterns),
    }


def _do_claude_md_sync(trw_dir: Path) -> dict[str, object]:
    """Execute CLAUDE.md sync — delegates to the canonical implementation.

    Previously this function duplicated the template context dictionary
    from ``claude_md.execute_claude_md_sync``, causing key drift (e.g.
    missing ``ceremony_quick_ref``, stale progressive-disclosure policy).
    Now it delegates entirely to the single canonical implementation in
    ``claude_md.py``.
    """
    from trw_mcp.clients.llm import LLMClient

    config = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()
    # Use a no-op LLM client — deliver path doesn't need LLM summarisation.
    llm = LLMClient()
    result = execute_claude_md_sync(
        scope="root",
        target_dir=None,
        config=config,
        reader=reader,
        writer=writer,
        llm=llm,
    )
    # Normalise status for backward compatibility with deliver callers.
    result["status"] = "success"
    return result


def _do_index_sync() -> dict[str, object]:
    """Execute INDEX.md and ROADMAP.md sync from PRD frontmatter."""
    from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md

    config = get_config()
    writer = FileStateWriter()
    project_root = resolve_project_root()
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
    """
    if run_dir is None:
        return {"status": "skipped", "reason": "no_active_run"}

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
