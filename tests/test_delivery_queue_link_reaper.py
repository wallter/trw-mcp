"""PRD-CORE-208 FR06: deferred queue-link reapers (terminal-transition + stale sweep).

Regression guard for the resource leak where terminal/abandoned operations left
their ``queued`` links behind, so ``queue_links`` accumulated to
``delivery_queue_depth_max`` and permanently wedged ``enqueue_deferred`` with
``deferred_queue_full``. Two coupled reapers close the leak while never dropping
live deferred work.
"""

from __future__ import annotations

import os

import pytest

from tests._delivery_support import make_coordinator, make_uuid7, strong_capability
from trw_mcp.tools._delivery_models import OperationRecord, OperationState, QueueLink, QueueState
from trw_mcp.tools._delivery_recovery import reap_orphaned_queue_links, terminal_queue_state
from trw_mcp.tools._delivery_request import DeliveryRequestError

_STALE_MS = 15 * 60 * 1000
_NOW = 4_000_000_000_000


def _queued_count(coord) -> int:  # type: ignore[no-untyped-def]
    conn = coord.store.connect()
    try:
        return coord.store.count_queue(conn, (QueueState.QUEUED,))
    finally:
        conn.close()


def _link_state(coord, operation_id: str) -> QueueState | None:  # type: ignore[no-untyped-def]
    conn = coord.store.connect()
    try:
        for link in coord.store.get_queue(conn):
            if link.operation_id == operation_id:
                return link.state
        return None
    finally:
        conn.close()


def _seed_op_with_link(
    coord,  # type: ignore[no-untyped-def]
    *,
    enqueued_utc_ms: int,
    op_state: OperationState = OperationState.RUNNING,
    lease_expiry_utc_ms: int = 0,
    lease_pid: int = 0,
) -> str:
    """Seed one operation row + QUEUED link directly with a controlled age/lease.

    Bypasses ``claim`` so no claim-time ``run_maintenance`` sweep runs between
    insertions — this reproduces the legacy leak state (pre-fix orphans already
    on disk) deterministically so the reaper can be tested in isolation.
    """
    did = make_uuid7()
    conn = coord.store.connect()
    try:
        with coord.store.immediate(conn):
            coord.store.insert_operation(
                conn,
                OperationRecord(
                    operation_id=did,
                    project_scope=coord.project_scope,
                    request_digest="d" * 16,
                    state=op_state,
                    revision=1,
                    created_utc_ms=_NOW,
                    updated_utc_ms=_NOW,
                    expiry_utc_ms=_NOW + _STALE_MS,
                    lease_expiry_utc_ms=lease_expiry_utc_ms,
                    lease_pid=lease_pid,
                ),
            )
            coord.store.insert_queue_link(
                conn,
                QueueLink(
                    operation_id=did,
                    deferred_digest=f"digest-{did[:8]}",
                    state=QueueState.QUEUED,
                    enqueued_utc_ms=enqueued_utc_ms,
                ),
            )
    finally:
        conn.close()
    return did


def _sweep(coord) -> int:  # type: ignore[no-untyped-def]
    conn = coord.store.connect()
    try:
        with coord.store.immediate(conn):
            return reap_orphaned_queue_links(coord.store, conn, _NOW, _STALE_MS)
    finally:
        conn.close()


# --- Reaper 1: terminal-transition reap frees FIFO depth ---


@pytest.mark.parametrize(
    ("terminal", "expected_link_state"),
    [
        (OperationState.SUCCEEDED, QueueState.DONE),
        (OperationState.FAILED, QueueState.DONE),
        (OperationState.CANCELLED, QueueState.CANCELLED),
    ],
)
def test_terminal_transition_retires_queue_link_and_frees_depth(tmp_path, terminal, expected_link_state) -> None:
    """FR06: mark_operation_state into any terminal state retires the QUEUED link."""
    coord = make_coordinator(tmp_path, queue_depth=1)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability())
    link = coord.enqueue_deferred(did, "digest-A")
    assert link.state is QueueState.QUEUED
    assert _queued_count(coord) == 1  # fills depth=1

    coord.mark_operation_state(did, terminal)

    assert _link_state(coord, did) is expected_link_state
    assert _queued_count(coord) == 0  # depth freed

    # A brand-new deferred delivery now enqueues successfully (depth is truly free).
    other = make_uuid7()
    coord.claim(delivery_id=other, capability_token=strong_capability())
    assert coord.enqueue_deferred(other, "digest-B").state is QueueState.QUEUED


