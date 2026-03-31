"""TRW session ceremony tools — trw_session_start, trw_deliver.

PRD-CORE-019: Composite tools that reduce ceremony from 7 manual calls
to 2, with partial-failure resilience on each sub-operation.
PRD-CORE-049: Phase-contextual auto-recall in trw_session_start.

Review tool: trw_mcp.tools.review (PRD-QUAL-022)
Checkpoint tools: trw_mcp.tools.checkpoint (PRD-CORE-053)

Deferred delivery infrastructure (background steps, file locking, step
helpers) lives in ``trw_mcp.tools._deferred_delivery``.  Test patches
for step functions should target that module directly:
``patch("trw_mcp.tools._deferred_delivery._step_foo")``.
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    ClaudeMdSyncResultDict,
    DeliverResultDict,
    ReflectResultDict,
    RunStatusDict,
    SessionStartResultDict,
)
from trw_mcp.state._paths import find_active_run, pin_active_run, resolve_trw_dir
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
from trw_mcp.tools._deferred_delivery import (
    _launch_deferred,
    _step_checkpoint,
)
from trw_mcp.tools._helpers import _run_step
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def _get_run_status(run_dir: Path) -> RunStatusDict:
    """Extract status summary from a run directory."""
    reader = FileStateReader()
    result: RunStatusDict = {"active_run": str(run_dir)}
    try:
        run_yaml = run_dir / "meta" / "run.yaml"
        if run_yaml.exists():
            data = reader.read_yaml(run_yaml)
            result["phase"] = str(data.get("phase", "unknown"))
            result["status"] = str(data.get("status", "unknown"))
            # FIX-050-FR03: RunState model uses field "task", not "task_name".
            result["task_name"] = str(data.get("task", ""))
            if "owner_session_id" in data:
                sid = data["owner_session_id"]
                result["owner_session_id"] = str(sid) if sid is not None else None
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
    except Exception:  # justified: fail-open, ceremony completion is best-effort
        # justified: marking run complete is best-effort — failure must not
        # block session_start or deliver.
        logger.warning(
            "mark_run_complete_failed",
            exc_info=True,
            run_dir=str(run_dir),
        )


# ── Synchronous delivery helpers (critical path) ──────────────────────


def _do_reflect(
    trw_dir: Path,
    run_dir: Path | None,
) -> ReflectResultDict:
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


def _do_instruction_sync(trw_dir: Path) -> ClaudeMdSyncResultDict:
    """Sync platform instruction files (CLAUDE.md, AGENTS.md, etc.).

    Writes to one or more instruction files based on ``config.target_platforms``.
    Delegates to the canonical ``execute_claude_md_sync`` implementation which
    handles per-platform file generation.
    """
    from trw_mcp.clients.llm import LLMClient

    config = get_config()
    reader = FileStateReader()
    # Use a no-op LLM client — deliver path doesn't need LLM summarisation.
    llm = LLMClient()

    # Derive client param from config.target_platforms so deliver writes
    # to the correct instruction files (CLAUDE.md, AGENTS.md, or both).
    platforms = config.target_platforms
    if len(platforms) == 1:
        client = platforms[0]
    elif len(platforms) > 1:
        client = "all"
    else:
        client = "auto"

    raw = execute_claude_md_sync(
        scope="root",
        target_dir=None,
        config=config,
        reader=reader,
        llm=llm,
        client=client,
    )
    # Normalise status for backward compatibility with deliver callers.
    raw["status"] = "success"
    return cast("ClaudeMdSyncResultDict", raw)


# ── Tool registration ─────────────────────────────────────────────────


def register_ceremony_tools(server: FastMCP) -> None:  # noqa: C901 — tool registration with 6 nested tool defs
    """Register session ceremony composite tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_session_start(query: str = "") -> SessionStartResultDict:  # noqa: C901 — complex session start orchestration
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

        See Also: trw_init, trw_recall
        """
        from trw_mcp.tools._ceremony_helpers import (
            _phase_contextual_recall,
            perform_session_recalls,
            step_ceremony_nudge,
            step_embed_health,
            step_increment_session_counter,
            step_log_session_event,
            step_mark_session_started,
            step_sanitize_and_maintain,
            step_telemetry_startup,
        )

        config = get_config()
        reader = FileStateReader()
        results: SessionStartResultDict = {"timestamp": datetime.now(timezone.utc).isoformat()}
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
            if "query" in extra:
                results["query"] = str(extra["query"])
            if "query_matched" in extra:
                results["query_matched"] = int(str(extra["query_matched"]))
            if "total_available" in extra:
                results["total_available"] = int(str(extra["total_available"]))
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
                logger.info("session_start_no_active_run")
                results["run"] = {"active_run": None, "status": "no_active_run"}
        except Exception as exc:  # justified: fail-open, run status check must not block session start
            errors.append(f"status: {exc}")
            results["run"] = {"active_run": None, "status": "error"}

        # Step 3: Log session_start event (FR01, PRD-CORE-031)
        try:
            step_log_session_event(run_dir, cast("dict[str, object]", results), query, is_focused)
        except Exception:  # justified: fail-open, event logging must not block session start
            logger.debug("session_event_write_failed", exc_info=True)

        # Step 3b: Queue SessionStartEvent for telemetry publishing
        try:
            step_telemetry_startup(cast("dict[str, object]", results), run_dir)
        except Exception:  # justified: fail-open, telemetry publish must not block session start
            logger.debug("session_telemetry_failed", exc_info=True)

        # Step 3c: Increment sessions_tracked counter (FIX-050-FR06)
        try:
            step_increment_session_counter()
        except Exception:  # justified: fail-open, counter increment must not block session start
            logger.debug("session_counter_increment_failed", exc_info=True)

        # Steps 3d, 4-5, 7: Auto-maintenance (upgrade, stale runs, embeddings, sanitization)
        try:
            maintenance = step_sanitize_and_maintain(run_dir)
            for key in (
                "update_advisory",
                "auto_upgrade",
                "stale_runs_closed",
                "embeddings_advisory",
                "embeddings_backfill",
            ):
                if key in maintenance:
                    results[key] = maintenance[key]
        except Exception:  # justified: fail-open, auto-maintenance must not block session start
            logger.debug("session_maintenance_failed", exc_info=True)

        # Step 6: Phase-contextual auto-recall (PRD-CORE-049)
        try:
            if config.auto_recall_enabled:
                trw_dir_ar = resolve_trw_dir()
                run_status_obj: RunStatusDict | None = results.get("run")
                phase_recalled = _phase_contextual_recall(
                    trw_dir_ar, query, config, run_dir,
                    run_status_obj,
                )
                if phase_recalled:
                    results["auto_recalled"] = phase_recalled
                    results["auto_recall_count"] = len(phase_recalled)
        except Exception:  # justified: fail-open, auto-recall must not block session start
            logger.debug("session_auto_recall_failed", exc_info=True)

        # FR01 (PRD-FIX-053): Embed health advisory for agents.
        results["embed_health"] = step_embed_health()

        results["errors"] = errors
        results["success"] = len(errors) == 0

        # FR07 (PRD-CORE-084): Compact response for light ceremony mode.
        if config.effective_ceremony_mode == "light":
            results["framework_reminder"] = (
                "Call trw_deliver() when done to persist your work."
            )
        else:
            results["framework_reminder"] = (
                "Read .trw/frameworks/FRAMEWORK.md — it defines the methodology "
                "your tools implement (6-phase execution model, exit criteria, "
                "formations, quality gates, phase reversion). Re-read after "
                "context compaction."
            )

        # Mark session started in ceremony state tracker (PRD-CORE-074 FR04)
        with contextlib.suppress(Exception):
            step_mark_session_started()

        # Inject ceremony nudge into response (PRD-CORE-074 FR01, PRD-CORE-084 FR02)
        with contextlib.suppress(Exception):
            step_ceremony_nudge(cast("dict[str, object]", results), int(str(results.get("learnings_count", 0))))

        run_info: RunStatusDict | None = results.get("run")
        _active_run_id = str(run_info.get("active_run", "")) if run_info else ""
        _phase = str(run_info.get("phase", "")) if run_info else ""
        _task = str(run_info.get("task_name", "")) if run_info else ""
        _learnings_count = int(str(results.get("learnings_count", 0)))
        logger.info(
            "session_start_ok",
            run_id=_active_run_id,
            phase=_phase,
            task=_task,
            learnings_count=_learnings_count,
        )
        logger.debug(
            "session_start_learnings_loaded",
            count=_learnings_count,
        )
        return results

    @server.tool()
    @log_tool_call
    def trw_deliver(  # noqa: C901 — delivery lifecycle with deferred background steps
        run_path: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
    ) -> DeliverResultDict:
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

        See Also: trw_checkpoint, trw_claude_md_sync
        """
        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()
        t0 = time.monotonic()
        results: DeliverResultDict = {"timestamp": datetime.now(timezone.utc).isoformat()}
        errors: list[str] = []
        trw_dir = resolve_trw_dir()

        # Resolve run path
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        results["run_path"] = str(resolved_run) if resolved_run else None

        logger.info(
            "deliver_started",
            run_id=str(resolved_run.name) if resolved_run else "",
            phase="DELIVER",
        )

        # Auto-update phase to DELIVER
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(resolved_run, Phase.DELIVER)

        # Steps 0, 0b, premature guard: extracted to helper
        from trw_mcp.tools._ceremony_helpers import check_delivery_gates

        gate_result = check_delivery_gates(resolved_run, reader)
        # Unpack DeliveryGatesDict keys explicitly (all declared in DeliverResultDict)
        if "review_warning" in gate_result:
            results["review_warning"] = gate_result["review_warning"]
        if "review_advisory" in gate_result:
            results["review_advisory"] = gate_result["review_advisory"]
        if "integration_review_block" in gate_result:
            results["integration_review_block"] = gate_result["integration_review_block"]
        if "integration_review_warning" in gate_result:
            results["integration_review_warning"] = gate_result["integration_review_warning"]
        if "untracked_warning" in gate_result:
            results["untracked_warning"] = gate_result["untracked_warning"]
        if "build_gate_warning" in gate_result:
            results["build_gate_warning"] = gate_result["build_gate_warning"]
        if "warning" in gate_result:
            results["warning"] = gate_result["warning"]
        if "review_scope_block" in gate_result:
            results["review_scope_block"] = gate_result["review_scope_block"]
        if "checkpoint_blocker_warning" in gate_result:
            results["checkpoint_blocker_warning"] = gate_result["checkpoint_blocker_warning"]
        if "complexity_drift_warning" in gate_result:
            results["complexity_drift_warning"] = gate_result["complexity_drift_warning"]

        # Step 0c: Copy compliance artifacts (INFRA-027-FR05)
        from trw_mcp.tools._ceremony_helpers import copy_compliance_artifacts
        compliance_result = copy_compliance_artifacts(resolved_run, trw_dir, config, reader, writer)
        if "compliance_artifacts_copied" in compliance_result:
            results["compliance_artifacts_copied"] = compliance_result["compliance_artifacts_copied"]
        if "compliance_dir" in compliance_result:
            results["compliance_dir"] = compliance_result["compliance_dir"]

        # Block delivery if integration review has blocking verdict
        if gate_result.get("integration_review_block"):
            errors.append(str(gate_result["integration_review_block"]))
            results["errors"] = errors
            results["success"] = False
            return results

        # Block delivery if >5 files modified without review (R-01)
        if gate_result.get("review_scope_block"):
            errors.append(str(gate_result["review_scope_block"]))
            results["errors"] = errors
            results["success"] = False
            return results

        # -- CRITICAL PATH (synchronous) --
        # These 3 steps must complete before returning — they produce the
        # artifacts the next session depends on.

        # Use a typed accumulator view for _run_step (which operates on dict[str, object])
        _results_view: dict[str, object] = cast("dict[str, object]", results)

        # Step 1: Reflect (extract learnings from events)
        if not skip_reflect:
            _run_step("reflect", lambda: _do_reflect(trw_dir, resolved_run), _results_view, errors)
        else:
            results["reflect"] = {"status": "skipped"}

        # Step 2: Checkpoint (delivery state snapshot)
        if resolved_run is not None:
            _run_step("checkpoint", lambda: _step_checkpoint(resolved_run), _results_view, errors)
        else:
            results["checkpoint"] = {"status": "skipped", "reason": "no_active_run"}

        # Step 3: CLAUDE.md sync removed (PRD-CORE-093 FR06).
        # Learning promotion no longer rotates CLAUDE.md content, so the prompt
        # cache stays stable across delivers. Explicit trw_claude_md_sync() or
        # update_project() remain the only triggers for CLAUDE.md re-render.
        results["claude_md_sync"] = {"status": "skipped", "reason": "PRD-CORE-093"}

        critical_elapsed = round(time.monotonic() - t0, 2)
        results["critical_elapsed_seconds"] = critical_elapsed

        # -- DEFERRED PATH (background thread) --
        # Housekeeping, analytics, publishing, and telemetry — these don't
        # affect the next session's startup and can run after we return.
        # Concurrency-safe: file lock prevents overlapping deferred batches.
        deferred_status = _launch_deferred(
            trw_dir, resolved_run, _results_view,
            skip_index_sync=skip_index_sync,
        )
        results["deferred"] = deferred_status

        # Count only critical steps for immediate success evaluation
        critical_step_count = 2  # reflect + checkpoint (claude_md_sync removed per PRD-CORE-093)
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

        # Mark deliver called in ceremony state tracker (PRD-CORE-074 FR04)
        try:
            from trw_mcp.state.ceremony_nudge import mark_deliver
            mark_deliver(trw_dir)
        except Exception:  # justified: fail-open, ceremony state tracking must not block delivery
            logger.debug("deliver_ceremony_state_update_skipped", exc_info=True)

        # Inject ceremony nudge into response (PRD-CORE-084 FR02)
        try:
            from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName
            from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge
            ctx = NudgeContext(tool_name=ToolName.DELIVER)
            append_ceremony_nudge(cast("dict[str, object]", results), trw_dir, context=ctx)
        except Exception:  # justified: fail-open, nudge injection is advisory and must not block delivery
            logger.debug("deliver_nudge_injection_skipped", exc_info=True)

        _deliver_run_id = str(resolved_run.name) if resolved_run else ""
        _events_jsonl = resolved_run / "meta" / "events.jsonl" if resolved_run else None
        _events_logged = len(reader.read_jsonl(_events_jsonl)) if _events_jsonl and _events_jsonl.exists() else 0
        if len(errors) == 0:
            logger.info(
                "deliver_ok",
                run_id=_deliver_run_id,
                task=str(results.get("run_path", "")),
                events_logged=_events_logged,
            )
        else:
            logger.warning(
                "deliver_failed",
                run_id=_deliver_run_id,
                errors=errors,
            )
        if deferred_status == "skipped_already_running":
            logger.warning("deliver_deferred", reason="background_thread_running")
        logger.info(
            "trw_deliver_complete",
            critical_steps=results.get("critical_steps_completed"),
            deferred=deferred_status,
            critical_elapsed=critical_elapsed,
            errors=len(errors),
        )
        return results
