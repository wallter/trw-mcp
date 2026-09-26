"""trw_session_start step helpers — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Step helpers covering the trw_session_start flow:

- ``_write_session_start_ids`` — populate injected_learning_ids.txt
  (PRD-CORE-095 FR16) so auto-injection doesn't re-surface learnings.
- ``step_recall_learnings`` — step 1: the one recall, presented as the learning
  block (PRD-CORE-294 FR02).
- ``step_surface_stamp`` — step 2c surface-snapshot stamp (PRD-HPO-MEAS-001
  FR-1/FR-2).
- ``step_assertion_health`` — assertion-health summary (PRD-CORE-086 FR07).
- ``step_pipeline_health_advisory`` — compact pipeline-health advisory injected
  when any compounding-pipeline signal is degraded (PRD-FIX-COMPOUNDING-6 FR03).
- ``finalize_session_start`` — errors/success/framework_reminder/
  ceremony_status/session_start_ok logging.

Extracted as DIST-243 batch 72 to keep ``_ceremony_runtime_helpers.py``
under the 350-LOC gate.
"""

from __future__ import annotations

import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import (
    RunStatusDict,
    SessionStartResultDict,
)
from trw_mcp.state._paths import TRWCallContext
from trw_mcp.tools._ceremony_degradations import (
    DegradationCollector,
    SessionStartStepError,
    record_into,
)
from trw_mcp.tools._ceremony_pipeline_advisory import (
    step_pipeline_health_advisory as step_pipeline_health_advisory,
)
from trw_mcp.tools._ceremony_runtime_helpers import _persist_surface_snapshot_pointer
from trw_mcp.tools._connection_fingerprint import build_connection_fingerprint
from trw_mcp.tools._injected_ids import (
    _MAX_INJECTED_IDS as _MAX_INJECTED_IDS,
)
from trw_mcp.tools._injected_ids import (
    _write_session_start_ids as _write_session_start_ids,
)
from trw_mcp.tools._pipeline_health import step_pipeline_health as step_pipeline_health

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


def step_run_resolve(
    ctx: object | None,
    results: SessionStartResultDict,
    errors: list[str],
) -> tuple[Path | None, TRWCallContext]:
    """Step 2 — resolve + pin the active run for this session.

    PRD-CORE-141 FR03/FR05/FR06: threads ctx through so fresh ctx-aware
    sessions do NOT hijack another session's active run via the mtime
    scan, and surfaces a structured ``hint`` field in the no-pin case.

    Returns ``(run_dir, call_ctx)``. Mutates ``results`` in-place: sets
    ``run`` (RunStatusDict), optionally ``hint`` and ``candidate_runs``
    when no pin exists. On failure appends to ``errors`` and sets
    ``run`` to error-state.
    """
    from trw_mcp.state._paths import pin_active_run, resolve_pin_key
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_runtime_helpers import (
        _candidate_run_hints,
        _get_run_status,
        _no_active_run_hint,
    )

    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    try:
        raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    except Exception as exc:  # justified: fail-open, session-id probe must not block session start
        # Previously a fully-silent fallback (no log, no payload trace). Record
        # it as an info-severity degradation so the swallow is observable
        # without changing the fallback control flow (raw_session stays None).
        raw_session = None
        record_into(cast("MutableMapping[str, object]", results), "run_resolve_session_probe", exc, severity="info")
    call_ctx = TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )

    run_dir: Path | None = None
    try:
        run_dir = _ceremony._find_active_run_compat(call_ctx)
        if run_dir is not None:
            pin_active_run(run_dir, context=call_ctx)
            results["run"] = _get_run_status(run_dir)
            # CORE269 FR04: metadata-only location hint, never recovered content.
            # Reuse the resolved run; this is not a second authority lookup.
            checkpoint = run_dir / "meta" / "checkpoints.jsonl"
            try:
                if stat.S_ISREG(checkpoint.lstat().st_mode):
                    resolved = checkpoint.resolve(strict=True)
                    if resolved.is_relative_to(run_dir.resolve(strict=True)):
                        results["run"]["checkpoint_log_path"] = str(resolved)
            except (OSError, RuntimeError):
                # Missing/inaccessible paths (including symlink loops) omit the
                # advisory pointer without changing the existing startup outcome.
                # trw-fail-silent-allow: the key is simply absent, so a caller cannot mistake it for a checked-and-valid path
                pass
        else:
            logger.info("session_start_no_active_run", pin_key=call_ctx.session_id)
            candidate_runs = _candidate_run_hints()
            results["run"] = {"active_run": None, "status": "no_active_run"}
            results["hint"] = _no_active_run_hint(candidate_runs)
            if candidate_runs:
                results["candidate_runs"] = candidate_runs
    except Exception as exc:
        # PRD-CORE-263-FR01. The error-state ``run`` block stays (NFR04: a
        # consumer reading ``results["run"]["status"]`` still finds it), but the
        # verdict is no longer this step's to set — it raises and the runner's
        # critical branch appends the typed reason. ``errors`` is left alone
        # here so one failure produces ONE entry, not two (NFR03).
        results["run"] = {"active_run": None, "status": "error"}
        raise SessionStartStepError("run_resolve", exc) from exc
    return run_dir, call_ctx


