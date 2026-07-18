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

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    DeliverResultDict,
    SessionStartResultDict,
    TrwAdoptRunResultDict,
    TrwHeartbeatResultDict,
)
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths import (
    TRWCallContext,
    find_active_run,
)

# Re-exported as the canonical monkeypatch seam: the session-start ``_ss_*``
# adapters call ``ceremony.resolve_trw_dir()`` and the deliver tool reads
# ``vars(ceremony)["resolve_trw_dir"]``, and ~79 tests patch it here. Kept as an
# explicit re-export so it survives even though this module's own body no longer
# calls it directly after the step-table refactor.
from trw_mcp.state._paths import resolve_trw_dir as resolve_trw_dir

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

# mcp-x-failopen: the typed fail-open degradation collector. Re-exported at this
# facade so tests/operators can construct or patch it via ``ceremony.<name>``.
from trw_mcp.tools._ceremony_degradations import (
    DegradationCollector as DegradationCollector,
)
from trw_mcp.tools._ceremony_degradations import (
    record_into as record_into,
)
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


def _find_active_run_compat(call_ctx: TRWCallContext) -> Path | None:
    """Resolve the active run while retaining the established patch seam."""
    return find_active_run(context=call_ctx)


def _build_call_context(ctx: Context | None) -> TRWCallContext:
    """Preserve the ceremony patch seam while using the shared builder."""
    return build_call_context(ctx)


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
    log_session_start_complete as log_session_start_complete,
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

