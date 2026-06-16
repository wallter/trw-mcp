# ruff: noqa: E402
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

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    DeliverResultDict,
    SessionStartResultDict,
    TrwAdoptRunResultDict,
    TrwHeartbeatResultDict,
)
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
    resolve_pin_key,
    resolve_trw_dir,
)

# Re-exported so the claude_md sync path (resolved via
# ``getattr(trw_mcp.tools.ceremony, "execute_claude_md_sync", ...)`` in
# _ceremony_runtime_helpers) and tests can patch it at this facade. A
# refactor dropped the binding; restoring it keeps the monkeypatch
# indirection (and the runtime getattr lookup) working.
from trw_mcp.state.claude_md import execute_claude_md_sync as execute_claude_md_sync
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateWriter,
)
from trw_mcp.tools._ceremony_adopt_run import adopt_run as _adopt_run_impl
from trw_mcp.tools._ceremony_deliver_tool import run_trw_deliver as _run_trw_deliver
from trw_mcp.tools._ceremony_heartbeat import compute_heartbeat_result
from trw_mcp.tools._ceremony_profile_step import (
    step_resolve_profile as step_resolve_profile,
)
from trw_mcp.tools._ceremony_telemetry import (
    step_first_session_marker as step_first_session_marker,
)
from trw_mcp.tools._deferred_delivery import _step_checkpoint as _step_checkpoint
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())
_STEP_CHECKPOINT_PATCH_SEAM = _step_checkpoint

# F24 (legibility): advisory delivery-gate warning keys. These are SOFT gates —
# surfaced on the deliver result but never blocking — so historically a
# "warned-but-delivered" run was byte-identical (success=True, no warning
# signal) to a fully-clean one. We aggregate the subset of these keys actually
# present on the result into warning_count / warnings_present / warnings so
# downstream eval / false-completion scoring can separate clean vs warned
# delivers. Blocking keys (review_block, *_scope_block, build_gate_*,
# delivery_blocked, truthfulness_gate_bypassed) are deliberately EXCLUDED —
# they already drive success=False or their own audit trail and are not
# "advisory warnings on an otherwise-successful deliver".
_ADVISORY_WARNING_KEYS: tuple[str, ...] = (
    "review_warning",
    "review_advisory",
    "integration_review_warning",
    "checkpoint_blocker_warning",
    "untracked_warning",
    "complexity_drift_warning",
    "instruction_parity_warning",
    "warning",
)


def _aggregate_advisory_warnings(results: DeliverResultDict) -> None:
    """Populate warning_count / warnings_present / warnings on the result.

    Scans ``results`` for the advisory-warning keys in
    :data:`_ADVISORY_WARNING_KEYS` that are present and non-empty, and records
    an aggregate count, a boolean flag, and the sorted list of present keys.
    Pure legibility — does not touch ``success`` or any blocking behavior.
    """
    present = sorted(key for key in _ADVISORY_WARNING_KEYS if results.get(key))
    results["warnings"] = present
    results["warning_count"] = len(present)
    results["warnings_present"] = bool(present)


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
        try:
            return find_active_run(session_id=call_ctx.session_id)
        except TypeError:
            return find_active_run()  # compat: legacy zero-argument test doubles


# Runtime helpers extracted to _ceremony_runtime_helpers (PRD-DIST-243 batch 53).
from trw_mcp.tools._ceremony_runtime_helpers import (
    _candidate_run_hints as _candidate_run_hints,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _compute_run_age_hours as _compute_run_age_hours,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _do_instruction_sync as _do_instruction_sync,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _do_reflect as _do_reflect,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _get_run_status as _get_run_status,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _learning_reflection_message as _learning_reflection_message,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _mark_run_complete as _mark_run_complete,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _no_active_run_hint as _no_active_run_hint,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _parse_iso_utc as _parse_iso_utc,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _persist_surface_snapshot_pointer as _persist_surface_snapshot_pointer,
)
from trw_mcp.tools._ceremony_runtime_helpers import (
    _timedelta_hours as _timedelta_hours,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    finalize_session_start as finalize_session_start,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_assertion_health as step_assertion_health,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_auto_recall_orchestrated as step_auto_recall_orchestrated,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_graph_health as step_graph_health,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_phase_auto_recall as step_phase_auto_recall,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_pipeline_health_advisory as step_pipeline_health_advisory,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_recall_learnings as step_recall_learnings,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_run_resolve as step_run_resolve,
)
from trw_mcp.tools._ceremony_session_start_steps import (
    step_surface_stamp as step_surface_stamp,
)
from trw_mcp.tools._session_start_trim import (
    find_intentional_marker as find_intentional_marker,
)
from trw_mcp.tools._session_start_trim import (
    trim_session_start_payload as trim_session_start_payload,
)

