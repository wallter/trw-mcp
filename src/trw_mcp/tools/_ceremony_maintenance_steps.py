"""Fail-open maintenance sub-steps for session_start auto-maintenance.

Belongs to the ``_ceremony_helpers.py`` facade. Re-exported there for
back-compat. ``run_auto_maintenance`` keeps the orchestration and calls each
sub-step defined here, so the parent stays under the 350 effective-LOC module
gate — the same split already used by ``_ceremony_embeddings_maintenance.py``
for the embeddings sub-step.

Every helper is fail-open: an individual failure is logged and swallowed so it
can never block ``trw_session_start``.

Logging goes through :func:`_facade_logger` rather than a module-level logger.
Tests patch ``trw_mcp.tools._ceremony_helpers.logger`` and assert on warnings
emitted from these sub-steps, so the name is resolved through the parent module
at call time to keep those monkeypatches effective.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoMaintenanceDict
from trw_mcp.state.deferral_ledger import (
    DeferralDecision,
    record_completion,
    step_deferral_decision,
    step_outcome,
)
from trw_mcp.state.memory_pressure import WriterCensus

if TYPE_CHECKING:
    from trw_mcp.state.learn_journal import LearnJournalDrainResult
    from trw_mcp.tools._learn_journal_wiring import SweepContext

# PRD-FIX-130-FR02: single-flight guard for the background drain continuation.
# Same shape as ``state/_memory_connection._BACKFILL_THREAD`` — one daemon
# thread at a time, cleared in a ``finally`` only while it still points at the
# thread that is exiting, so an overlapping schedule can never orphan a handle.
_DRAIN_THREAD: threading.Thread | None = None
_DRAIN_LOCK = threading.Lock()


def _facade_logger() -> Any:
    """Return the ``_ceremony_helpers`` logger, resolved at call time.

    Late lookup through the parent module (not an import-time binding) so a
    test monkeypatch on ``_ceremony_helpers.logger`` is observed here.
    """
    from trw_mcp.tools import _ceremony_helpers

    return _ceremony_helpers.logger


def _check_version_sentinel(
    trw_dir: Path,
    maintenance: AutoMaintenanceDict,
) -> None:
    """Detect if the installer wrote a newer version since this process started.

    The installer writes ``.trw/installed-version.json`` after upgrading.
    If the on-disk version is newer than the running version, inject an
    ``update_advisory`` telling the user to run ``/mcp`` to reload.
    """
    sentinel = trw_dir / "installed-version.json"
    if not sentinel.is_file():
        return

    try:
        data = json.loads(sentinel.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    installed_version = str(data.get("version", ""))
    if not installed_version:
        return

    # Compare with running version
    try:
        from importlib.metadata import version as pkg_version

        running_version = pkg_version("trw-mcp")
    except Exception:  # justified: importlib.metadata may fail in edge cases
        return

    # Potemkin defect D (sub_zAfRqZYYq2KtF72d): fire ONLY when the on-disk
    # installed version is genuinely NEWER than the running process — a real
    # pending upgrade that a ``/mcp`` reload would apply. The previous bare
    # ``!=`` check also fired when on-disk was OLDER than (or differently
    # formatted from) the running version, e.g. a stale sentinel left by a
    # downgrade or a server that out-lived the on-disk install. That produced
    # the confusing "vOLD was installed but still running vNEW — reload"
    # advisory the operator reported (reloading would DOWN-grade, not update).
    # Reuse the canonical semver comparator so the direction logic lives in one
    # place; it fails closed (no advisory) on any unparseable version.
    from trw_mcp.state.auto_upgrade import _compare_versions

    if _compare_versions(running_version, installed_version) and "update_advisory" not in maintenance:
        maintenance["update_advisory"] = (
            f"TRW v{installed_version} is installed on disk but this MCP server is still "
            f"running v{running_version}. Run /mcp to reload."
        )


def _run_learn_journal_drain(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    *,
    census: WriterCensus,
    defer_memory_heavy: bool,
) -> None:
    """Replay any learnings journaled-but-not-stored by an interrupted session.

    This is the REAL CONSUMER that closes the durability loop: a write-ahead
    record with no drain is itself the same defect class it guards against. Runs
    on every session_start; a no-op (and zero payload cost) when nothing is
    pending.

    Writer pressure THROTTLES this sweep, it no longer cancels it
    (PRD-INFRA-171-FR06). Cancelling was measured to be permanent — one peer MCP
    instance is enough to trip the pressure gate, so across 122 log files 42
    records were journaled and not one sweep ever ran. Under pressure the sweep
    now replays a bounded budget (see
    :func:`~trw_mcp.state.learn_journal.pressure_drain_budget`): a small
    minimum-progress floor plus any record past the age bound. That keeps the
    deferral's intent — recovery must not fight a live writer for the memory
    backend — as a SMALLER sweep rather than as no sweep, and the deferral
    advisory is still emitted for whatever the budget could not take.
    """
    if not config.learn_journal_enabled:
        return
    # PRD-INFRA-171-FR06 is preserved exactly: pressure THROTTLES this sweep to a
    # bounded budget, it does not cancel it. The PRD-CORE-257 ledger only adds a
    # BOUND to the residue — a budget throttled to zero is a real deferral and
    # expires like any other covered step. The ledger is consulted only once the
    # sweep knows it has work, so an idle journal never opens a phantom streak.
    decision: DeferralDecision | None = None
    drained_anything = False
    try:
        from trw_mcp.state import learn_journal
        from trw_mcp.tools._learn_journal_wiring import make_sweep_replay

        limit = config.learn_journal_drain_limit
        if defer_memory_heavy:
            if learn_journal.pending_count(trw_dir, learnings_dir=config.learnings_dir) == 0:
                return
            decision = _step_pressure_decision(trw_dir, config, maintenance, "pending_learns", defer_memory_heavy=True)
            # FR06: writer pressure and the age hatch set the per-sweep COUNT.
            # The wall-clock budget below is applied OVER that count on both the
            # pressured and unpressured branches, so an aged record is admitted
            # to the queue by its age and stopped by the clock like any other.
            limit = learn_journal.pressure_drain_budget(
                trw_dir,
                drain_limit=config.learn_journal_drain_limit,
                min_batch=config.learn_journal_drain_min_batch,
                max_age_seconds=config.learn_journal_pending_max_age_hours * 3600.0,
                learnings_dir=config.learnings_dir,
            )
            if limit <= 0 and not decision.expired:
                _defer_learn_journal_drain(maintenance, census, decision)
                _record_step_outcome(trw_dir, maintenance, "pending_learns", decision)
                return
            limit = max(limit, config.learn_journal_drain_min_batch if decision.expired else limit)

        budget_ms = config.learn_journal_drain_budget_ms
        # FIX130-04: ONE context for the whole sweep. When the budget splits the
        # sweep, this same context is handed to the continuation, which owns the
        # single flush — building a second one there paid a second index write
        # and a second active-set materialization, breaking the FR03 bound on
        # exactly the runs FR02 exists for.
        sweep = make_sweep_replay(trw_dir, config)
        flush_handled = False
        try:
            drain_result = learn_journal.drain_pending(
                trw_dir,
                sweep.replay,
                limit=limit,
                learnings_dir=config.learnings_dir,
                max_attempts=config.learn_journal_max_replay_attempts,
                budget_seconds=budget_ms / 1000.0,
            )
            if drain_result:
                flush_handled = _finish_drain_sweep(
                    trw_dir,
                    config,
                    maintenance,
                    drain_result,
                    sweep=sweep,
                    limit=limit,
                    budget_ms=budget_ms,
                    under_pressure=defer_memory_heavy,
                )
        finally:
            if not flush_handled:
                sweep.flush()
        drained_anything = True
        if defer_memory_heavy and decision is not None and int(drain_result.get("deferred", 0)) > 0:
            _defer_learn_journal_drain(maintenance, census, decision)
    except Exception:  # justified: fail-open, journal recovery must never block session start
        _facade_logger().warning("maintenance_learn_journal_drain_failed", exc_info=True)
        if decision is not None:
            _record_step_outcome(trw_dir, maintenance, "pending_learns", decision, failed=True)
        # trw-fail-silent-allow: the "failed" outcome is already recorded above via _record_step_outcome
        return
    if decision is not None and drained_anything:
        _record_step_outcome(trw_dir, maintenance, "pending_learns", decision)


def _finish_drain_sweep(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    drain_result: LearnJournalDrainResult,
    *,
    sweep: SweepContext,
    limit: int,
    budget_ms: int,
    under_pressure: bool,
) -> bool:
    """Schedule the continuation, flush if we own it, and report what happened.

    Returns True once the sweep's index flush has been handled — either
    performed here or handed to the continuation that now owns the context.

    Truthfulness rules this function exists to hold (FIX130-02, FIX130-08):

    * a remainder the running worker will pick up is reported as
      ``deferred_to_background``; a remainder nobody is coming for is reported as
      ``deferred_to_next_sweep``. It is NEVER silently zeroed, which is what the
      previous ``to_background = 0`` on a refused schedule did.

    Batch migration is explicit maintenance, not a pending capture obligation.
    """
    replayed_inline = int(drain_result.get("replayed", 0))
    deferred = int(drain_result.get("deferred", 0))
    budget_exhausted = bool(drain_result.get("budget_exhausted", False))
    # FR02: only a BUDGET stop earns a continuation. A count-limit or pressure
    # deferral keeps its current meaning — those records wait for the next sweep
    # and are still reported via pending_learns_deferred.
    remainder = min(deferred, max(0, limit - replayed_inline)) if budget_exhausted else 0
    scheduled = bool(remainder) and _schedule_background_drain(trw_dir, config, remainder, False, sweep=sweep)
    if not scheduled:
        sweep.flush()
    payload = dict(drain_result)
    # PC-8: nested inside an ALREADY-allowlisted key, because
    # _ceremony_step_table copies maintenance results through an explicit tuple
    # and would silently drop a new top-level key.
    payload["replayed_inline"] = replayed_inline
    payload["deferred_to_background"] = remainder if scheduled else 0
    if remainder and not scheduled:
        payload["deferred_to_next_sweep"] = remainder
    if sweep.index_failed():
        payload["index_update_failed"] = True
    if sweep.degraded():
        payload["active_set_degraded"] = True
    maintenance["pending_learns_replayed"] = payload
    # FR06 (d): make sweep liveness observable from the ceremony layer. Before
    # this, only FAILURE was visible here — success lived solely in the response
    # payload, so the journaled-to-drained ratio could not be measured from logs.
    _facade_logger().info(
        "learn_journal_drain_completed",
        replayed=replayed_inline,
        recovered=int(drain_result.get("recovered", 0)),
        dead_lettered=int(drain_result.get("dead_lettered", 0)),
        retained=int(drain_result.get("retained", 0)),
        deferred=deferred,
        contended=int(drain_result.get("contended", 0)),
        under_pressure=under_pressure,
        replayed_inline=replayed_inline,
        deferred_to_background=remainder if scheduled else 0,
        deferred_to_next_sweep=remainder if not scheduled else 0,
        budget_ms=budget_ms,
        budget_exhausted=budget_exhausted,
    )
    return True


def _schedule_background_drain(
    trw_dir: Path,
    config: TRWConfig,
    background_limit: int,
    run_migration: bool,
    *,
    sweep: SweepContext | None = None,
) -> bool:
    """Continue the budget-stopped sweep on ONE background daemon thread (FR02).

    Single-flight, mirroring ``state/_memory_connection._schedule_post_recovery_backfill``:
    a second overlapping call starts no second thread and says so. Returns True
    when a thread was started, so the caller only reports records as deferred to
    a continuation that actually exists — and reports the rest as owed to the
    NEXT sweep rather than zeroing it (FIX130-02).

    **The running worker picks up an overlapping remainder.** The worker re-lists
    the pending directory after each batch (see
    :func:`trw_mcp.tools._learn_journal_background.run_background_sweep`), so
    records that appeared after its first snapshot are drained by the flight
    already in the air. A refused schedule is therefore "already covered", not
    "silently dropped" — and the caller still says which of the two it was.

    **Cross-process safety is a CLAIM, not an assumption.** Two stdio server
    processes can each schedule a continuation over the same pending directory.
    Each record is claimed atomically before its replay
    (:mod:`trw_mcp.state._learn_journal_claims`), so the second drain reports the
    record as ``contended`` instead of re-entering ``execute_learn`` for it. The
    earlier argument — that whichever process consumes the file first wins —
    only ever covered SEQUENTIAL arrival, not two live loops.

    **Daemon durability.** ``daemon=True`` means interpreter exit can kill this
    thread mid-replay, and that is safe: a pending file is unlinked only AFTER
    ``execute_learn`` reaches a terminal outcome (stored, deduped, or
    quarantined). A kill anywhere before that leaves the record on disk with its
    attempt count intact and its claim reclaimable by the staleness rule, so the
    next session replays it. Nothing accepted is ever lost by having been
    deferred; the record simply lands one session later, which is exactly the
    pre-PRD-FIX-130 behaviour.
    """
    global _DRAIN_THREAD

    def _run_drain() -> None:
        global _DRAIN_THREAD
        from trw_mcp.tools._learn_journal_background import run_background_sweep

        try:
            run_background_sweep(trw_dir, config, background_limit, run_migration, sweep)
        finally:
            with _DRAIN_LOCK:
                if _DRAIN_THREAD is threading.current_thread():
                    _DRAIN_THREAD = None

    with _DRAIN_LOCK:
        if _DRAIN_THREAD is not None and _DRAIN_THREAD.is_alive():
            _facade_logger().warning("learn_journal_drain_already_running", trw_dir=str(trw_dir))
            return False
        thread = threading.Thread(target=_run_drain, name="trw-learn-drain", daemon=True)
        _DRAIN_THREAD = thread
        thread.start()
    _facade_logger().info(
        "learn_journal_background_drain_scheduled",
        records=background_limit,
        migration=run_migration,
    )
    return True


def _defer_learn_journal_drain(
    maintenance: AutoMaintenanceDict, census: WriterCensus, decision: DeferralDecision
) -> None:
    """Record that pressure held records back — retained, not replaced, by FR06."""
    maintenance["pending_learns_deferred"] = _writer_pressure_details(census, decision)
    _facade_logger().warning("learn_journal_drain_deferred", **_census_log_fields(census))


def _writer_pressure_details(census: WriterCensus, decision: DeferralDecision) -> dict[str, object]:
    from trw_mcp.state.memory_pressure import writer_pressure_details

    return writer_pressure_details(census, decision)


def _step_pressure_decision(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    step: str,
    *,
    defer_memory_heavy: bool,
) -> DeferralDecision:
    """Consult the bounded ledger for one covered step (PRD-CORE-257-FR03).

    A step whose deferral streak has reached ``session_start_max_deferral_hours``
    runs anyway, and — when this process is the single winner of the forced run —
    its name is appended to the top-level ``deferral_expired_ran`` list so the
    response says which bound fired rather than leaving it to the log.
    """
    decision = step_deferral_decision(
        trw_dir,
        step,
        under_pressure=defer_memory_heavy,
        max_deferral_hours=config.session_start_max_deferral_hours,
    )
    if decision.expired:
        expired = maintenance.setdefault("deferral_expired_ran", [])
        expired.append(step)
        _facade_logger().warning(
            "deferral_bound_expired_running",
            step=step,
            age_hours=decision.age_hours,
            max_deferral_hours=config.session_start_max_deferral_hours,
        )
    return decision


def _record_step_outcome(
    trw_dir: Path,
    maintenance: AutoMaintenanceDict,
    step: str,
    decision: DeferralDecision,
    *,
    failed: bool = False,
) -> None:
    """Record one step's outcome from the closed FR12 vocabulary and close its streak."""
    outcome = step_outcome(decision, failed=failed)
    outcomes = maintenance.setdefault("step_outcomes", {})
    outcomes[step] = outcome
    if outcome in {"executed", "expired_ran"}:
        record_completion(trw_dir, step)


