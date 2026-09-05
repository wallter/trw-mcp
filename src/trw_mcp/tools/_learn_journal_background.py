"""Body of the FR02 background drain continuation and the FR05 migration.

Belongs to ``tools/_ceremony_maintenance_steps.py``, which owns the single-flight
``_DRAIN_THREAD`` handle and starts this body on it. Split out so the maintenance
module stays under the 350 effective-LOC gate.

Two behaviours here are corrections the first implementation got wrong:

* **The worker RE-LISTS** (FIX130-02). ``drain_pending`` takes a finite snapshot
  of the pending directory, so a remainder that appeared after the running
  worker's snapshot — or that a second, overlapping sweep handed over — was
  stranded until another session. The worker now loops until a pass attempts
  nothing or its own count cap is spent, so an overlapping sweep's remainder is
  picked up by the flight already in the air instead of being zeroed.
* **The migration's outcome is REPORTED, not assumed** (FIX130-08). The previous
  completion event said ``migration_run=True`` whenever one was requested, even
  when ``batch_dedup`` returned ``skipped`` and correctly declined to write the
  marker. A "done" that never happened is the failure class this PRD exists to
  remove, so the real ``BatchDedupResult`` status travels into the event.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from trw_mcp.models.config import TRWConfig

if TYPE_CHECKING:
    from trw_mcp.tools._learn_journal_wiring import SweepContext

#: Bound on the re-list loop. Each pass is capped by ``background_limit`` and a
#: pass that attempts nothing ends the loop, so this only stops a pathological
#: churn where records are re-journaled as fast as they drain.
_MAX_RELIST_PASSES = 8


def _facade_logger() -> Any:
    from trw_mcp.tools._ceremony_maintenance_steps import _facade_logger as resolve

    return resolve()


def run_background_sweep(
    trw_dir: Path,
    config: TRWConfig,
    background_limit: int,
    run_migration: bool,
    sweep: SweepContext | None,
) -> None:
    """Drain the budget-stopped remainder, then run any owed migration.

    Never raises: a background thread that takes the server with it is a worse
    defect than an un-drained record. Emits exactly one completion event, from
    exactly one place, carrying what actually happened.
    """
    started = time.monotonic()
    replayed = recovered = dead_lettered = retained = contended = 0
    passes = 0
    migration_outcome = "not_requested"
    migration_reason = ""
    index_failed = False
    degraded = False
    try:
        context = sweep if sweep is not None else _own_context(trw_dir, config)
        try:
            remaining = background_limit
            while remaining > 0 and passes < _MAX_RELIST_PASSES:
                from trw_mcp.state import learn_journal

                # budget_seconds is left at its unbounded default: off the hot
                # path, the remainder runs to the SAME per-sweep count limit.
                outcome = learn_journal.drain_pending(
                    trw_dir,
                    context.replay,
                    limit=remaining,
                    learnings_dir=config.learnings_dir,
                    max_attempts=config.learn_journal_max_replay_attempts,
                )
                passes += 1
                attempted = int(outcome.get("replayed", 0))
                replayed += attempted
                recovered += int(outcome.get("recovered", 0))
                dead_lettered += int(outcome.get("dead_lettered", 0))
                retained += int(outcome.get("retained", 0))
                contended += int(outcome.get("contended", 0))
                remaining -= attempted
                # FIX130-02: re-list only while the last pass made progress. A
                # pass that attempted nothing means the directory is empty, the
                # rest is claimed by a live peer, or every remaining record was
                # retained — none of which a further pass would change.
                if attempted == 0:
                    break
            if run_migration:
                result = run_batch_dedup_migration(trw_dir, config)
                migration_outcome = str(result.get("status", "unknown"))
                migration_reason = str(result.get("reason", ""))
        finally:
            index_failed = not context.flush()
            degraded = context.degraded()
    except Exception:  # justified: fail-open, a background thread must never take the server with it
        if migration_outcome == "not_requested" and run_migration:
            migration_outcome = "failed"
        _facade_logger().exception("learn_journal_background_drain_failed", trw_dir=str(trw_dir))
    finally:
        _facade_logger().info(
            "learn_journal_background_drain_completed",
            replayed=replayed,
            recovered=recovered,
            dead_lettered=dead_lettered,
            retained=retained,
            contended=contended,
            passes=passes,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
            migration_requested=run_migration,
            migration_outcome=migration_outcome,
            migration_reason=migration_reason,
            migration_run=migration_outcome == "completed",
            index_update_failed=index_failed,
            active_set_degraded=degraded,
        )


def _own_context(trw_dir: Path, config: TRWConfig) -> SweepContext:
    """Build a context when the caller had none to share (standalone drain)."""
    from trw_mcp.tools._learn_journal_wiring import make_sweep_replay

    return make_sweep_replay(trw_dir, config)


def run_batch_dedup_migration(trw_dir: Path, config: TRWConfig) -> dict[str, object]:
    """Run the one-time batch dedup off the replay path, once GLOBALLY (FR05).

    FIX130-07: "exactly once" was only ever process-local. Every stdio server
    process has its own thread handle and independently stats the same absent
    marker, so two could run the quadratic scan against the same sidecars
    concurrently. The marker is now claimed with the same cross-process primitive
    the per-record replay uses, and the need is RE-CHECKED after the claim is
    held — the window between "marker absent" and "claim acquired" is exactly
    where a peer finishes the work.

    The marker is written by ``batch_dedup`` itself and ONLY on completion, so a
    migration that was skipped, contended, or failed stays owed and the next
    sweep retries. A skipped run releases its claim in a ``finally``, so it
    leaves nothing behind for the next process to trip over.
    """
    from trw_mcp.state._learn_journal_claims import acquire_claim, release_claim
    from trw_mcp.state.dedup import batch_dedup, is_migration_needed

    marker = trw_dir / config.learnings_dir / "dedup_migration.yaml"
    claim = acquire_claim(marker)
    if claim is None:
        _facade_logger().info("batch_dedup_migration_contended", path=str(marker))
        return {"status": "contended", "reason": "another process holds the migration claim"}
    try:
        if not is_migration_needed(trw_dir):
            return {"status": "skipped", "reason": "migration already completed by a peer"}
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        result = batch_dedup(trw_dir, FileStateReader(), FileStateWriter(), config=config)
        return dict(result)
    finally:
        release_claim(claim)


__all__ = ["run_background_sweep", "run_batch_dedup_migration"]