# ── Tool registration ─────────────────────────────────────────────────


def register_ceremony_tools(server: FastMCP) -> None:
    """Register session ceremony composite tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_session_start(
        ctx: Context | None = None,
        query: str = "",
        verbose: bool = False,
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
        - verbose: when False (default) returns a COMPACT payload — the learnings
          list is capped to the top-K most relevant (with a ``learnings_omitted``
          "N more" indicator) and the low-signal diagnostic sub-blocks
          (embed_health/assertion_health/sync_health/step_durations_ms) are folded
          into a one-line ``health_summary`` to cut token cost. Run/pin recovery,
          errors, framework_reminder, and degraded advisories are always preserved.
          Set verbose=True for the full diagnostic payload (legacy behavior).

        Output: SessionStartResultDict with fields
        {learnings: list, learnings_count: int, learnings_omitted?: int,
         run: RunStatusDict, auto_recalled?: list, health_summary?: str (compact),
         embed_health?: dict (verbose), assertion_health?: dict (verbose),
         framework_reminder: str, errors: list, success: bool, compact: bool,
         payload_token_estimate: int}.

        Example:
            trw_session_start(query="sqlite extension macos")
            → {"learnings": [...], "learnings_count": 8, "compact": true,
               "health_summary": "embed=ok; start=42ms (verbose=True for ...)",
               "run": {"active_run": "/path/...", "phase": "IMPLEMENT"}, ...}

        See Also: trw_init, trw_recall
        """
        from trw_mcp.tools._ceremony_helpers import (
            step_embed_health,
            step_increment_session_counter,
            step_log_session_event,
            step_sanitize_and_maintain,
            step_sync_health,
            step_telemetry_startup,
        )

        config = get_config()
        results: SessionStartResultDict = {"timestamp": datetime.now(timezone.utc).isoformat()}
        errors: list[str] = []
        is_focused = query.strip() not in ("", "*")

        # PRD-FIX-085 FR02: mark this scope as HOT_PATH so any caller that
        # accidentally invokes the legacy mtime scan during session_start
        # emits a hot_path_legacy_scan_attempted WARN (or raises in
        # TRW_HOT_PATH_STRICT=1).  ContextVar is reset in a try/finally
        # block at the BOTTOM of trw_session_start so it always unwinds
        # even if a step raises.
        from trw_mcp.state._paths import HOT_PATH

        _hot_path_token = HOT_PATH.set(True)

        # PRD-FIX-084: Per-step latency telemetry. The five regressions of the
        # "step in step_sanitize_and_maintain accidentally O(corpus)" class
        # required py-spy on a live server to diagnose -- which step swallowed
        # the time was invisible from logs. step_durations_ms makes the slow
        # step name appear directly on session_start_ok event payloads.
        _step_started_at = time.monotonic()
        step_durations_ms: dict[str, float] = {}

        def _record_step(step_key: str, started_at: float) -> None:
            """Record elapsed milliseconds for a named step."""

            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            step_durations_ms[step_key] = round(elapsed_ms, 2)

        # PRD-HPO-MEAS-001 NFR-12 / FR-13: fail at boot, before any session
        # telemetry or startup artifacts are written.
        from trw_mcp.telemetry.boot_audit import run_boot_audit

        run_boot_audit()

        # Step 1: Recall learnings via SQLite adapter (compact mode)
        _recall_started = time.monotonic()
        step_recall_learnings(query, config, results, errors)
        _record_step("recall", _recall_started)

        # Step 2: resolve + pin active run (PRD-CORE-141 FR03/FR05/FR06)
        _run_resolve_started = time.monotonic()
        run_dir, call_ctx = step_run_resolve(ctx, results, errors)
        _record_step("run_resolve", _run_resolve_started)

        _surface_stamp_started = time.monotonic()
        results["surface_snapshot_id"] = step_surface_stamp(run_dir, str(call_ctx.session_id))
        _record_step("surface_stamp", _surface_stamp_started)

        # PRD-HPO-PROF-001 FR-4: resolve the hierarchical profile (defaults →
        # org → domain → task-type → session → client) and stamp the resolved
        # surface + persistent snapshot id onto the result. Fail-open inside
        # step_resolve_profile: a missing/invalid layer or disabled feature
        # flag omits the block without blocking session start. Looked up via
        # the ``_ceremony`` facade so test monkeypatches propagate.
        _profile_started = time.monotonic()
        from trw_mcp.tools import ceremony as _ceremony

        _ceremony.step_resolve_profile(config, run_dir, results)
        _record_step("profile_resolve", _profile_started)

        # Step 3: Log session_start event (FR01, PRD-CORE-031)
        _log_event_started = time.monotonic()
        try:
            step_log_session_event(run_dir, cast("dict[str, object]", results), query, is_focused)
        except Exception:  # justified: fail-open, event logging must not block session start
            logger.debug("session_event_write_failed", exc_info=True)
        _record_step("log_event", _log_event_started)

        # Step 3b: Queue SessionStartEvent for telemetry publishing
        _telemetry_started = time.monotonic()
        try:
            step_telemetry_startup(cast("dict[str, object]", results), run_dir)
        except Exception:  # justified: fail-open, telemetry publish must not block session start
            logger.debug("session_telemetry_failed", exc_info=True)
        _record_step("telemetry", _telemetry_started)

        # Step 3b': PRD-INFRA-142 FR02 — emit a one-time first_session funnel
        # event on the first session of a fresh installation. Idempotent via a
        # local flag file (no backend round-trip on subsequent calls). Fail-open
        # and looked up via the _ceremony facade so test monkeypatches propagate.
        try:
            from trw_mcp.tools import ceremony as _ceremony

            results["first_session_emitted"] = _ceremony.step_first_session_marker()
        except Exception:  # justified: fail-open, first-session marker must not block session start
            logger.debug("first_session_marker_failed", exc_info=True)

        # Step 3c: Increment sessions_tracked counter (FIX-050-FR06)
        _counter_started = time.monotonic()
        try:
            step_increment_session_counter()
        except Exception:  # justified: fail-open, counter increment must not block session start
            logger.debug("session_counter_increment_failed", exc_info=True)
        _record_step("counter", _counter_started)

        # Steps 3d, 4-5, 7: Auto-maintenance (upgrade, stale runs, embeddings, sanitization)
        _sanitize_started = time.monotonic()
        try:
            maintenance = step_sanitize_and_maintain(run_dir)
            for key in (
                "update_advisory",
                "auto_upgrade",
                "auto_upgrade_check_deferred",
                "stale_runs_closed",
                "stale_runs_deferred",
                "embeddings_advisory",
                "embeddings_backfill",
                "embeddings_backfill_deferred",
                "wal_checkpoint_deferred",
            ):
                if key in maintenance:
                    results[key] = maintenance[key]
        except Exception:  # justified: fail-open, auto-maintenance must not block session start
            logger.debug("session_maintenance_failed", exc_info=True)
        _record_step("sanitize_maintain", _sanitize_started)

        # Step 6: Phase-contextual auto-recall (PRD-CORE-049)
        _phase_recall_started = time.monotonic()
        step_auto_recall_orchestrated(query, config, run_dir, results)
        _record_step("phase_recall", _phase_recall_started)

        # PRD-FIX-084 follow-on: cover the post-phase-recall tail so total
        # never has a large unmeasured gap. assertion_health iterates every
        # learning with an assertion and can dominate the call on big corpora.
        _embed_health_started = time.monotonic()
        # FR01 (PRD-FIX-053): Embed health advisory for agents.
        results["embed_health"] = step_embed_health()
        _record_step("embed_health", _embed_health_started)

        # PRD-FIX-COMPOUNDING-1 FR02: surface backend sync-push health so a
        # silently-stalled push (config disabled / backend unreachable) is
        # visible on the first session rather than after weeks. Fail-open.
        _sync_health_started = time.monotonic()
        try:
            results["sync_health"] = step_sync_health(resolve_trw_dir(), config)
        except Exception:  # justified: fail-open, sync health must not block session start
            logger.debug("sync_health_failed", exc_info=True)
        _record_step("sync_health", _sync_health_started)

        _assertion_health_started = time.monotonic()
        try:
            ah = step_assertion_health(resolve_trw_dir())
            if ah is not None:
                results["assertion_health"] = ah
        except Exception:  # justified: fail-open, assertion health must not block session start
            logger.debug("assertion_health_failed", exc_info=True)
        _record_step("assertion_health", _assertion_health_started)

        # PRD-FIX-COMPOUNDING-2 FR04: graph-empty advisory. Surfaces the wiring
        # gap (0 edges / many memories) so operators notice before more
        # un-graphed learnings accumulate. Fail-open inside step_graph_health.
        try:
            gh = step_graph_health(resolve_trw_dir())
            if gh is not None:
                results["graph_health"] = gh
        except Exception:  # justified: fail-open, graph health must not block session start
            logger.debug("graph_health_failed", exc_info=True)

        # PRD-FIX-COMPOUNDING-6 FR03 + PRD-FIX-107 FR06: compounding-pipeline
        # health surface. Injects pipeline_health_advisory (compact single-line
        # string) when any of the five pipeline signals is degraded, and — when
        # the fail-closed FR06 gate trips (push staleness / dead graph /
        # localhost-only target) — escalates to a prominent pipeline_health_warning.
        # Absent on healthy sessions (PRD-INFRA-068 lesson). Fail-open: never
        # blocks session_start (the hard gate lives in check_pipeline_health for CI).
        _pipeline_health_started = time.monotonic()
        try:
            step_pipeline_health_advisory(resolve_trw_dir(), cast("dict[str, object]", results), config)
        except Exception:  # justified: fail-open, pipeline health must not block session start
            logger.debug("pipeline_health_advisory_failed", exc_info=True)
        _record_step("pipeline_health", _pipeline_health_started)

        _finalize_started = time.monotonic()
        # PRD-FIX-084: total elapsed time for the entire session_start call.
        # Captured BEFORE finalize so logs see the post-step total.
        _record_step("finalize", _finalize_started)
        _record_step("total", _step_started_at)
        finalize_session_start(results, config, step_durations_ms, errors)

        # PRD-IMPROVE-MCP-04 FR1: trim the payload to compact-by-default. Caps
        # the learnings list to top-K, folds the diagnostic sub-blocks into a
        # one-line health_summary, and records payload_token_estimate so the
        # token-cost reduction is measurable. verbose=True is a pass-through.
        # Fail-open inside trim_session_start_payload: never drops run/error
        # fields, so resume correctness is preserved.
        results = trim_session_start_payload(results, verbose=verbose)

        # PRD-FIX-085 FR02: reset HOT_PATH ContextVar before returning. Always
        # runs even if step bodies raised, because each step uses its own
        # try/except. If a future change adds an unhandled raise, this reset
        # is moot (the contextvar will be GC'd with the asyncio task), but
        # explicit reset is correct hygiene.
        HOT_PATH.reset(_hot_path_token)
        return results

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_deliver(
        ctx: Context | None = None,
        run_path: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
        allow_unverified: bool = False,
        unverified_reason: str = "",
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
        - allow_unverified: explicit override for delivery without a passing
          trw_build_check record. Use only for documented acceptable failures.
        - unverified_reason: required rationale when allow_unverified is true.

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
        return _run_trw_deliver(
            ctx,
            run_path,
            skip_reflect,
            skip_index_sync,
            allow_unverified,
            unverified_reason,
        )

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
        return compute_heartbeat_result(ctx, message)

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
        return _adopt_run_impl(ctx, run_path, force)
