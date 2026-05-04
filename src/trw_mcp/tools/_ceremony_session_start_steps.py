"""trw_session_start step helpers — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Six step helpers covering the trw_session_start flow:

- ``_write_session_start_ids`` — populate injected_learning_ids.txt
  (PRD-CORE-095 FR16) so auto-injection doesn't re-surface learnings.
- ``step_recall_learnings`` — step 1 recall via SQLite + extras-promotion.
- ``step_surface_stamp`` — step 2c surface-snapshot stamp (PRD-HPO-MEAS-001
  FR-1/FR-2).
- ``step_phase_auto_recall`` — step 6 phase-contextual auto-recall
  (PRD-CORE-049).
- ``step_assertion_health`` — assertion-health summary (PRD-CORE-086 FR07).
- ``finalize_session_start`` — errors/success/framework_reminder/
  ceremony_status/session_start_ok logging.

Extracted as DIST-243 batch 72 to keep ``_ceremony_runtime_helpers.py``
under the 350-LOC gate.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import (
    AutoRecalledItemDict,
    RunStatusDict,
    SessionStartResultDict,
)
from trw_mcp.state._paths import TRWCallContext
from trw_mcp.tools._ceremony_runtime_helpers import _persist_surface_snapshot_pointer

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


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
    except Exception:
        raw_session = None
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
        else:
            logger.info("session_start_no_active_run", pin_key=call_ctx.session_id)
            candidate_runs = _candidate_run_hints()
            results["run"] = {"active_run": None, "status": "no_active_run"}
            results["hint"] = _no_active_run_hint(candidate_runs)
            if candidate_runs:
                results["candidate_runs"] = candidate_runs
    except Exception as exc:  # justified: fail-open, run status check must not block session start
        errors.append(f"status: {exc}")
        results["run"] = {"active_run": None, "status": "error"}
    return run_dir, call_ctx


def step_recall_learnings(
    query: str,
    config: TRWConfig,
    results: SessionStartResultDict,
    errors: list[str],
) -> None:
    """Step 1 — recall learnings via SQLite adapter and update results in-place.

    Looks up ``resolve_trw_dir`` via the parent ``ceremony`` module so test
    monkeypatches on ``trw_mcp.tools.ceremony.resolve_trw_dir`` propagate
    correctly (per the test-monkeypatch indirection pattern).
    """
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_helpers import perform_session_recalls

    reader = FileStateReader()
    try:
        trw_dir = _ceremony.resolve_trw_dir()  # type: ignore[attr-defined]
        learnings, _auto_recalled, extra = perform_session_recalls(trw_dir, query, config, reader)
        results["learnings"] = learnings
        results["learnings_count"] = len(learnings)
        if "query" in extra:
            results["query"] = str(extra["query"])
        if "query_matched" in extra:
            results["query_matched"] = int(str(extra["query_matched"]))
        if "total_available" in extra:
            results["total_available"] = int(str(extra["total_available"]))
        if "response_compacted" in extra:
            results["response_compacted"] = bool(extra["response_compacted"])
        if "side_effects_deferred" in extra:
            results["side_effects_deferred"] = extra["side_effects_deferred"]
        if "side_effects_deferred" not in extra:
            _write_session_start_ids(trw_dir, learnings)
    except Exception as exc:  # justified: fail-open, recall failure must not block session start
        errors.append(f"recall: {exc}")
        results["learnings"] = []
        results["learnings_count"] = 0


def step_surface_stamp(run_dir: Path | None, session_id: str) -> str:
    """PRD-HPO-MEAS-001 FR-1/FR-2 — resolve SurfaceRegistry + stamp run snapshot."""
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
    except Exception:  # justified: fail-open, surface stamping must not block session start
        logger.debug("surface_snapshot_stamp_failed", exc_info=True)
        return ""


def step_auto_recall_orchestrated(
    query: str,
    config: TRWConfig,
    run_dir: Path | None,
    results: SessionStartResultDict,
) -> None:
    """Orchestrate step 6: response-compacted check + primary_ids + auto_recall + surfaces.

    Looks up ``resolve_trw_dir`` and ``record_session_start_surfaces``
    via the parent ``ceremony`` module so test monkeypatches propagate.
    Fail-open on every branch.
    """
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_helpers import record_session_start_surfaces

    try:
        if bool(results.get("response_compacted")):
            results["auto_recall_deferred"] = {
                "reason": "session_start_compacted",
                "detail": "Phase auto-recall is optional and was left off the hot response path.",
            }
            return
        trw_dir_ar = _ceremony.resolve_trw_dir()  # type: ignore[attr-defined]
        primary_ids = {
            str(entry.get("id", "")) for entry in results.get("learnings", []) if entry.get("id")
        }
        outcome = step_phase_auto_recall(trw_dir_ar, query, config, run_dir, results.get("run"), primary_ids)
        if outcome is None:
            return
        phase_recalled, auto_ids = outcome
        record_session_start_surfaces(trw_dir_ar, auto_ids)
        results["auto_recalled"] = phase_recalled
        results["auto_recall_count"] = len(phase_recalled)
    except Exception:  # justified: fail-open, auto-recall must not block session start
        logger.debug("session_auto_recall_failed", exc_info=True)


def step_phase_auto_recall(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    run_dir: Path | None,
    run_status: RunStatusDict | None,
    primary_ids: set[str],
) -> tuple[list[AutoRecalledItemDict], list[str]] | None:
    """PRD-CORE-049 — phase-contextual auto-recall on session_start."""
    from trw_mcp.tools._ceremony_helpers import _phase_contextual_recall

    if not config.auto_recall_enabled:
        return None
    try:
        phase_recalled = _phase_contextual_recall(trw_dir, query, config, run_dir, run_status)
        if not phase_recalled:
            return None
        auto_ids = [
            str(entry.get("id", ""))
            for entry in phase_recalled
            if entry.get("id") and str(entry.get("id", "")) not in primary_ids
        ]
        return phase_recalled, auto_ids
    except Exception:  # justified: fail-open, auto-recall must not block session start
        logger.debug("session_auto_recall_failed", exc_info=True)
        return None


def step_assertion_health(trw_dir: Path) -> dict[str, int] | None:
    """PRD-CORE-086 FR07: assertion health summary from cached last_result fields."""
    from trw_mcp.state.memory_adapter import get_backend

    started = time.monotonic()
    try:
        backend = get_backend(trw_dir)
        if not hasattr(backend, "entries_with_assertions"):
            return None
        entries = backend.entries_with_assertions()
        if not entries:
            return None
        stale_threshold = datetime.now(timezone.utc) - timedelta(days=7)
        passing = failing = stale = unverifiable = 0
        for entry in entries:
            for a in entry.assertions:
                if a.last_verified_at is None or a.last_verified_at < stale_threshold:
                    stale += 1
                elif a.last_result is True:
                    passing += 1
                elif a.last_result is False:
                    failing += 1
                else:
                    unverifiable += 1
        return {
            "passing": passing,
            "failing": failing,
            "stale": stale,
            "unverifiable": unverifiable,
            "total": len(entries),
        }
    except Exception:  # justified: fail-open per PRD-CORE-086 NFR
        logger.debug("assertion_health_failed", exc_info=True)
        return None
    finally:
        logger.debug("assertion_health_computed", duration_ms=round((time.monotonic() - started) * 1000, 1))


def finalize_session_start(
    results: SessionStartResultDict,
    config: TRWConfig,
    step_durations_ms: dict[str, float],
    errors: list[str],
) -> None:
    """Finalize trw_session_start: errors, success, framework_reminder, ceremony_status, logs."""
    from trw_mcp.tools._ceremony_helpers import step_ceremony_status, step_mark_session_started

    results["errors"] = errors
    results["success"] = len(errors) == 0

    if bool(results.get("response_compacted")) or config.effective_ceremony_mode == "light":
        results["framework_reminder"] = "Call trw_deliver() when done to persist your work."
    else:
        results["framework_reminder"] = (
            "Read .trw/frameworks/FRAMEWORK.md — it defines the methodology "
            "your tools implement (6-phase execution model, exit criteria, "
            "formations, quality gates, phase reversion). Re-read after "
            "context compaction."
        )

    try:
        step_mark_session_started()
    except Exception:  # justified: fail-open, state mutation must not block session start
        logger.debug("session_mark_started_failed", exc_info=True)

    try:
        if bool(results.get("response_compacted")):
            results["ceremony_status_deferred"] = {
                "reason": "session_start_compacted",
                "detail": "Nudge decoration is optional and was left off the hot response path.",
            }
        else:
            step_ceremony_status(cast("dict[str, object]", results))
    except Exception:  # justified: fail-open, status decoration must not block session start
        logger.debug("session_ceremony_status_failed", exc_info=True)

    results["step_durations_ms"] = step_durations_ms

    run_info: RunStatusDict | None = results.get("run")
    active_run_id = str(run_info.get("active_run", "")) if run_info else ""
    phase = str(run_info.get("phase", "")) if run_info else ""
    task = str(run_info.get("task_name", "")) if run_info else ""
    learnings_count = int(str(results.get("learnings_count", 0)))
    logger.info(
        "session_start_ok",
        run_id=active_run_id,
        phase=phase,
        task=task,
        learnings_count=learnings_count,
        step_durations_ms=step_durations_ms,
    )
    logger.debug("session_start_learnings_loaded", count=learnings_count)
