"""PRD-CORE-208 NFR03 + FR06: concurrency, bounded latency, deferred singleflight."""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests._delivery_support import make_coordinator, make_uuid7, strong_capability
from trw_mcp.tools._delivery_models import ClaimStatus, OperationState, QueueState, RecoverStatus
from trw_mcp.tools._delivery_request import DeliveryRequestError


def _claim_worker(trw_dir_str: str, did: str, cap: str, out) -> None:  # type: ignore[no-untyped-def]
    from tests._delivery_support import make_coordinator as _mk

    coord = _mk(Path(trw_dir_str))
    result = coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
    out.put(result.status.value)


def test_multi_process_single_claim_and_bounded_status_latency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR03: concurrent duplicate claims converge on ONE operation; no deadlock."""
    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(repo_root)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (repo_root, existing_pythonpath))))

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    did = make_uuid7()
    cap = strong_capability()

    ctx = mp.get_context("spawn")
    out: mp.Queue = ctx.Queue()
    procs = [ctx.Process(target=_claim_worker, args=(str(trw_dir), did, cap, out)) for _ in range(8)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=15)
        assert not proc.is_alive()  # no deadlock

    statuses = [out.get() for _ in range(8)]
    # Exactly one process performed the claim; the rest followed the same operation.
    assert statuses.count(ClaimStatus.CLAIMED.value) == 1
    assert all(s in {ClaimStatus.CLAIMED.value, ClaimStatus.EXISTING.value} for s in statuses)

    # And exactly one operation row exists.
    coord = make_coordinator(trw_dir)
    conn = coord.store.connect()
    ops = coord.store.iter_operations(conn)
    conn.close()
    assert len(ops) == 1
    assert ops[0].operation_id == did


def test_status_read_p95_latency_under_50ms(tmp_path) -> None:
    """NFR03: 100 read-only status calls have p95 <= 50 ms on the repo fixture."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity="task/run-1")

    samples = []
    for _ in range(100):
        start = time.perf_counter()
        coord.project_status(did)
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[94]
    assert p95 <= 50.0, f"status p95={p95:.2f}ms"


