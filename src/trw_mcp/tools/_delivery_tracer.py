"""Runtime delivery-effect dispatch tracer — PRD-CORE-208 FR03.

Belongs to the ``tools/_delivery_operations.py`` facade family. The static
registry (:mod:`trw_mcp.tools._delivery_effect_registry`) is ONE census
authority; this module is the OTHER — it declares which registry effect IDs the
LIVE ``run_trw_deliver`` critical path and deferred batch actually dispatch, and
provides the reconciliation used by the FR03 dispatcher-reachability test
(FPI-8).

A journaled operation's own durable steps ARE the runtime trace: after a real
deliver, the set of effect IDs with a non-``not_started`` step equals the
declared dispatch contract below, and every observed ID resolves to exactly one
descriptor (no orphan / unclassified mutation). This closes the "delivered ≠
wired" gap — a new synchronous delivery mutation that is not journaled makes the
runtime trace diverge from :data:`SYNCHRONOUS_DISPATCH_EFFECTS`, and an observed
ID with no descriptor fails the census.

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
        "S20",  # _log_deliver_event (delivery-complete event append)
    }
)

#: Deferred roster step name -> its representative census effect ID, journaled by
#: the single ``_timed_step`` chokepoint in ``_run_deferred_steps``. Keeps the
#: live deferred batch (incl. the NON_REPLAYABLE trust increment D16) crash-safe:
#: a process death mid-step leaves that ID ``started`` for FR04 recovery.
DEFERRED_STEP_EFFECT_IDS: dict[str, str] = {
    "auto_prune": "D01",
    "consolidation": "D02",
    "tier_sweep": "D03",
    "index_sync": "D04",
    "auto_progress": "D06",
    "publish_learnings": "D07",
    "outcome_correlation": "D09",
    "recall_outcome": "D10",
    "telemetry": "D12",
    "batch_send": "D14",
    "trust_increment": "D16",
    "ceremony_feedback": "D18",
    "delivery_metrics": "D22",
}

#: The census IDs the live deferred batch dispatches (one per roster step).
DEFERRED_DISPATCH_EFFECTS: frozenset[str] = frozenset(DEFERRED_STEP_EFFECT_IDS.values())


def trace_journaled_effects(coordinator: DeliveryCoordinator, operation_id: str) -> frozenset[str]:
    """Return the runtime mutation trace = the operation's durable step IDs.

    The journal store is the authoritative runtime census: every effect the live
    delivery path crossed has a committed step row, so reading them back is a
    zero-instrumentation tracer that cannot lie about what the dispatcher touched.
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