def step_recall_learnings(
    query: str,
    config: TRWConfig,
    results: SessionStartResultDict,
    errors: list[str],
    *,
    verbose: bool = False,
) -> None:
    """Step 1 — the one session_start recall, presented as the learning block (PRD-CORE-294 FR02).

    Looks up ``resolve_trw_dir`` via the parent ``ceremony`` module so test
    monkeypatches on ``trw_mcp.tools.ceremony.resolve_trw_dir`` propagate
    correctly (per the test-monkeypatch indirection pattern).
    """
    from trw_mcp.state._memory_recall import pop_store_error
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_helpers import perform_session_recalls

    reader = FileStateReader()
    try:
        trw_dir = _ceremony.resolve_trw_dir()
        # Clear first: the slot is context-scoped, and a failure left by an earlier recall in the
        # same context (another tool, a test in the same worker) is not THIS session's recall.
        pop_store_error()
        learnings, extra = perform_session_recalls(trw_dir, query, config, reader, verbose=verbose)
        store_error = pop_store_error()
        if store_error:
            # Recall returned nothing because the store could not be opened; route it
            # through this step's critical-failure handling instead of a silent empty list.
            from trw_memory.exceptions import StorageError

            raise StorageError(store_error)
        results["learnings"] = learnings
        results["learnings_count"] = len(learnings)
        if "query" in extra:
            results["query"] = str(extra["query"])
        if "query_advisory" in extra:
            results["query_advisory"] = str(extra["query_advisory"])
        if "learnings_omitted" in extra:
            results["learnings_omitted"] = int(str(extra["learnings_omitted"]))
        if "store_count" in extra:
            results["store_count"] = int(str(extra["store_count"]))
        # This writes a bounded, de-duplicated text file under the context
        # directory and opens no SQLite connection.
        _write_session_start_ids(trw_dir, learnings, cast("MutableMapping[str, object]", results))
    except Exception as exc:
        # PRD-CORE-263-FR01. This handler used to swallow into a warning and a
        # degradation, which made the ``recall`` step's ``critical=True`` flag
        # unreachable: an agent read ``success: true`` on a session whose recall
        # returned nothing. The step no longer decides. It leaves the payload
        # keys an existing consumer reads (NFR04) and hands the failure to the
        # runner, which is the only place that holds the ``critical`` flag.
        results["learnings"] = []
        results["learnings_count"] = 0
        raise SessionStartStepError("recall", exc) from exc


def _record_or_debug(
    degradations: DegradationCollector | None, step: str, exc: BaseException, fallback_event: str
) -> None:
    """Record a swallow on the threaded collector, else fall back to the debug log.

    DRY helper for the fail-open steps that take an OPTIONAL per-call collector:
    when threaded, the swallow becomes an observable typed degradation; when a
    legacy caller passes ``None`` it keeps the old invisible debug log. Never
    changes control flow — the caller still fails open either way.
    """
    if degradations is not None:
        degradations.record(step, exc)
    else:
        logger.debug(fallback_event, exc_info=True)