# Declarative session-start step table (driver + per-step adapters). The
# ``_ss_*`` adapters are re-exported here so ``run_steps`` can resolve each step
# via ``getattr(ceremony, attr)`` at CALL TIME — preserving every monkeypatch
# seam. See _ceremony_step_table for the folded 16 timing blocks + 9 fail-open
# swallows that used to live inline in trw_session_start.
from trw_mcp.tools._ceremony_step_table import (
    SESSION_START_STEPS as SESSION_START_STEPS,
)
from trw_mcp.tools._ceremony_step_table import (
    SessionStartContext as SessionStartContext,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_assertion_health as _ss_assertion_health,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_counter as _ss_counter,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_embed_health as _ss_embed_health,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_first_session_marker as _ss_first_session_marker,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_graph_health as _ss_graph_health,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_log_event as _ss_log_event,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_phase_recall as _ss_phase_recall,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_pipeline_health as _ss_pipeline_health,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_profile_resolve as _ss_profile_resolve,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_recall as _ss_recall,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_run_resolve as _ss_run_resolve,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_sanitize_maintain as _ss_sanitize_maintain,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_surface_stamp as _ss_surface_stamp,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_sync_health as _ss_sync_health,
)
from trw_mcp.tools._ceremony_step_table import (
    _ss_telemetry as _ss_telemetry,
)
from trw_mcp.tools._ceremony_step_table import (
    run_steps as run_steps,
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

        try:
            # PRD-FIX-084: Per-step latency telemetry. step_durations_ms makes
            # the slow step name appear directly on session_start_ok payloads;
            # run_steps records each timed step. Total/finalize are recorded
            # below (bookkeeping outside the table).
            _step_started_at = time.monotonic()
            step_durations_ms: dict[str, float] = {}

            # PRD-HPO-MEAS-001 NFR-12 / FR-13: fail at boot, before any session
            # telemetry or startup artifacts are written.
            from trw_mcp.telemetry.boot_audit import run_boot_audit

            run_boot_audit()

            # Steps 1-7: recall, run-resolve, surface-stamp, profile-resolve,
            # log-event, telemetry, first-session-marker, counter, maintenance,
            # phase-recall, embed/sync/assertion/graph/pipeline health. Each is a
            # ``_ss_*`` adapter resolved through this facade at call time (so all
            # ``ceremony.<name>`` monkeypatches propagate); critical steps
            # re-raise, the rest are fail-open. See _ceremony_step_table.
            from trw_mcp.tools import ceremony as _ceremony

            sctx = SessionStartContext(
                query=query,
                config=config,
                ctx=ctx,
                is_focused=is_focused,
                results=results,
                errors=errors,
                step_durations_ms=step_durations_ms,
            )
            run_steps(SESSION_START_STEPS, sctx, _ceremony)

            # PRD-FIX-084: measure finalization rather than recording a
            # near-zero placeholder before it starts.
            _finalize_started = time.monotonic()
            session_id = sctx.call_ctx.session_id if sctx.call_ctx is not None else None
            finalize_session_start(results, config, step_durations_ms, errors, session_id=session_id)
            step_durations_ms["finalize"] = round((time.monotonic() - _finalize_started) * 1000.0, 2)
            step_durations_ms["total"] = round((time.monotonic() - _step_started_at) * 1000.0, 2)

            # PRD-IMPROVE-MCP-04 FR1: trim the payload to compact-by-default. Caps
            # the learnings list to top-K, folds the diagnostic sub-blocks into a
            # one-line health_summary, and records payload_token_estimate so the
            # token-cost reduction is measurable. verbose=True is a pass-through.
            # Fail-open inside trim_session_start_payload: never drops run/error
            # fields, so resume correctness is preserved.
            logged_learnings_count = int(str(results.get("learnings_count", 0)))
            results = trim_session_start_payload(results, verbose=verbose)
            step_durations_ms["total"] = round((time.monotonic() - _step_started_at) * 1000.0, 2)
            log_session_start_complete(
                results,
                step_durations_ms,
                learnings_count=logged_learnings_count,
            )
            return results
        finally:
            # PRD-FIX-085 FR02 / trw-mcp-2: reset HOT_PATH ContextVar in a
            # finally so it always unwinds even if a future change adds an
            # unhandled raise between set and reset (the previous inline reset
            # was unguarded despite the docstring claiming try/finally).
            HOT_PATH.reset(_hot_path_token)

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_deliver(
        ctx: Context | None = None,
        run_path: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
        allow_unverified: bool = False,
        unverified_reason: str = "",
        delivery_id: str = "",
        capability_token: str = "",
    ) -> DeliverResultDict:
        """Persist learnings and progress so future sessions inherit this session's work.

        Use when ending a session or closing a validated milestone.

        Before calling, check whether this session produced a non-obvious,
        reusable discovery. Record it with trw_learn when one exists; do not
        manufacture a learning for trivial or already-known work.

        Runs reflection and checkpoint synchronously, then launches
        concurrency-safe housekeeping in the background.

        Input:
        - run_path: run directory; auto-detected when omitted.
        - skip_reflect: skip an already-completed reflection.
        - skip_index_sync: skip INDEX/ROADMAP synchronization.
        - allow_unverified: request a structured acceptable-failure override of
          a hard delivery gate. Advisory task classes do not need an override.
        - unverified_reason: when allow_unverified is true, a JSON or YAML record
          with all four required fields: failed_command, residual_risk, owner,
          and expiry_iso (YYYY-MM-DD). Free text and review-verdict labels are
          rejected; accepted records are written to the override ledger.

        - delivery_id / capability_token: optional caller UUIDv7 plus a
          >=128-bit recovery secret (PRD-CORE-208) that makes a timed-out response
          recoverable via ``trw_delivery_status`` / ``trw_delivery_recover``. Omit
          both for the legacy, non-recoverable path.

        Output: DeliverResultDict with fields
        {run_path: str, reflect: dict, checkpoint: dict, deferred: str,
         critical_steps_completed: int, deferred_steps: int, errors: list,
         success: bool, learning_reflection?: str, delivery_operation?: dict}.

        See Also: trw_checkpoint, trw_instructions_sync, trw_delivery_status
        """
        return _run_trw_deliver(
            ctx,
            run_path,
            skip_reflect,
            skip_index_sync,
            allow_unverified,
            unverified_reason,
            delivery_id,
            capability_token,
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
