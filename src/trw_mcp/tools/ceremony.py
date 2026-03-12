"""TRW session ceremony tools — trw_session_start, trw_deliver.

PRD-CORE-019: Composite tools that reduce ceremony from 7 manual calls
to 2, with partial-failure resilience on each sub-operation.
PRD-CORE-049: Phase-contextual auto-recall in trw_session_start.

Review tool: trw_mcp.tools.review (PRD-QUAL-022)
Checkpoint tools: trw_mcp.tools.checkpoint (PRD-CORE-053)

Deferred delivery infrastructure (background steps, file locking, step
helpers) lives in ``trw_mcp.tools._deferred_delivery``.  This module
re-exports those names so existing patches at
``trw_mcp.tools.ceremony._step_*`` etc. continue to work.
"""

from __future__ import annotations

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

# ── Deferred delivery thread globals ───────────────────────────────────
# These live here (not in _deferred_delivery) so existing test patches via
# ``monkeypatch.setattr(cer, "_deferred_thread", ...)`` continue to work.
# ``_deferred_delivery._launch_deferred`` accesses them via late import.
_deferred_thread: threading.Thread | None = None
_deferred_lock = threading.Lock()

# ── Re-exports from _deferred_delivery ─────────────────────────────────
# Tests and conftest patch these at ``trw_mcp.tools.ceremony.*``, so they
# MUST be importable from this module.  The names below are re-assigned
# as module-level attributes so ``patch("trw_mcp.tools.ceremony._step_foo")``
# resolves correctly.
from trw_mcp.tools._deferred_delivery import (  # noqa: E402
    _do_auto_progress,
    _do_index_sync,
    _launch_deferred,
    _log_deferred_result,
    _release_deferred_lock,
    _resolve_installation_id,
    _run_deferred_steps,
    _step_auto_progress,
    _step_auto_prune,
    _step_batch_send,
    _step_ceremony_feedback,
    _step_checkpoint,
    _step_consolidation,
    _step_outcome_correlation,
    _step_publish_learnings,
    _step_recall_outcome,
    _step_telemetry,
    _step_tier_sweep,
    _step_trust_increment,
    _try_acquire_deferred_lock,
)

logger = structlog.get_logger()

_events = FileEventLogger(FileStateWriter())


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)

# Re-export checkpoint helpers for backward compatibility with tests/hooks
from trw_mcp.tools.checkpoint import (  # noqa: E402
    _do_checkpoint,
    _maybe_auto_checkpoint,
    _reset_tool_call_counter,
)

# Suppress unused import warnings — these are re-exports
__all__ = [
    "_do_checkpoint", "_maybe_auto_checkpoint", "_reset_tool_call_counter",
    # Deferred delivery re-exports
    "_deferred_lock", "_deferred_thread",
    "_do_auto_progress", "_do_index_sync",
    "_launch_deferred", "_log_deferred_result",
    "_release_deferred_lock", "_resolve_installation_id",
    "_run_deferred_steps",
    "_step_auto_progress", "_step_auto_prune",
    "_step_batch_send", "_step_ceremony_feedback",
    "_step_checkpoint", "_step_consolidation", "_step_outcome_correlation",
    "_step_publish_learnings", "_step_recall_outcome", "_step_telemetry",
    "_step_tier_sweep", "_step_trust_increment",
    "_try_acquire_deferred_lock",
    # Module-level imports used by _deferred_delivery via late import
    "get_config", "resolve_project_root", "resolve_trw_dir",
]


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
    except Exception as exc:  # justified: fail-open, individual delivery step must not block others
        errors.append(f"{name}: {exc}")
        results[name] = {"status": "failed", "error": str(exc)}


# ── Synchronous delivery helpers (critical path) ──────────────────────


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


# ── Tool registration ─────────────────────────────────────────────────


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
        except Exception as exc:  # justified: fail-open, recall failure must not block session start
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
        except Exception as exc:  # justified: fail-open, run status check must not block session start
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
        except Exception:  # justified: fail-open, event logging must not block session start
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
        except Exception:  # justified: fail-open, telemetry publish must not block session start
            logger.debug("session_telemetry_failed", exc_info=True)

        # Steps 4-5, 7: Auto-maintenance (upgrade, stale runs, embeddings)
        try:
            maintenance = run_auto_maintenance(
                resolve_trw_dir(), config, run_dir,
            )
            results.update(maintenance)
        except Exception:  # justified: fail-open, auto-maintenance must not block session start
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
        except Exception:  # justified: fail-open, auto-recall must not block session start
            logger.debug("session_auto_recall_failed", exc_info=True)

        results["errors"] = errors
        results["success"] = len(errors) == 0
        results["framework_reminder"] = (
            "Read .trw/frameworks/FRAMEWORK.md — it defines the methodology "
            "your tools implement (6-phase execution model, exit criteria, "
            "formations, quality gates, phase reversion). Re-read after "
            "context compaction."
        )

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

        # -- CRITICAL PATH (synchronous) --
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

        # -- DEFERRED PATH (background thread) --
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
