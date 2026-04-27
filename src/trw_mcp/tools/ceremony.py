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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

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
    TrwAdoptRunResultDict,
    TrwHeartbeatResultDict,
)
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
    pin_active_run,
    resolve_pin_key,
    resolve_trw_dir,
)
from trw_mcp.state._pin_store import (
    _iso_now,
    get_pin_entry,
    load_pin_store,
    remove_pin_entry,
    upsert_pin_entry,
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
    try:
        raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    except Exception:
        raw_session = None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,  # Wave 4 may populate from user-agent header
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )


def _find_active_run_compat(call_ctx: TRWCallContext) -> Path | None:
    """Call ctx-aware ``find_active_run`` when supported, else fall back."""
    try:
        return find_active_run(context=call_ctx)
    except TypeError:
        return find_active_run()


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


def _persist_surface_snapshot_pointer(run_dir: Path, snapshot_id: str) -> None:
    """Persist the run's surface snapshot pointer into ``run.yaml``.

    FR-2 requires the resolved ``surface_snapshot_id`` be discoverable from
    the session's ``run.yaml`` in addition to the immutable
    ``run_surface_snapshot.yaml`` file under ``meta/``.
    """
    run_yaml = run_dir / "meta" / "run.yaml"
    if not run_yaml.exists():
        return
    reader = FileStateReader()
    writer = FileStateWriter()
    try:
        data = reader.read_yaml(run_yaml)
        data["surface_snapshot_id"] = snapshot_id
        data["run_surface_snapshot_path"] = "meta/run_surface_snapshot.yaml"
        writer.write_yaml(run_yaml, data)
    except Exception:  # justified: fail-open, pointer persistence must not block session start
        logger.warning(
            "surface_snapshot_pointer_persist_failed",
            run_dir=str(run_dir),
            snapshot_id=snapshot_id,
            exc_info=True,
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
        return f"{learnings_count} discovery/discoveries persisted for future sessions."
    return (
        "Note: No discoveries were recorded this session. "
        "Consider what you learned \u2014 even a one-line root cause "
        "helps the next agent avoid re-discovery."
    )


# ── Pin-isolation helpers (PRD-CORE-141 FR07/FR08) ───────────────────


def _parse_iso_utc(ts: str) -> datetime | None:
    """Parse an ISO 8601 UTC timestamp tolerating the ``Z`` suffix.

    Returns ``None`` when the value is empty or unparseable so heartbeat
    / adopt callers can degrade gracefully without crashing the tool.
    """
    if not ts:
        return None
    try:
        # datetime.fromisoformat in 3.11+ accepts "Z" via "+00:00" swap.
        normalized = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _timedelta_hours(hours: float) -> timedelta:
    """Return a ``timedelta`` spanning *hours* (isolated for patchability)."""
    return timedelta(hours=hours)


def _compute_run_age_hours(run_dir: Path | None) -> float:
    """Return the run's age in hours from its run.yaml ``created_at`` field.

    Falls back to file mtime, then ``0.0`` on unreadable state.  Used by
    ``trw_heartbeat`` to decide ``should_checkpoint`` without throwing if
    the run.yaml is missing timestamp metadata.
    """
    if run_dir is None:
        return 0.0
    run_yaml = run_dir / "meta" / "run.yaml"
    if not run_yaml.exists():
        return 0.0
    try:
        reader = FileStateReader()
        data = reader.read_yaml(run_yaml)
        for key in ("created_at", "created_ts", "started_at"):
            val = data.get(key)
            parsed: datetime | None = None
            if isinstance(val, datetime):
                parsed = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            elif isinstance(val, str) and val:
                parsed = _parse_iso_utc(val)
            if parsed is not None:
                return max(
                    0.0,
                    (datetime.now(timezone.utc) - parsed).total_seconds() / 3600.0,
                )
    except Exception:  # justified: fail-open — run-age probe must not raise
        logger.debug("run_age_read_failed", run_path=str(run_dir), exc_info=True)

    # Fallback: use file mtime.
    try:
        mtime = run_yaml.stat().st_mtime
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - mtime_dt).total_seconds() / 3600.0)
    except OSError:
        return 0.0