def _census_log_fields(census: WriterCensus) -> dict[str, object]:
    """Per-site structlog fields for one deferral.

    PRD-CORE-257-FR07 moved the census itself to a single INFO line per
    session_start, so a per-site warning states WHICH step deferred against
    which measurement without restating the whole census six times.
    """
    return {
        "reason": "writer_pressure",
        "writer_count": census.writer_count,
        "peer_writer_count": census.peer_writer_count,
        "threshold": census.threshold,
    }


def _run_wal_maintenance(
    trw_dir: Path,
    maintenance: AutoMaintenanceDict,
) -> None:
    """Run the WAL checkpoint without coupling its failures to other maintenance.

    PRD-CORE-248 FR04 clause 3 deleted the writer-pressure deferral branch that
    used to live here (and the ``wal_checkpoint_deferred`` advisory it emitted).
    ``defer_memory_heavy`` is the ``writer_count >= 2`` verdict, and two live
    editor sessions is the steady state on a working machine — so the checkpoint
    was permanently skipped and the WAL grew without bound (measured: 25.0 MiB
    against a 10 MB threshold). Writer pressure now selects the checkpoint MODE
    inside ``maybe_checkpoint_wal`` (PASSIVE under pressure, which never resets
    the WAL and is safe at any connection count) instead of cancelling the work,
    matching the PRD-INFRA-171 FR06 precedent set for the journal drain.
    """
    try:
        from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

        wal_result = maybe_checkpoint_wal(trw_dir)
        if wal_result.get("checkpointed"):
            maintenance["wal_checkpoint"] = wal_result
    except Exception:  # justified: fail-open, WAL checkpoint must not block session start
        _facade_logger().warning("maintenance_wal_checkpoint_failed", exc_info=True)


__all__ = [
    "_census_log_fields",
    "_check_version_sentinel",
    "_defer_learn_journal_drain",
    "_finish_drain_sweep",
    "_record_step_outcome",
    "_run_learn_journal_drain",
    "_run_wal_maintenance",
    "_schedule_background_drain",
    "_step_pressure_decision",
    "_writer_pressure_details",
]
