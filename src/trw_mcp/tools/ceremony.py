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

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    ClaudeMdSyncResultDict,
    DeliverResultDict,
    ReflectResultDict,
    RunStatusDict,
    SessionStartResultDict,
)
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
    pin_active_run,
    resolve_pin_key,
    resolve_trw_dir,
)
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


def _build_call_context(ctx: Context | None) -> TRWCallContext:
    """Construct a :class:`TRWCallContext` from a FastMCP ``Context`` (PRD-CORE-141 FR01+FR03).

    Resolves the pin-key via :func:`resolve_pin_key` (four-layer precedence),
    captures whatever raw FastMCP session probe hits for diagnostics, and
    returns a frozen value object suitable for threading through pin-state
    helpers.  Safe to call with ``ctx=None`` — the resolver falls back to
    env / process identity.
    """
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,  # Wave 4 may populate from user-agent header
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )


def _write_session_start_ids(trw_dir: Path, learnings: list[dict[str, object]]) -> None:
    """Write learning IDs from session_start to the injected-IDs state file.

    PRD-CORE-095 FR16: Prevents the auto-injection hook from re-injecting
    learnings that session_start already surfaced.
    """
    ids = [str(e.get("id", "")) for e in learnings if e.get("id")]
    if not ids:
        return
    state_file = trw_dir / "context" / "injected_learning_ids.txt"
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with state_file.open("a", encoding="utf-8") as f:
            for lid in ids:
                f.write(lid + "\n")
    except OSError:  # justified: fail-open, missing/unreadable heartbeat falls back to checkpoint-only
        logger.debug("injected_ids_write_failed", exc_info=True)


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
        error_events,
        repeated_ops,
        trw_dir,
        max_errors=5,
        max_repeated=3,
    )

    # Success patterns are analytics data only — do NOT create learning entries
    # (PRD-FIX-021: suppress telemetry noise from "Success: X (Nx)" entries).

    if run_dir and (run_dir / "meta").exists():
        _events.log_event(
            run_dir / "meta" / "events.jsonl",
            "reflection_complete",
            {
                "reflection_id": "delivery",
                "scope": "delivery",
                "learnings_produced": len(new_learnings),
            },
        )

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
    return raw


# ── Self-reflection helpers (PRD-CORE-125 FR05) ──────────────────────


def _learning_reflection_message(learnings_count: int) -> str:
    """Return a self-reflection message based on session learning count.

    PRD-CORE-125 FR05: Informational reminder (never blocks delivery).
    - 0 learnings: reminder to record what was discovered
    - >0 learnings: positive reinforcement with count
    """
    if learnings_count > 0:
        return (
            f"{learnings_count} discovery/discoveries persisted for future sessions."
        )
    return (
        "Note: No discoveries were recorded this session. "
        "Consider what you learned \u2014 even a one-line root cause "
        "helps the next agent avoid re-discovery."
    )


# ── Tool registration ─────────────────────────────────────────────────