def test_finalize_step_completing_cancellation_retires_queue_link(tmp_path) -> None:
    """FR06: a step finalizing the last effect of a cancellation retires the link."""
    coord = make_coordinator(tmp_path, queue_depth=1)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w", pid=1)
    coord.enqueue_deferred(did, "digest-A")
    coord.begin_step(did, "S01", owner="w", pid=1)
    # CANCEL_REQUESTED is not terminal, so the link is still QUEUED here.
    coord.mark_operation_state(did, OperationState.CANCEL_REQUESTED)
    assert _queued_count(coord) == 1

    from trw_mcp.tools._delivery_models import StepState

    coord.finalize_step(did, "S01", state=StepState.SUCCEEDED, proof_digest="d1")

    assert _link_state(coord, did) is QueueState.CANCELLED
    assert _queued_count(coord) == 0


# --- Reaper 2: bounded stale-QUEUED sweep ---


def test_stale_sweep_reaps_orphaned_and_abandoned_links(tmp_path) -> None:
    """FR06: terminal-owned and stale/no-live-lease QUEUED links are reaped."""
    coord = make_coordinator(tmp_path)
    # (a) owning op already terminal -> reaped regardless of age.
    terminal_op = _seed_op_with_link(coord, enqueued_utc_ms=_NOW, op_state=OperationState.SUCCEEDED)
    # (b) past-stale link, dead/absent lease -> reaped.
    abandoned = _seed_op_with_link(
        coord, enqueued_utc_ms=_NOW - 2 * _STALE_MS, op_state=OperationState.RUNNING, lease_pid=0
    )

    reaped = _sweep(coord)

    assert reaped == 2
    assert _link_state(coord, terminal_op) is QueueState.DONE
    assert _link_state(coord, abandoned) is QueueState.CANCELLED
    assert _queued_count(coord) == 0


def test_stale_sweep_leaves_fresh_and_live_owned_links_intact(tmp_path) -> None:
    """FR06/NFR04: a live-owned or still-fresh QUEUED link is never dropped."""
    coord = make_coordinator(tmp_path)
    # (a) past-stale link but the owning op holds a LIVE lease (this process) -> kept.
    live = _seed_op_with_link(
        coord,
        enqueued_utc_ms=_NOW - 2 * _STALE_MS,
        op_state=OperationState.RUNNING,
        lease_expiry_utc_ms=_NOW + _STALE_MS,
        lease_pid=os.getpid(),
    )
    # (b) fresh link (inside the stale window), non-terminal op, no lease -> kept.
    fresh = _seed_op_with_link(
        coord, enqueued_utc_ms=_NOW - _STALE_MS // 2, op_state=OperationState.RUNNING, lease_pid=0
    )

    reaped = _sweep(coord)

    assert reaped == 0
    assert _link_state(coord, live) is QueueState.QUEUED
    assert _link_state(coord, fresh) is QueueState.QUEUED
    assert _queued_count(coord) == 2


def test_enqueue_still_rejects_when_queue_full_of_live_work(tmp_path) -> None:
    """FR06/NFR04 regression: the self-heal sweep does not weaken a legitimate full queue."""
    coord = make_coordinator(tmp_path, queue_depth=1)
    # A fresh, live QUEUED link fills depth=1 and must survive the self-heal sweep.
    first = make_uuid7()
    coord.claim(delivery_id=first, capability_token=strong_capability())
    coord.enqueue_deferred(first, "digest-B")

    second = make_uuid7()
    coord.claim(delivery_id=second, capability_token=strong_capability())
    with pytest.raises(DeliveryRequestError) as exc:
        coord.enqueue_deferred(second, "digest-C")
    assert exc.value.code == "deferred_queue_full"
    assert coord.project_status(first)["queue_disposition"] == "queued"


def test_enqueue_self_heals_a_wedged_queue_of_orphans(tmp_path) -> None:
    """End-to-end: a queue full of terminal-linked orphans self-heals so a deliver succeeds."""
    depth = 4
    coord = make_coordinator(tmp_path, queue_depth=depth)
    # Fill the entire FIFO depth with QUEUED links whose owning ops are already terminal.
    for _ in range(depth):
        _seed_op_with_link(coord, enqueued_utc_ms=_NOW, op_state=OperationState.SUCCEEDED)
    assert _queued_count(coord) == depth  # wedged: no room left

    fresh = make_uuid7()
    coord.claim(delivery_id=fresh, capability_token=strong_capability())
    # Without the self-heal sweep this would raise deferred_queue_full forever.
    link = coord.enqueue_deferred(fresh, "digest-live")

    assert link.state is QueueState.QUEUED
    assert _queued_count(coord) == 1  # only the live one remains queued


def test_terminal_queue_state_mapping() -> None:
    """The retirement-state mapping keeps cancellation distinct from done."""
    assert terminal_queue_state(OperationState.CANCELLED) is QueueState.CANCELLED
    assert terminal_queue_state(OperationState.SUCCEEDED) is QueueState.DONE
    assert terminal_queue_state(OperationState.FAILED) is QueueState.DONE