def test_duplicate_claim_read_p95_latency_under_100ms(tmp_path) -> None:
    """NFR03: 100 duplicate-claim reads have p95 <= 100 ms."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")

    samples = []
    for _ in range(100):
        start = time.perf_counter()
        coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[94]
    assert p95 <= 100.0, f"duplicate-claim p95={p95:.2f}ms"


# --- FR06: truthful deferred singleflight (attach vs FIFO queue) ---


def _make_running_batch(coord, digest: str) -> str:
    """Claim an operation and mark its deferred link RUNNING (owns the batch lease)."""
    owner = make_uuid7()
    coord.claim(delivery_id=owner, capability_token=strong_capability())
    coord.enqueue_deferred(owner, digest)
    conn = coord.store.connect()
    with coord.store.immediate(conn):
        coord.store.update_queue_state(conn, owner, QueueState.RUNNING)
    conn.close()
    return owner


def test_equal_digest_attaches_and_different_digest_queues_fifo(tmp_path) -> None:
    """FR06: equal deferred digest attaches; non-equal work stays durably queued."""
    coord = make_coordinator(tmp_path)
    _make_running_batch(coord, "digest-A")

    attacher = make_uuid7()
    coord.claim(delivery_id=attacher, capability_token=strong_capability())
    attach_link = coord.enqueue_deferred(attacher, "digest-A")
    assert attach_link.state is QueueState.ATTACHED

    queued = make_uuid7()
    coord.claim(delivery_id=queued, capability_token=strong_capability())
    queue_link = coord.enqueue_deferred(queued, "digest-B")
    assert queue_link.state is QueueState.QUEUED

    # The attacher records its attach linkage; the queued op is not falsely successful.
    status = coord.project_status(queued)
    assert status["queue_disposition"] == "queued"
    assert status["aggregate_success"] is False


def test_queue_full_rejects_without_dropping_existing(tmp_path) -> None:
    """FR06/NFR04: at the bounded depth new deferred work is deferred_queue_full."""
    coord = make_coordinator(tmp_path, queue_depth=1)
    _make_running_batch(coord, "digest-A")  # RUNNING, not counted against QUEUED depth

    first = make_uuid7()
    coord.claim(delivery_id=first, capability_token=strong_capability())
    coord.enqueue_deferred(first, "digest-B")  # 1 queued (fills depth=1)

    second = make_uuid7()
    coord.claim(delivery_id=second, capability_token=strong_capability())
    with pytest.raises(DeliveryRequestError) as exc:
        coord.enqueue_deferred(second, "digest-C")
    assert exc.value.code == "deferred_queue_full"

    # The already-queued operation is untouched.
    assert coord.project_status(first)["queue_disposition"] == "queued"


def test_takeover_reloads_revision_after_waiting_for_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=did, capability_token=capability, pid=0)
    original_connect = coord.store.connect
    writer = original_connect()
    results = []
    contender_conn: dict[str, object] = {}
    contender_conn_ready = threading.Event()
    start_contender = threading.Event()
    contender_at_immediate = threading.Event()
    original_immediate = coord.store.immediate

    @contextmanager
    def _signal_immediate(conn):  # type: ignore[no-untyped-def]
        contender_at_immediate.set()
        with original_immediate(conn) as transaction:
            yield transaction

    def _takeover() -> None:
        contender_conn["value"] = original_connect()
        contender_conn_ready.set()
        assert start_contender.wait(timeout=2)
        results.append(
            coord.takeover(
                operation_id=did,
                capability_token=capability,
                expected_revision=1,
                reason="recover stale owner",
                new_owner="replacement",
                new_pid=1234,
                owner_alive=False,
            )
        )

    monkeypatch.setattr(coord.store, "immediate", _signal_immediate)
    monkeypatch.setattr(coord.store, "connect", lambda: contender_conn["value"])
    contender = threading.Thread(target=_takeover)
    contender.start()
    assert contender_conn_ready.wait(timeout=2)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE operations SET revision=revision+1, state=? WHERE operation_id=?",
        (OperationState.BLOCKED.value, did),
    )
    start_contender.set()
    assert contender_at_immediate.wait(timeout=2)
    writer.commit()
    contender.join(timeout=5)
    writer.close()
    monkeypatch.setattr(coord.store, "connect", original_connect)

    assert not contender.is_alive()
    assert results[0].status is RecoverStatus.STALE_REVISION
    status = coord.project_status(did)
    assert status["revision"] == 2
    assert status["state"] == OperationState.BLOCKED.value


def test_crash_recovery_preserves_newer_live_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), pid=0)
    original_connect = coord.store.connect
    writer = original_connect()
    results = []
    contender_conn: dict[str, object] = {}
    contender_conn_ready = threading.Event()
    start_contender = threading.Event()
    contender_at_immediate = threading.Event()
    original_immediate = coord.store.immediate

    @contextmanager
    def _signal_immediate(conn):  # type: ignore[no-untyped-def]
        contender_at_immediate.set()
        with original_immediate(conn) as transaction:
            yield transaction

    def _recover() -> None:
        contender_conn["value"] = original_connect()
        contender_conn_ready.set()
        assert start_contender.wait(timeout=2)
        results.append(coord.recover_after_crash(did))

    monkeypatch.setattr(coord.store, "immediate", _signal_immediate)
    monkeypatch.setattr(coord.store, "connect", lambda: contender_conn["value"])
    contender = threading.Thread(target=_recover)
    contender.start()
    assert contender_conn_ready.wait(timeout=2)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE operations SET revision=revision+1, state=?, lease_owner=?, lease_pid=?, "
        "lease_expiry_utc_ms=? WHERE operation_id=?",
        (OperationState.BLOCKED.value, "new-live-owner", os.getpid(), 9_000_000_000_000, did),
    )
    start_contender.set()
    assert contender_at_immediate.wait(timeout=2)
    writer.commit()
    contender.join(timeout=5)
    writer.close()
    monkeypatch.setattr(coord.store, "connect", original_connect)

    assert not contender.is_alive()
    assert results[0].status is RecoverStatus.LIVE_OWNER
    status = coord.project_status(did)
    assert status["revision"] == 2
    assert status["state"] == OperationState.BLOCKED.value
    assert status["lease_current"] is True
    conn = coord.store.connect()
    try:
        persisted = coord.store.get_operation(conn, did)
    finally:
        conn.close()
    assert persisted is not None
    assert persisted.revision == 2
    assert persisted.lease_owner == "new-live-owner"
    assert persisted.lease_pid == os.getpid()


# --- PRD-CORE-215 NFR01: at-most-once effect ---


def test_prd_core_215_nfr01(tmp_path) -> None:
    """NFR01: after durable acceptance, concurrent + reconnect-style repeat retries
    with ONE request key produce at most one visible effect — one receipt, one
    operation row, and zero new effect calls on every retry.
    """
    from trw_mcp.tools._delivery_models import ClaimStatus
    from trw_mcp.tools._operation_owner_adapter import DeliveryOperationOwner

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    cap = strong_capability()
    did = make_uuid7()

    owner = DeliveryOperationOwner(coordinator_factory=lambda: make_coordinator(trw_dir))

    # Durable acceptance: the first claim commits the single effect.
    first = owner.resolve_claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
    assert first.status is ClaimStatus.CLAIMED

    # Concurrent + repeat retries with the SAME key (mimics reconnect storms).
    results: list = []
    lock = threading.Lock()

    def _retry() -> None:
        r = owner.resolve_claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_retry) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert all(not t.is_alive() for t in threads)  # no deadlock

    # Every retry followed the ORIGINAL operation with ZERO new effect.
    assert len(results) == 8
    assert all(r.status is ClaimStatus.EXISTING for r in results)
    assert all(r.operation_id == first.operation_id for r in results)
    assert all(r.effect_calls == 0 for r in results)

    # Exactly ONE operation row exists across the whole retry matrix.
    coord = make_coordinator(trw_dir)
    conn = coord.store.connect()
    ops = coord.store.iter_operations(conn)
    conn.close()
    assert len(ops) == 1
    assert ops[0].operation_id == did


# --- PRD-CORE-215 FR03: request identity delegated to operation owners ---


class _FakeLearningOwner:
    """A non-delivery owner that dedupes in its OWN store (never the journal)."""

    from trw_mcp.models.tool_result import CeremonyExecutionClass as _EC

    execution_class = _EC.SYNCHRONOUS_ONLY
    owner_id = "fake-learning-store"

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def resolve(self, request_id: str, input_digest: str) -> str:
        if request_id in self._store:
            return "existing" if self._store[request_id] == input_digest else "collision"
        self._store[request_id] = input_digest
        return "new"


def _all_sqlite_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.sqlite3") if p.is_file()]


def test_prd_core_215_fr03(tmp_path) -> None:
    """FR03: identical request IDs dedupe via the CORE-208 journal; conflicting IDs
    return a typed collision; a non-delivery fixture uses its own store; no second
    delivery journal is created; an unowned operation-backed claim is refused.
    """
    from trw_mcp.tools import _operation_owner_adapter as owner_mod
    from trw_mcp.tools._delivery_models import ClaimStatus
    from trw_mcp.tools._operation_owner_adapter import (
        DeliveryOperationOwner,
        UnownedOperationError,
        register_owner,
        require_operation_backed,
        reset_registry,
        validate_operation_backed_claim,
    )

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    cap = strong_capability()

    try:
        delivery_owner = DeliveryOperationOwner(coordinator_factory=lambda: make_coordinator(trw_dir))

        # Identical request IDs (same delivery_id + same bound fields) dedupe via
        # the CORE-208 journal — the second resolve follows the original operation.
        did = make_uuid7()
        first = delivery_owner.resolve_claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
        second = delivery_owner.resolve_claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
        assert first.status is ClaimStatus.CLAIMED
        assert second.status is ClaimStatus.EXISTING
        assert second.operation_id == first.operation_id
        assert second.effect_calls == 0

        # Conflicting request ID (same id, different input digest) is a typed collision.
        conflict = delivery_owner.resolve_claim(delivery_id=did, capability_token=cap, run_identity="task/run-2")
        assert conflict.status is ClaimStatus.CONFLICT
        assert conflict.effect_calls == 0

        # Exactly one operation row exists and the ONLY sqlite journal under .trw is
        # the CORE-208 operations journal — no second generic delivery journal.
        coord = make_coordinator(trw_dir)
        conn = coord.store.connect()
        ops = coord.store.iter_operations(conn)
        conn.close()
        assert len(ops) == 1
        journals = _all_sqlite_files(trw_dir)
        assert journals, "the CORE-208 journal must exist"
        assert all(p.name == "operations.sqlite3" for p in journals)
        assert {p.parent.name for p in journals} == {"delivery"}

        # A non-delivery fixture uses its OWN store and never touches the journal.
        learn_dir = tmp_path / "learn_project" / ".trw"
        learn_dir.mkdir(parents=True)
        fake = _FakeLearningOwner()
        register_owner("trw_learn_fixture", fake)
        assert owner_mod.get_owner("trw_learn_fixture") is fake
        assert fake.resolve("req-1", "digestA") == "new"
        assert fake.resolve("req-1", "digestA") == "existing"  # dedupe in own store
        assert fake.resolve("req-1", "digestB") == "collision"  # conflicting digest
        assert not (learn_dir / "delivery").exists()  # no delivery journal created
        assert _all_sqlite_files(learn_dir) == []

        # A tool with no registered owner cannot claim operation-backed behavior.
        with pytest.raises(UnownedOperationError):
            require_operation_backed("trw_unregistered")
        assert validate_operation_backed_claim("trw_unregistered", {"operation_backed": True}) == "unowned_claim"
        # And the registered delivery owner validates as legitimate.
        assert (
            validate_operation_backed_claim("trw_delivery_status", {"operation_id": did, "accepted": True}) == "valid"
        )
    finally:
        reset_registry()


def test_prd_core_215_fr03_middleware_consumes_owner_registry() -> None:
    """FR03 production wiring: the ceremony middleware validates operation-backed
    claims against the owner registry and stamps the verdict onto the result.
    """
    from trw_mcp.middleware.ceremony import _annotate_operation_backed_claim

    class _Result:
        structured_content: dict[str, object]

    # An unowned tool returning a handle is flagged, never silently trusted.
    unowned = _Result()
    unowned.structured_content = {"operation_id": "op-x", "accepted": True}
    _annotate_operation_backed_claim("trw_unregistered_tool", unowned)
    assert unowned.structured_content["operation_backed_claim"] == "unowned_claim"

    # The registered delivery owner validates as legitimate.
    owned = _Result()
    owned.structured_content = {"operation_id": "op-x", "accepted": True}
    _annotate_operation_backed_claim("trw_delivery_status", owned)
    assert owned.structured_content["operation_backed_claim"] == "valid"

    # A non-claim result is left untouched (no false annotation).
    plain = _Result()
    plain.structured_content = {"result": "ok"}
    _annotate_operation_backed_claim("trw_status", plain)
    assert "operation_backed_claim" not in plain.structured_content
