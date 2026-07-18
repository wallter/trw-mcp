"""Implementation body for the ``trw_deliver`` ceremony tool.

The public FastMCP registration stays in :mod:`trw_mcp.tools.ceremony`; this
module owns the deliver workflow so the facade remains a small registration and
patch-seam module.  Runtime lookups intentionally go through the facade where
legacy tests/operators monkeypatch helpers such as ``resolve_trw_dir``,
``find_active_run``, ``_do_reflect``, ``_step_checkpoint``, and ``_events``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import Context

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import DeliverResultDict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._ceremony_degradations import record_into
from trw_mcp.tools._ceremony_deliver_steps import (
    log_deliver_complete,
    step_clear_score,
    step_knowledge_sync,
    step_session_changelog,
    unpack_gate_result,
)
from trw_mcp.tools._deferred_delivery import DEFERRED_STEPS, _launch_deferred
from trw_mcp.tools._delivery_journal_wiring import (
    DeliverJournal,
    compute_deferred_digest,
    open_delivery_journal,
)
from trw_mcp.tools._delivery_models import OperationState
from trw_mcp.tools._helpers import _run_step

logger = structlog.get_logger(__name__)

# The synchronous critical steps trw_deliver runs before launching the deferred
# batch. ``critical_steps_completed`` is DERIVED from this roster (minus the
# steps that errored) instead of a magic literal, so it can never drift from the
# real number of critical steps.
DELIVER_CRITICAL_STEPS: tuple[str, ...] = ("reflect", "checkpoint")


def run_trw_deliver(
    ctx: Context | None = None,
    run_path: str | None = None,
    skip_reflect: bool = False,
    skip_index_sync: bool = False,
    allow_unverified: bool = False,
    unverified_reason: str = "",
    delivery_id: str = "",
    capability_token: str = "",
) -> DeliverResultDict:
    """Persist learnings/progress and launch deferred delivery housekeeping.

    PRD-CORE-208: when ``delivery_operations_mode`` is not ``off`` the call is
    bound to a durable, crash-safe delivery operation. ``delivery_id`` +
    ``capability_token`` (optional, NFR01-additive) let a caller supply a
    UUIDv7 + >=128-bit recovery capability so a timed-out response is
    recoverable via ``trw_delivery_status`` / ``trw_delivery_recover``; a
    conflicting explicit ID returns ``delivery_request_conflict`` with zero
    effects. Omitting them keeps the legacy path (server-generated ID,
    ``caller_recoverable=false``).
    """
    from trw_mcp.tools import ceremony as _ceremony

    get_config_fn = cast("Callable[[], TRWConfig]", vars(_ceremony)["get_config"])
    config = get_config_fn()
    reader = FileStateReader()
    writer = FileStateWriter()
    t0 = time.monotonic()
    results: DeliverResultDict = {"timestamp": datetime.now(timezone.utc).isoformat()}
    errors: list[str] = []
    resolve_trw_dir_fn = cast("Callable[[], Path]", vars(_ceremony)["resolve_trw_dir"])
    trw_dir = resolve_trw_dir_fn()

    call_ctx = _ceremony._build_call_context(ctx)
    resolved_run = Path(run_path).resolve() if run_path else _ceremony._find_active_run_compat(call_ctx)

    # PRD-QUAL-042-FR02 (path-traversal): a caller-supplied ``run_path`` must
    # resolve INSIDE the project root. Without this an explicit run_path like
    # ``../../etc`` would make deliver checkpoint/copy artifacts into arbitrary
    # directories outside the project. Mirrors the containment check in
    # ``_paths.resolve_run_path``. Reject (do not silently fall back) so the
    # traversal attempt is visible.
    if run_path and resolved_run is not None:
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root().resolve()
        if not resolved_run.is_relative_to(project_root):
            logger.warning(
                "deliver_run_path_escapes_project",
                run_path=str(resolved_run),
                project_root=str(project_root),
            )
            results["run_path"] = None
            block_msg = f"run_path escapes project root: {resolved_run}"
            errors.append(block_msg)
            results["errors"] = errors
            results["delivery_blocked"] = block_msg
            results["success"] = False
            return results

        configured_runs_root = Path(config.runs_root)
        runs_root = (
            configured_runs_root.resolve()
            if configured_runs_root.is_absolute()
            else (project_root / configured_runs_root).resolve()
        )
        run_yaml = resolved_run / "meta" / "run.yaml"
        try:
            run_identity = reader.read_yaml(run_yaml) if run_yaml.is_file() else None
        except Exception:
            run_identity = None
            logger.warning("deliver_run_identity_unreadable", run_path=str(resolved_run), exc_info=True)
        run_id = run_identity.get("run_id") if isinstance(run_identity, dict) else None
        valid_run_identity = (
            runs_root.is_relative_to(project_root)
            and resolved_run.is_relative_to(runs_root)
            and isinstance(run_id, str)
            and run_id == resolved_run.name
        )
        if not valid_run_identity:
            logger.warning(
                "deliver_run_path_invalid",
                run_path=str(resolved_run),
                runs_root=str(runs_root),
            )
            results["run_path"] = None
            block_msg = f"run_path is not a valid TRW run directory: {resolved_run}"
            errors.append(block_msg)
            results["errors"] = errors
            results["delivery_blocked"] = block_msg
            results["success"] = False
            return results

    results["run_path"] = str(resolved_run) if resolved_run else None
    candidate_runs = _ceremony._candidate_run_hints() if resolved_run is None else []
    if candidate_runs:
        results["candidate_runs"] = candidate_runs

    logger.info("deliver_started", run_id=str(resolved_run.name) if resolved_run else "", phase="DELIVER")

    # PRD-CORE-208 FR01: claim a caller-stable delivery operation BEFORE the first
    # delivery mutation. An explicit-ID conflict/rejection returns zero effects
    # here; a legacy no-ID call is journaled but not caller-recoverable (NFR01).
    journal, block_result = _open_journal(
        trw_dir,
        config,
        resolved_run,
        skip_reflect=skip_reflect,
        skip_index_sync=skip_index_sync,
        allow_unverified=allow_unverified,
        delivery_id=delivery_id,
        capability_token=capability_token,
    )
    if block_result is not None:
        return block_result

    from trw_mcp.models.run import Phase
    from trw_mcp.state.phase import try_update_phase
    from trw_mcp.tools._ceremony_helpers import check_delivery_gates, copy_compliance_artifacts

    with journal.step("S01"):  # run phase write
        try_update_phase(resolved_run, Phase.DELIVER)
    gate_result = check_delivery_gates(resolved_run, reader, trw_dir, session_id=call_ctx.session_id)
    unpack_gate_result(gate_result, results)

    with journal.step("S05"):  # review/integration compliance copies
        compliance_result = copy_compliance_artifacts(resolved_run, trw_dir, config, reader, writer)
    if "compliance_artifacts_copied" in compliance_result:
        results["compliance_artifacts_copied"] = compliance_result["compliance_artifacts_copied"]
    if "compliance_dir" in compliance_result:
        results["compliance_dir"] = compliance_result["compliance_dir"]

    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    if evaluate_delivery_gates(
        gate_result, results, errors, resolved_run, trw_dir, allow_unverified, unverified_reason
    ):
        # A blocked gate is a durable operation terminal/provisional state, not an
        # absent operation (FR02 acceptance).
        journal.mark_state(OperationState.BLOCKED)
        if journal.enabled:
            results["delivery_operation"] = journal.summary()
        return results

    results_view: dict[str, object] = cast("dict[str, object]", results)
    if not skip_reflect:
        with journal.step("S08"):  # mechanically extracted learning writes
            _run_step("reflect", lambda: _ceremony._do_reflect(trw_dir, resolved_run), results_view, errors)
    else:
        results["reflect"] = {"status": "skipped"}

    if resolved_run is not None:
        with journal.step("S11"):  # checkpoint record append
            _run_step("checkpoint", lambda: _ceremony._step_checkpoint(resolved_run), results_view, errors)
    else:
        checkpoint_skip: dict[str, object] = {
            "status": "skipped",
            "reason": "no_active_run",
            "detail": (
                "Learning persistence can still succeed, but run checkpointing was skipped because "
                "this MCP session has no pinned run."
            ),
            "hint": _ceremony._no_active_run_hint(candidate_runs),
        }
        # candidate_runs already sits at the top level of this response —
        # do not re-embed the same list inside the checkpoint block.
        results["checkpoint"] = checkpoint_skip

    critical_elapsed = round(time.monotonic() - t0, 2)
    results["critical_elapsed_seconds"] = critical_elapsed

    _probe_integrity(trw_dir, resolved_run, results)
    if resolved_run is not None:
        with journal.step("S14"):  # CLEAR score JSON replace
            step_clear_score(resolved_run, results)
    with journal.step("S15"):  # knowledge topic synchronization
        step_knowledge_sync(trw_dir, results)
    # PRD-LOCAL-049: durable session changelog artifact. Only runs with an
    # active run dir; fail-open inside the step so it never blocks deliver.
    if resolved_run is not None:
        with journal.step("S17"):  # session changelog write
            step_session_changelog(resolved_run, results)

    # PRD-CORE-208: critical synchronous effects are journaled; record the
    # milestone and the deferred-batch digest (FR06) before launching the batch.
    journal.mark_state(OperationState.CRITICAL_COMPLETE)
    _journal_enqueue_deferred(journal, resolved_run, skip_index_sync=skip_index_sync)

    deferred_status = _launch_deferred(
        trw_dir, resolved_run, results_view, skip_index_sync=skip_index_sync, operation_id=journal.operation_id
    )
    results["deferred"] = deferred_status
    results["errors"] = errors
    results["success"] = len(errors) == 0
    results["critical_steps_completed"] = len(DELIVER_CRITICAL_STEPS) - len(errors)
    # ``deferred`` (the launch-status string above) is the actionable signal; the
    # deferred-step *count* is a compile-time constant (len(DEFERRED_STEPS)) that
    # never varies at runtime, so it is not re-emitted per deliver response. The
    # roster size is guarded directly by a unit test on DEFERRED_STEP_COUNT.
    _ceremony._aggregate_advisory_warnings(results)
    with journal.step("S18"):  # ceremony deliver-called flag
        _mark_deliver_and_reflect_learning(trw_dir, results)
    _write_nudge_analysis_artifact(trw_dir, results)
    with journal.step("S20"):  # delivery-complete event append
        _log_deliver_event(
            trw_dir,
            resolved_run,
            results,
            errors,
            deferred_status,
            critical_elapsed,
            call_ctx.session_id,
        )
    log_deliver_complete(
        resolved_run=resolved_run,
        results=results,
        errors=errors,
        deferred_status=deferred_status,
        critical_elapsed=critical_elapsed,
    )
    if journal.enabled:
        results["delivery_operation"] = journal.summary()
    return results


def _relative_run_identity(resolved_run: Path | None) -> str:
    """Project-relative run identity for the FR01 canonical request (never abs)."""
    if resolved_run is None:
        return ""
    try:
        from trw_mcp.state._paths import resolve_project_root

        return str(resolved_run.relative_to(resolve_project_root().resolve()))
    except Exception:  # justified: fall back to the bare run dir name, never abs path
        return resolved_run.name


def _open_journal(
    trw_dir: Path,
    config: TRWConfig,
    resolved_run: Path | None,
    *,
    skip_reflect: bool,
    skip_index_sync: bool,
    allow_unverified: bool,
    delivery_id: str,
    capability_token: str,
) -> tuple[DeliverJournal, DeliverResultDict | None]:
    """Open the PRD-CORE-208 delivery journal for this call (FR01 claim-first)."""
    return open_delivery_journal(
        trw_dir,
        config,
        run_identity=_relative_run_identity(resolved_run),
        skip_reflect=skip_reflect,
        skip_index_sync=skip_index_sync,
        allow_unverified=allow_unverified,
        delivery_id=delivery_id,
        capability_token=capability_token,
    )


def _journal_enqueue_deferred(journal: DeliverJournal, resolved_run: Path | None, *, skip_index_sync: bool) -> None:
    """Record the FR06 deferred-batch digest + mark the operation deferred-queued."""
    if not journal.enabled:
        return
    digest = compute_deferred_digest(
        run_identity=_relative_run_identity(resolved_run),
        skip_index_sync=skip_index_sync,
        deferred_steps=DEFERRED_STEPS,
    )
    journal.enqueue_deferred(digest)
    journal.mark_state(OperationState.DEFERRED_QUEUED)


def _probe_integrity(trw_dir: Path, resolved_run: Path | None, results: DeliverResultDict) -> None:
    try:
        from trw_mcp.tools._deliver_integrity import check_memory_integrity_on_deliver

        integrity_result = check_memory_integrity_on_deliver(trw_dir, resolved_run)
        # The full record (incl. db_path/checked_at) still persists to
        # events.jsonl for the audit trail inside the probe. On the happy path
        # (ok=True) the response dict is pure diagnostic noise — a static db_path
        # plus a checked_at that duplicates the top-level timestamp — so it is
        # surfaced in the compact response ONLY on a real corruption event, and
        # then only the actionable {ok, detail}.
        if not integrity_result["ok"]:
            results["db_integrity"] = {
                "ok": integrity_result["ok"],
                "detail": integrity_result["detail"],
            }
    except Exception as exc:  # justified: fail-open — integrity probe must not block deliver
        record_into(cast("MutableMapping[str, object]", results), "db_integrity", exc)


def _mark_deliver_and_reflect_learning(trw_dir: Path, results: DeliverResultDict) -> None:
    try:
        from trw_mcp.state.ceremony_progress import mark_deliver

        mark_deliver(trw_dir)
    except Exception as exc:  # justified: fail-open — state mutation must not block deliver
        record_into(cast("MutableMapping[str, object]", results), "mark_deliver", exc)
    try:
        from trw_mcp.state.ceremony_progress import read_ceremony_state
        from trw_mcp.tools import ceremony as _ceremony

        results["learning_reflection"] = _ceremony._learning_reflection_message(
            read_ceremony_state(trw_dir).learnings_this_session
        )
    except Exception as exc:  # justified: fail-open — reflection must not block deliver
        record_into(cast("MutableMapping[str, object]", results), "learning_reflection", exc)


def _write_nudge_analysis_artifact(trw_dir: Path, results: DeliverResultDict) -> None:
    """Write the live nudge-effectiveness artifact and surface a compact summary.

    Nudge-deep-dive work target #1/#2: computes responsiveness, per-step
    resistance, recall-pull, and timing-validity from this session's own
    ceremony-state + surface stream, writes ``.trw/context/nudge-analysis.json``,
    and attaches a summary (incl. flagged resistance steps) to the deliver
    result so an operator/loop can read the structural signal directly.

    Runs AFTER ``mark_deliver`` so the deliver step counts toward responsiveness.
    Fail-open: analysis is observability and must never block deliver.
    """
    try:
        from trw_mcp.state._session_id import resolve_effective_session_id
        from trw_mcp.state.nudge_analysis import (
            analysis_summary,
            compute_nudge_analysis,
            persist_nudge_analysis,
        )

        session_id = resolve_effective_session_id(trw_dir)
        analysis = compute_nudge_analysis(trw_dir, session_id=session_id)
        path = persist_nudge_analysis(trw_dir, analysis)
        if path is not None:
            summary = analysis_summary(analysis)
            # The artifact path is a static absolute path; surface it only when
            # there is real nudge activity to inspect (applicable=True). When
            # not applicable the summary is already just {"applicable": False}.
            if analysis.applicable:
                summary["artifact"] = str(path)
            results["nudge_analysis"] = summary
    except Exception as exc:  # justified: fail-open — nudge analysis must not block deliver
        record_into(cast("MutableMapping[str, object]", results), "nudge_analysis", exc)


def _log_deliver_event(
    trw_dir: Path,
    resolved_run: Path | None,
    results: DeliverResultDict,
    errors: list[str],
    deferred_status: str,
    critical_elapsed: float,
    session_id: str,
) -> None:
    if resolved_run is None or not (resolved_run / "meta").exists():
        return
    try:
        from trw_mcp.state.ceremony_progress import read_ceremony_state

        nudge_summary = dict(read_ceremony_state(trw_dir).nudge_counts)
    except Exception as exc:  # justified: fail-open
        record_into(cast("MutableMapping[str, object]", results), "deliver_nudge_summary", exc, severity="info")
        nudge_summary = {}
    from trw_mcp.tools import ceremony as _ceremony

    _ceremony._events.log_event(
        resolved_run / "meta" / "events.jsonl",
        "trw_deliver_complete",
        {
            "critical_steps_completed": results.get("critical_steps_completed"),
            "deferred": deferred_status,
            "critical_elapsed_seconds": critical_elapsed,
            "errors": len(errors),
            "nudge_summary": nudge_summary,
            "session_id": session_id,
        },
    )