# ── Tool registration ─────────────────────────────────────────────────


def register_ceremony_tools(server: FastMCP) -> None:
    """Register session ceremony composite tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_session_start(
        ctx: Context | None = None,
        query: str = "",
    ) -> SessionStartResultDict:
        """Load prior learnings + any active run so you start with full context.

        Use when:
        - Starting a new session (first action, before reading code or editing).
        - Resuming after context compaction and you need the pin and learnings reloaded.
        - Switching onto an unfamiliar task and want a focused recall on the topic.

        Recalls high-impact learnings (patterns, gotchas, architecture decisions) and
        checks for an active run (phase, progress, last checkpoint). Partial-failure
        resilient: a failure in one sub-step does not block the others.

        Input:
        - query: optional focus string. When set, performs a focused recall on your
          topic AND a baseline high-impact recall, then merges + dedupes. Empty
          string or "*" uses default wildcard behavior.

        Output: SessionStartResultDict with fields
        {learnings: list, learnings_count: int, run: RunStatusDict,
         auto_recalled?: list, embed_health: dict, assertion_health?: dict,
         framework_reminder: str, errors: list, success: bool}.

        Example:
            trw_session_start(query="sqlite extension macos")
            → {"learnings": [...], "learnings_count": 8,
               "run": {"active_run": "/path/...", "phase": "IMPLEMENT"}, ...}

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

        # PRD-HPO-MEAS-001 NFR-12 / FR-13: fail at boot, before any session
        # telemetry or startup artifacts are written.
        from trw_mcp.telemetry.boot_audit import run_boot_audit

        run_boot_audit()

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
            run_dir = _find_active_run_compat(call_ctx)
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

        # Step 2c: Resolve surface snapshot + stamp run_surface_snapshot.yaml
        # (PRD-HPO-MEAS-001 FR-1 / FR-2).
        # - Always resolves the SurfaceRegistry so surface_snapshot_id is
        #   available for downstream event emitters.
        # - When a run_dir is pinned, writes the immutable
        #   run_surface_snapshot.yaml frozen copy under <run_dir>/meta/.
        # - Failure is non-fatal by design (fail-open) — the empty-string
        #   Phase-1 default remains available on HPOTelemetryEvent.
        surface_snapshot_id: str = ""
        try:
            from trw_mcp.telemetry.artifact_registry import SurfaceRegistry, resolve_surface_registry
            from trw_mcp.telemetry.surface_manifest import stamp_session

            if run_dir is not None:
                registry = SurfaceRegistry.build_and_emit(
                    session_id=str(call_ctx.session_id),
                    run_id=run_dir.name,
                    run_dir=run_dir,
                )
                surface_snapshot_id = registry.snapshot_id
                stamp_session(run_dir / "meta")
                _persist_surface_snapshot_pointer(run_dir, surface_snapshot_id)
            else:
                registry = resolve_surface_registry()
                surface_snapshot_id = registry.snapshot_id
            results["surface_snapshot_id"] = surface_snapshot_id
            logger.debug(
                "surface_snapshot_stamped",
                snapshot_id=surface_snapshot_id,
                run_dir=str(run_dir) if run_dir else "",
                artifact_count=len(registry.artifacts),
            )
        except Exception:  # justified: fail-open, surface stamping must not block session start
            logger.debug("surface_snapshot_stamp_failed", exc_info=True)
            results["surface_snapshot_id"] = ""

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
                        str(entry.get("id", "")) for entry in results.get("learnings", []) if entry.get("id")
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
    def trw_deliver(
        ctx: Context | None = None,
        run_path: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
    ) -> DeliverResultDict:
        """Persist learnings and progress so future sessions inherit this session's work.

        Use when:
        - Your session is about to end and you want discoveries to persist for future agents.
        - A milestone is reached and you want to close out the current run directory.

        Before calling, check: did you record at least one discovery with
        trw_learn? If not, add even a one-line root-cause learning so the next
        agent avoids re-discovery.

        Runs reflect + checkpoint synchronously, then launches housekeeping
        (consolidation, publish, telemetry, tier sweep) in the background.
        Background work is concurrency-safe — overlapping batches are skipped
        rather than queued.

        Input:
        - run_path: path to run directory (auto-detected if None).
        - skip_reflect: skip reflection step (e.g., already reflected).
        - skip_index_sync: skip INDEX/ROADMAP sync step.

        Output: DeliverResultDict with fields
        {run_path: str, reflect: dict, checkpoint: dict, deferred: str,
         critical_steps_completed: int, deferred_steps: int, errors: list,
         success: bool, learning_reflection?: str}.

        Example:
            trw_deliver()
            → {"run_path": "/path/...", "critical_steps_completed": 2,
               "deferred": "launched", "success": true}

        See Also: trw_checkpoint, trw_instructions_sync
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
            resolved_run = _find_active_run_compat(call_ctx)

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
        # cache stays stable across delivers. Explicit trw_instructions_sync() or
        # update_project() remain the only triggers for instruction-file re-render.
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

        # Step 3c: PRD-HPO-MEAS-001 FR-5 — compute + persist CLEAR score
        # for this session. One record per closed session; failure is
        # fail-open so the scorer never blocks deliver completion.
        if resolved_run is not None:
            try:
                from trw_mcp.scoring.clear import load_and_score_run

                session_id_for_clear = str(resolved_run.name)
                clear_score = load_and_score_run(session_id_for_clear, resolved_run)
                if clear_score is not None:
                    import json as _clear_json

                    clear_path = resolved_run / "meta" / "session_clear_score.json"
                    clear_path.write_text(
                        _clear_json.dumps(clear_score.model_dump(mode="json"), indent=2),
                        encoding="utf-8",
                    )
                    results["clear_score"] = cast("dict[str, object]", clear_score.model_dump(mode="json"))
                    logger.info(
                        "clear_score_persisted",
                        session_id=session_id_for_clear,
                        cost=clear_score.cost,
                        latency=clear_score.latency,
                        efficacy=clear_score.efficacy,
                        assurance=clear_score.assurance,
                        reliability=clear_score.reliability,
                    )
            except Exception:  # justified: fail-open — CLEAR scoring must not block deliver
                logger.debug("clear_score_step_failed", exc_info=True)

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

    # ── PRD-CORE-141 FR07 — trw_heartbeat ─────────────────────────────
    @server.tool(output_schema=None)
    @log_tool_call
    def trw_heartbeat(
        ctx: Context | None = None,
        message: str = "",
    ) -> TrwHeartbeatResultDict:
        """Refresh the caller's pin heartbeat and append a heartbeat event.

        Use when:
        - A long-running campaign needs to keep its pin alive between work units.
        - You want to probe whether the current run is stale enough to checkpoint.

        Rate-limit: if ``now - last_heartbeat_ts < 60s`` the call short-circuits
        (no events.jsonl append, no pin-store write) and returns
        ``rate_limited=True`` so long-running loops don't spam the audit trail.
        Rate-limit state lives in ``pins.json::<pin_key>::last_heartbeat_ts``
        so the 60s window survives server restart.

        Input:
        - message: optional context string logged alongside the heartbeat event.

        Output: TrwHeartbeatResultDict — on success
        {run_id, last_heartbeat_ts, stale_after_ts, age_hours, should_checkpoint,
        rate_limited}; on missing-pin
        {error: "no_active_pin", hint: "call trw_init or trw_adopt_run first"}.
        """
        pin_key = resolve_pin_key(ctx=ctx, explicit=None)
        raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
        # PRD-CORE-141: construct TRWCallContext for shape parity with other
        # ctx-aware tools.  Reserved for future analytics hooks (e.g. pin-key
        # logging on heartbeat events) — no downstream consumer today.
        _ = TRWCallContext(
            session_id=pin_key,
            client_hint=None,
            explicit=False,
            fastmcp_session=raw_session if isinstance(raw_session, str) else None,
        )

        entry = get_pin_entry(pin_key)
        if entry is None:
            logger.warning("trw_heartbeat_no_pin", pin_key=pin_key)
            return {
                "error": "no_active_pin",
                "hint": "call trw_init or trw_adopt_run first",
            }

        config = get_config()
        now_dt = datetime.now(timezone.utc)

        # Rate-limit check — parse last heartbeat from on-disk record.
        last_ts_str = str(entry.get("last_heartbeat_ts", "") or "")
        last_dt = _parse_iso_utc(last_ts_str)
        rate_limited = False
        if last_dt is not None and (now_dt - last_dt).total_seconds() < 60.0:
            rate_limited = True

        run_path_str = str(entry.get("run_path", "") or "")
        run_dir = Path(run_path_str) if run_path_str else None
        run_id = run_dir.name if run_dir is not None else ""

        age_hours = _compute_run_age_hours(run_dir)
        stale_after_ts = ""
        should_checkpoint = False

        if rate_limited:
            # Short-circuit path: return the existing state, no writes.
            if last_dt is not None:
                stale_after_ts = (last_dt + _timedelta_hours(config.run_staleness_hours)).isoformat()
            should_checkpoint = age_hours > float(config.checkpoint_suggest_hours)
            logger.debug(
                "trw_heartbeat_rate_limited",
                pin_key=pin_key,
                run_id=run_id,
                age_hours=age_hours,
            )
            return {
                "run_id": run_id,
                "last_heartbeat_ts": last_ts_str,
                "stale_after_ts": stale_after_ts,
                "age_hours": age_hours,
                "should_checkpoint": should_checkpoint,
                "rate_limited": True,
            }

        # Normal path: write pin + append event.
        new_ts = _iso_now()
        # Mutate the entry preserving created_ts and client_hint.
        upsert_pin_entry(
            pin_key,
            Path(run_path_str) if run_path_str else Path("."),
            client_hint=entry.get("client_hint") if isinstance(entry.get("client_hint"), str) else None,
        )

        if run_dir is not None and (run_dir / "meta").exists():
            _events.log_event(
                run_dir / "meta" / "events.jsonl",
                "heartbeat",
                {
                    "message": message,
                    "pin_key": pin_key,
                },
            )

        stale_after_ts = (now_dt + _timedelta_hours(config.run_staleness_hours)).isoformat()
        should_checkpoint = age_hours > float(config.checkpoint_suggest_hours)

        logger.debug(
            "trw_heartbeat_applied",
            pin_key=pin_key,
            run_id=run_id,
            age_hours=age_hours,
            should_checkpoint=should_checkpoint,
        )
        return {
            "run_id": run_id,
            "last_heartbeat_ts": new_ts,
            "stale_after_ts": stale_after_ts,
            "age_hours": age_hours,
            "should_checkpoint": should_checkpoint,
            "rate_limited": False,
        }

    # ── PRD-CORE-141 FR08 — trw_adopt_run ─────────────────────────────
    @server.tool(output_schema=None)
    @log_tool_call
    def trw_adopt_run(
        ctx: Context | None = None,
        run_path: str = "",
        force: bool = False,
    ) -> TrwAdoptRunResultDict:
        """Transfer an existing run's pin to the caller's session.

        Use when:
        - Resuming a run started by another session (fresh context, same task).
        - Reclaiming a run whose previous owner went away without delivering.

        Guards:
        - Out-of-project run_path raises StateError (no force override).
        - Terminal status (delivered/complete/failed) requires force=True.
        - Live owner (heartbeat within pin_ttl_hours) requires force=True and
          emits ``run_adopted_potential_writer_conflict`` WARN when displaced.

        Input:
        - run_path: absolute path to the run directory to adopt (required).
        - force: override terminal-status and live-owner guards.

        Output: TrwAdoptRunResultDict with fields
        {adopted_run_id, previous_pin_key, from_pin_key, to_pin_key,
        adopted_ts, from_owner_was_live, force_used}.

        Example:
            trw_adopt_run(run_path="/repo/.trw/runs/<task>/<id>")
            → {"adopted_run_id": "<id>", "from_pin_key": "sess-a",
               "to_pin_key": "sess-b", "force_used": false, ...}
        """
        if not run_path:
            raise StateError("run_path is required for trw_adopt_run")

        # Containment check — must be under project root.  No force override.
        # Import lazily so conftest's monkeypatch of the source attribute
        # reaches this call site (FR08 containment).
        from trw_mcp.state._paths import resolve_project_root

        resolved = Path(run_path).resolve()
        project_root = resolve_project_root()
        if not resolved.is_relative_to(project_root):
            raise StateError(
                f"run_path escapes project root: {resolved}",
                path=str(resolved),
            )
        if not resolved.exists():
            raise StateError(f"run_path does not exist: {resolved}", path=str(resolved))

        # Resolve caller pin key.
        caller_pin_key = resolve_pin_key(ctx=ctx, explicit=None)

        # Read target run's status from run.yaml.
        run_yaml = resolved / "meta" / "run.yaml"
        target_status = "unknown"
        if run_yaml.exists():
            try:
                reader = FileStateReader()
                data = reader.read_yaml(run_yaml)
                target_status = str(data.get("status", "unknown"))
            except Exception:  # justified: fail-open, unreadable run.yaml treated as unknown
                logger.debug("adopt_run_read_status_failed", run_path=str(resolved), exc_info=True)

        if target_status in ("delivered", "complete", "failed") and not force:
            raise StateError(
                f"cannot adopt terminal-status run (status={target_status}); pass force=True to override",
                path=str(resolved),
                status=target_status,
            )

        # Find existing pin entry for the target run path (scan the store).
        store = load_pin_store()
        previous_pin_key: str | None = None
        previous_entry: dict[str, Any] | None = None
        target_str = str(resolved)
        for pkey, pentry in store.items():
            if not isinstance(pentry, dict):
                continue
            if str(pentry.get("run_path", "")) == target_str:
                previous_pin_key = pkey
                previous_entry = pentry
                break

        # Live-owner check.
        from_owner_was_live = False
        previous_owner_heartbeat_age_hours: float | None = None
        config = get_config()
        if previous_entry is not None:
            prev_last_ts = _parse_iso_utc(str(previous_entry.get("last_heartbeat_ts", "") or ""))
            if prev_last_ts is not None:
                age_s = (datetime.now(timezone.utc) - prev_last_ts).total_seconds()
                previous_owner_heartbeat_age_hours = age_s / 3600.0
                if age_s < float(config.pin_ttl_hours) * 3600.0:
                    from_owner_was_live = True

        if from_owner_was_live and not force:
            raise StateError(
                "run is actively held by a live pin; pass force=True to override",
                path=str(resolved),
                pin_key=previous_pin_key,
            )

        # Perform the adoption.  Remove prior entry, then upsert the caller's
        # pin pointing at the target run.  Atomic across two save calls is
        # acceptable — the intermediate state (pin gone briefly) is no
        # worse than a crash between the calls, and the file lock in the
        # pin store serializes both writes.
        if previous_pin_key is not None and previous_pin_key != caller_pin_key:
            remove_pin_entry(previous_pin_key)

        upsert_pin_entry(caller_pin_key, resolved)

        adopted_ts = _iso_now()

        if from_owner_was_live and force:
            structlog.get_logger(__name__).warning(
                "run_adopted_potential_writer_conflict",
                previous_pin_key=previous_pin_key,
                previous_owner_heartbeat_age_hours=previous_owner_heartbeat_age_hours,
                new_pin_key=caller_pin_key,
                run_path=str(resolved),
            )

        # Append run_adopted event.
        if (resolved / "meta").exists():
            _events.log_event(
                resolved / "meta" / "events.jsonl",
                "run_adopted",
                {
                    "from_pin_key": previous_pin_key,
                    "to_pin_key": caller_pin_key,
                    "force_used": force,
                    "previous_owner_heartbeat_age_hours": previous_owner_heartbeat_age_hours,
                },
            )

        logger.info(
            "run_adopted",
            run_path=str(resolved),
            from_pin_key=previous_pin_key,
            to_pin_key=caller_pin_key,
            force_used=force,
            from_owner_was_live=from_owner_was_live,
        )

        return {
            "adopted_run_id": resolved.name,
            "previous_pin_key": previous_pin_key,
            "from_pin_key": previous_pin_key,
            "to_pin_key": caller_pin_key,
            "adopted_ts": adopted_ts,
            "from_owner_was_live": from_owner_was_live,
            "force_used": force,
        }