def step_surface_stamp(run_dir: Path | None, session_id: str, degradations: DegradationCollector | None = None) -> str:
    """PRD-HPO-MEAS-001 FR-1/FR-2 — resolve SurfaceRegistry + stamp run snapshot.

    PRD-CORE-263-FR01: ``surface_stamp`` is declared critical in the
    session-start table, so a stamping failure raises a typed
    :class:`SessionStartStepError` rather than returning ``""`` — an empty
    snapshot id was indistinguishable from a stamp that never happened, which is
    exactly the ambiguity the critical flag exists to remove. ``degradations``
    is retained for the legacy non-table callers that pass one.
    """
    try:
        from trw_mcp.telemetry.artifact_registry import SurfaceRegistry, resolve_surface_registry
        from trw_mcp.telemetry.surface_manifest import stamp_session

        if run_dir is not None:
            registry = SurfaceRegistry.build_and_emit(
                session_id=session_id,
                run_id=run_dir.name,
                run_dir=run_dir,
            )
            snapshot_id = registry.snapshot_id
            stamp_session(run_dir / "meta")
            _persist_surface_snapshot_pointer(run_dir, snapshot_id)
        else:
            registry = resolve_surface_registry()
            snapshot_id = registry.snapshot_id
        logger.debug(
            "surface_snapshot_stamped",
            snapshot_id=snapshot_id,
            run_dir=str(run_dir) if run_dir else "",
            artifact_count=len(registry.artifacts),
        )
        return snapshot_id
    except Exception as exc:
        raise SessionStartStepError("surface_stamp", exc) from exc


def _resolve_assertion_stale_days(config: TRWConfig | None) -> int:
    """The configured assertion staleness window, or a raise (PRD-CORE-263-FR08).

    Refuse-on-exception: there is deliberately no numeric fallback here. A
    hardcoded default would put this surface back out of step with
    ``lifecycle.verification_pass``, which is the whole defect.
    """
    if config is None:
        from trw_mcp.models.config import get_config

        config = get_config()
    return int(config.assertion_stale_threshold_days)


def step_assertion_health(
    trw_dir: Path,
    degradations: DegradationCollector | None = None,
    config: TRWConfig | None = None,
) -> dict[str, int] | None:
    """PRD-CORE-086 FR07: assertion health summary from cached last_result fields.

    ``degradations`` (optional): threads the per-call collector so a probe
    failure is recorded as a typed degradation.

    ``config`` (PRD-CORE-263-FR08): supplies ``assertion_stale_threshold_days``.
    This step used to hardcode a 7-day window against a configured default of 30,
    so session start and the maintenance verification pass reported DIFFERENT
    stale counts from the same store and an operator who moved the knob saw one
    of them move. Passing ``None`` resolves the live config; a config that cannot
    be resolved records a degradation and returns no summary rather than falling
    back to a hardcoded window, which would recreate the defect one layer down.
    """
    from trw_mcp.state._store_selection import selected_store

    started = time.monotonic()
    try:
        stale_days = _resolve_assertion_stale_days(config)
    except Exception as exc:
        _record_or_debug(degradations, "assertion_health", exc, "assertion_health_config_unresolved")
        return None
    try:
        # The store counts its own project namespace, so a shared store never
        # leaks another namespace's assertions into this summary (memory-storage-1).
        store, namespace = selected_store(trw_dir)
        return store.assertion_health(namespace, stale_days)
    except Exception as exc:  # justified: fail-open per PRD-CORE-086 NFR
        # trw-fail-silent-allow: the failure is recorded as a typed assertion_health degradation, and the summary is omitted rather than zeroed
        _record_or_debug(degradations, "assertion_health", exc, "assertion_health_failed")
        return None
    finally:
        logger.debug("assertion_health_computed", duration_ms=round((time.monotonic() - started) * 1000, 1))