def register_ceremony_tools(server: FastMCP) -> None:  # noqa: C901 — tool registration with 6 nested tool defs
    """Register session ceremony composite tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_session_start(
        ctx: Context | None = None,
        query: str = "",
    ) -> SessionStartResultDict:  # noqa: C901 — complex session start orchestration
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
            record_session_start_surfaces,
            step_ceremony_status,
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
                trw_dir,
                query,
                config,
                reader,
            )
            results["learnings"] = learnings
            results["learnings_count"] = len(learnings)
            if "query" in extra:
                results["query"] = str(extra["query"])
            if "query_matched" in extra:
                results["query_matched"] = int(str(extra["query_matched"]))
            if "total_available" in extra:
                results["total_available"] = int(str(extra["total_available"]))
            # PRD-CORE-095 FR16: Pre-populate injected IDs so the auto-injection
            # hook doesn't re-inject learnings that session_start already surfaced.
            _write_session_start_ids(trw_dir, learnings)
        except Exception as exc:  # justified: fail-open, recall failure must not block session start
            errors.append(f"recall: {exc}")
            results["learnings"] = []
            results["learnings_count"] = 0

        # Step 2: Check active run status (and pin it for this process).
        # PRD-CORE-141 FR03/FR05/FR06: thread ctx through so fresh ctx-aware
        # sessions do NOT hijack another session's active run via the mtime
        # scan, and surface a structured ``hint`` field in the no-pin case.
        call_ctx = _build_call_context(ctx)
        run_dir: Path | None = None
        try:
            run_dir = find_active_run(context=call_ctx)
            if run_dir is not None:
                pin_active_run(run_dir, context=call_ctx)
                results["run"] = _get_run_status(run_dir)
            else:
                logger.info(
                    "session_start_no_active_run",
                    pin_key=call_ctx.session_id,
                )
                results["run"] = {"active_run": None, "status": "no_active_run"}
                # FR06: surface actionable guidance when no pin exists.
                results["hint"] = (
                    "No active run for this session. Call trw_init() to create one, "
                    "or pass run_path to resume an existing run."
                )
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
                    trw_dir_ar,
                    query,
                    config,
                    run_dir,
                    run_status_obj,
                )
                if phase_recalled:
                    primary_ids = {
                        str(entry.get("id", ""))
                        for entry in results.get("learnings", [])
                        if entry.get("id")
                    }
                    auto_ids = [
                        str(entry.get("id", ""))
                        for entry in phase_recalled
                        if entry.get("id") and str(entry.get("id", "")) not in primary_ids
                    ]
                    record_session_start_surfaces(trw_dir_ar, auto_ids)
                    results["auto_recalled"] = phase_recalled
                    results["auto_recall_count"] = len(phase_recalled)
        except Exception:  # justified: fail-open, auto-recall must not block session start
            logger.debug("session_auto_recall_failed", exc_info=True)

        # FR01 (PRD-FIX-053): Embed health advisory for agents.
        results["embed_health"] = step_embed_health()

        # FR07 (PRD-CORE-086): Assertion health summary from cached last_result fields.
        try:
            ah_start = time.monotonic()
            from trw_mcp.state.memory_adapter import get_backend

            ah_trw_dir = resolve_trw_dir()
            backend = get_backend(ah_trw_dir)
            if hasattr(backend, "entries_with_assertions"):
                entries_with_assertions = backend.entries_with_assertions()
                if entries_with_assertions:
                    from datetime import timedelta

                    stale_threshold = datetime.now(timezone.utc) - timedelta(days=7)
                    ah_passing = 0
                    ah_failing = 0
                    ah_stale = 0
                    ah_unverifiable = 0
                    for entry in entries_with_assertions:
                        for a in entry.assertions:
                            if a.last_verified_at is None or a.last_verified_at < stale_threshold:
                                ah_stale += 1
                            elif a.last_result is True:
                                ah_passing += 1
                            elif a.last_result is False:
                                ah_failing += 1
                            else:
                                ah_unverifiable += 1
                    results["assertion_health"] = {
                        "passing": ah_passing,
                        "failing": ah_failing,
                        "stale": ah_stale,
                        "unverifiable": ah_unverifiable,
                        "total": len(entries_with_assertions),
                    }
            ah_ms = (time.monotonic() - ah_start) * 1000
            logger.debug("assertion_health_computed", duration_ms=round(ah_ms, 1))
        except Exception:  # justified: fail-open, assertion health must not block session start
            logger.debug("assertion_health_failed", exc_info=True)

        results["errors"] = errors
        results["success"] = len(errors) == 0

        # FR07 (PRD-CORE-084): Compact response for light ceremony mode.
        if config.effective_ceremony_mode == "light":
            results["framework_reminder"] = "Call trw_deliver() when done to persist your work."
        else:
            results["framework_reminder"] = (
                "Read .trw/frameworks/FRAMEWORK.md — it defines the methodology "
                "your tools implement (6-phase execution model, exit criteria, "
                "formations, quality gates, phase reversion). Re-read after "
                "context compaction."
            )

        # Mark session started in ceremony state (PRD-CORE-074 FR04)
        try:
            step_mark_session_started()
        except Exception:  # justified: fail-open, state mutation must not block session start
            logger.debug("session_mark_started_failed", exc_info=True)

        # Inject ceremony progress summary when full ceremony mode is active.
        try:
            step_ceremony_status(cast("dict[str, object]", results))
        except Exception:  # justified: fail-open, status decoration must not block session start
            logger.debug("session_ceremony_status_failed", exc_info=True)

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

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_deliver(  # noqa: C901 — delivery lifecycle with deferred background steps
        ctx: Context | None = None,
        run_path: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
    ) -> DeliverResultDict:
        """Persist your learnings and progress for future sessions — without this, your work is invisible to the next agent.

        When to call: as your last action before the session ends. Before calling,
        check: did you record at least one discovery with trw_learn()? If not,
        record the root cause or approach that worked — even a one-line learning
        helps the next agent avoid re-discovery.

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

        # Resolve run path (PRD-CORE-141 FR03/FR05: ctx-aware find_active_run
        # suppresses scan fallback for fresh sessions).
        call_ctx = _build_call_context(ctx)
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run(context=call_ctx)

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

        # Step 3b: DB integrity check on delivery (PRD-INFRA-067 / C2)
        # Observability only — a failed probe is logged at WARNING but does
        # not block deliver or trigger recovery.
        try:
            from trw_mcp.tools._deliver_integrity import check_memory_integrity_on_deliver

            integrity_result = check_memory_integrity_on_deliver(trw_dir, resolved_run)
            results["db_integrity"] = cast("dict[str, object]", dict(integrity_result))
        except Exception:  # justified: fail-open — integrity probe must not block deliver
            logger.debug("deliver_integrity_check_failed", exc_info=True)

        # -- DEFERRED PATH (background thread) --
        # Housekeeping, analytics, publishing, and telemetry — these don't
        # affect the next session's startup and can run after we return.
        # Concurrency-safe: file lock prevents overlapping deferred batches.
        deferred_status = _launch_deferred(
            trw_dir,
            resolved_run,
            _results_view,
            skip_index_sync=skip_index_sync,
        )
        results["deferred"] = deferred_status

        # Count only critical steps for immediate success evaluation
        critical_step_count = 2  # reflect + checkpoint (claude_md_sync removed per PRD-CORE-093)
        results["errors"] = errors
        results["success"] = len(errors) == 0
        results["critical_steps_completed"] = critical_step_count - len(errors)
        results["deferred_steps"] = 11  # launched in background

        # Mark deliver in ceremony state (PRD-CORE-124 FR-deliver)
        try:
            from trw_mcp.state.ceremony_progress import mark_deliver

            mark_deliver(trw_dir)
        except Exception:  # justified: fail-open — state mutation must not block deliver
            logger.debug("mark_deliver_failed", exc_info=True)

        # PRD-CORE-125 FR05: Self-reflection gate — learning count feedback
        try:
            from trw_mcp.state.ceremony_progress import read_ceremony_state as _read_cs_fr05

            _cs_fr05 = _read_cs_fr05(trw_dir)
            _learnings_count_fr05 = _cs_fr05.learnings_this_session
            results["learning_reflection"] = _learning_reflection_message(_learnings_count_fr05)
        except Exception:  # justified: fail-open — reflection must not block deliver
            logger.debug("learning_reflection_failed", exc_info=True)

        # PRD-QUAL-058-FR05: Read nudge_counts from CeremonyState for deliver event
        _nudge_summary: dict[str, int] = {}
        try:
            from trw_mcp.state.ceremony_progress import read_ceremony_state as _read_cs

            _cs = _read_cs(trw_dir)
            _nudge_summary = dict(_cs.nudge_counts)
        except Exception:  # justified: fail-open
            logger.debug("deliver_nudge_summary_unavailable", exc_info=True)

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
                    # PRD-QUAL-058-FR05: Aggregate nudge signal at deliver time
                    "nudge_summary": _nudge_summary,
                },
            )

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
