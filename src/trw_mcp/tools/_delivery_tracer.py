"""Declared delivery-effect dispatch contract — PRD-CORE-208 FR03.

Belongs to the ``tools/_delivery_operations.py`` facade family. The static
registry (:mod:`trw_mcp.tools._delivery_effect_registry`) is ONE census
authority; this module is the OTHER — it declares which registry effect IDs the
LIVE ``run_trw_deliver`` critical path and deferred batch dispatch, and provides
the reconciliation used by the FR03 dispatcher-reachability test (FPI-8).

:func:`read_journaled_step_ids` is a PROJECTION of the journal's own rows, and
only that. PRD-FIX-127 FR05 demoted it: this module used to describe the
projection as instrumentation that could not misreport what the dispatcher
touched, but reading back the ids the wiring itself wrote can only detect a
DELETED ``step()`` call, never an ADDED unjournaled mutation — which is the
failure a census gate exists to catch. The real input/output tracer, which
observes durable writes at the ``FileStateWriter`` / ``FileEventLogger`` / SQLite
seams and attributes each to an open boundary, lives in
:mod:`trw_mcp.tools._delivery_io_tracer`.

This module is data + pure query helpers only (no I/O beyond reading an already
open store), so it can be imported by the wiring, the deferred batch, and the
tests without side effects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trw_mcp.tools._delivery_effect_registry import all_effect_ids

if TYPE_CHECKING:
    from trw_mcp.tools._delivery_operations import DeliveryCoordinator

#: Synchronous critical-path effects the live ``run_trw_deliver`` journals with a
#: claim-first ``begin_step``/``finalize_step`` boundary. This is the single
#: source of truth the wiring imports so the journaled set can never silently
#: drift from the contract the FR03 test enforces. Each ID maps to a §6.6 owner
#: call point that is a discrete statement in ``run_trw_deliver``.
SYNCHRONOUS_DISPATCH_EFFECTS: frozenset[str] = frozenset(
    {
        "S01",  # try_update_phase (run phase write)
        "S05",  # copy_compliance_artifacts
        "S08",  # _do_reflect (mechanically extracted learning writes)
        "S11",  # _step_checkpoint (checkpoint record append)
        "S14",  # step_clear_score (CLEAR score JSON replace)
        "S15",  # step_knowledge_sync (knowledge topic synchronization)
        "S17",  # step_session_changelog (session changelog write)
        "S18",  # mark_deliver (ceremony deliver-called flag)
        "S19",  # _write_nudge_analysis_artifact (nudge-analysis JSON write)
        "S20",  # _log_deliver_event (delivery-complete event append)
        "S23",  # _persist_decision_set (gate decision-set receipt writes)
        "S22",  # step_project_handoff (project handoff + remaining-work section)
    }
)

#: Deferred roster step name -> its representative census effect ID, journaled by
#: the single ``_timed_step`` chokepoint in ``_run_deferred_steps``. Keeps the
#: live deferred batch (incl. the NON_REPLAYABLE trust increment D16) crash-safe:
#: a process death mid-step leaves that ID ``started``, which the PRD-FIX-127
#: ``resume`` action classifies before granting a lease.
DEFERRED_STEP_EFFECT_IDS: dict[str, str] = {
    "auto_prune": "D01",
    "consolidation": "D02",
    "tier_sweep": "D03",
    "memory_decay": "D25",
    "index_sync": "D04",
    "auto_progress": "D06",
    "publish_learnings": "D07",
    "outcome_correlation": "D09",
    "recall_outcome": "D10",
    "telemetry": "D12",
    "batch_send": "D14",
    "trust_increment": "D16",
    "ceremony_feedback": "D18",
    # PRD-FIX-127 FR05: the input/output tracer proved this roster step is NOT
    # pure computation — it appends a ``rollout_meta_tune_linkage`` event to the
    # run's events.jsonl (``_deferred_steps_learning``). That append had no
    # descriptor at all; D26 registers it, and D22 moved off this step onto the
    # run-yaml write it is actually named for.
    "delivery_metrics": "D26",
}

#: Deferred census IDs journaled OUTSIDE the roster chokepoint, at the real write
#: they are registered for. ``D22`` moved here from the ``delivery_metrics``
#: roster step (PRD-FIX-127 FR04): that step is pure computation — a git diff plus
#: scoring that writes nothing — while the run-yaml write D22 is named for sat
#: outside the boundary. ``D23``/``D24`` are the pre-terminal evidence + audit
#: writes.
DEFERRED_POST_ROSTER_EFFECTS: frozenset[str] = frozenset({"D22", "D23", "D24"})

#: Every census ID the live deferred batch dispatches.
DEFERRED_DISPATCH_EFFECTS: frozenset[str] = frozenset(DEFERRED_STEP_EFFECT_IDS.values()) | DEFERRED_POST_ROSTER_EFFECTS


def read_journaled_step_ids(coordinator: DeliveryCoordinator, operation_id: str) -> frozenset[str]:
    """Project the effect IDs that have a durable step row for ``operation_id``.

    This is a read of the journal's OWN steps, not an observation of I/O: it can
    prove a declared boundary was reached, and nothing else. Use
    :mod:`trw_mcp.tools._delivery_io_tracer` when the question is whether a durable
    write happened OUTSIDE a boundary (PRD-FIX-127 FR05).
    """
    conn = coordinator.store.connect()
    try:
        steps = coordinator.store.get_steps(conn, operation_id)
    finally:
        conn.close()
    return frozenset(step.effect_id for step in steps)


def reconcile_runtime_dispatch(observed: frozenset[str], *, expected: frozenset[str]) -> dict[str, tuple[str, ...]]:
    """Reconcile an observed runtime trace against the declared dispatch contract.

    A clean dispatch has both fields empty (FR03 acceptance). Any non-empty field
    fails the dispatcher-reachability gate:

    - ``orphan`` / ``unclassified``: an observed mutation with no registered
      descriptor (an unclassified delivery side effect).
    - ``uncovered``: a declared dispatch effect the live path did NOT journal
      (unwired / unreachable production boundary).
    """
    registered = all_effect_ids()
    orphan = tuple(sorted(observed - registered))
    uncovered = tuple(sorted(expected - observed))
    return {"orphan": orphan, "uncovered": uncovered, "unclassified": orphan}