def step_graph_health(trw_dir: Path, degradations: DegradationCollector | None = None) -> dict[str, object] | None:
    """PRD-FIX-COMPOUNDING-2 FR04 — graph-empty advisory for session_start.

    PRD-FIX-141-FR02: this used to hold its OWN copy of the predicate — its own
    ``graph_has_relations`` call over the live backend connection, its own
    ``count_entries`` population (canary-excluded, capped at 100,000) and its own
    literal ``memories > 10`` threshold. That made three predicates for one
    question, and on 2026-09-16 they disagreed on the same store in the same
    second (learning L-Rikf). It asks
    :func:`trw_mcp.tools._pipeline_health.probe_graph_edges` now — the one place
    that decides — so this advisory, the pipeline-health CLI verb and the
    fail-closed gate cannot drift apart again.

    Returns ``None`` (advisory omitted) when the graph holds a relation, when the
    corpus is below the configured minimum, when the probe could not read the
    store, or on any error (fail-open). "Could not read" is deliberately NOT an
    advisory: an unmeasured probe is not evidence of an empty graph, and those
    are different facts that used to share one answer.

    The remedy is config-derived, never asserted: ``trw_deliver`` backfills the
    graph inside ``step_knowledge_sync`` only while
    ``deliver_graph_backfill_enabled`` is true (the shipped default). With it
    off, "re-deliver to trigger graph backfill" would send an agent to a step
    that no longer runs, so the advisory names the config field instead.
    """
    from trw_mcp.tools._pipeline_health import probe_graph_edges

    try:
        graph = probe_graph_edges(trw_dir)
        if not graph.get("measured", True) or not graph.get("degraded"):
            return None

        from trw_mcp.models.config import get_config

        backfill_on = bool(getattr(get_config(), "deliver_graph_backfill_enabled", True))
        remedy = (
            "re-deliver (trw_deliver) to trigger graph backfill"
            if backfill_on
            else "deliver-time backfill is off (deliver_graph_backfill_enabled=false)"
        )
        return {
            "status": "empty",
            "memories": int(str(graph.get("corpus_count", 0))),
            "advisory": f"{graph.get('advisory', 'knowledge graph empty')} — {remedy}",
        }
    except Exception as exc:  # justified: fail-open — graph-health probe must not block session start
        _record_or_debug(degradations, "graph_health", exc, "graph_health_probe_failed")
        return None


def finalize_session_start(
    results: SessionStartResultDict,
    config: TRWConfig,
    step_durations_ms: dict[str, float],
    errors: list[str],
    session_id: str | None = None,
) -> None:
    """Finalize trw_session_start fields and ceremony state."""
    from trw_mcp.tools._ceremony_helpers import step_ceremony_status, step_mark_session_started

    results["errors"] = errors
    results["success"] = len(errors) == 0

    # PRD-CORE-215 FR01: the session-start finalizer OWNS the public connection
    # fingerprint. It describes exactly one stdio process (never a proxy) with a
    # process-stable nonce so callers can distinguish distinct stdio processes.
    # SessionStartResultDict is owned by another module, so the extra key is
    # written through a MutableMapping cast (same pattern as record_into).
    cast("MutableMapping[str, object]", results)["connection_fingerprint"] = build_connection_fingerprint()

    if config.effective_ceremony_mode == "light":
        results["framework_reminder"] = (
            "Preserve unfinished work with trw_checkpoint() or a durable handoff. "
            "Use trw_deliver() only to accept completed work under delivery gates."
        )
    else:
        results["framework_reminder"] = (
            "Read your phase's sections of .trw/frameworks/FRAMEWORK.md (start "
            "with EXECUTION MODEL SUMMARY) — it defines the methodology your tools "
            "implement (6-phase execution model, exit criteria, formations, quality "
            "gates, phase reversion). Re-read them after context compaction."
        )

    try:
        step_mark_session_started(session_id=session_id)
    except Exception as exc:  # justified: fail-open, state mutation must not block session start
        record_into(cast("MutableMapping[str, object]", results), "mark_session_started", exc)

    try:
        step_ceremony_status(cast("dict[str, object]", results))
    except Exception as exc:  # justified: fail-open, status decoration must not block session start
        record_into(cast("MutableMapping[str, object]", results), "ceremony_status", exc)

    results["step_durations_ms"] = step_durations_ms


def log_session_start_complete(
    results: SessionStartResultDict,
    step_durations_ms: dict[str, float],
    *,
    learnings_count: int,
) -> None:
    """Log completion after finalization and payload shaping are measured."""
    run_info: RunStatusDict | None = results.get("run")
    active_run_id = str(run_info.get("active_run", "")) if run_info else ""
    phase = str(run_info.get("phase", "")) if run_info else ""
    task = str(run_info.get("task_name", "")) if run_info else ""
    logger.info(
        "session_start_ok",
        run_id=active_run_id,
        phase=phase,
        task=task,
        learnings_count=learnings_count,
        step_durations_ms=step_durations_ms,
    )
    logger.debug("session_start_learnings_loaded", count=learnings_count)
