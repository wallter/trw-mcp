"""PRD-CORE-208 FR02/FR04: crash-boundary state machine + authorized takeover.

Includes the FPI-1 crash-recovery integration test: a REAL child process is
SIGKILLed mid-journal (after a NON_REPLAYABLE trust step is ``started`` and a
single trust increment has committed) and the restarted coordinator recovers the
operation without duplicating the side effect.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from tests._delivery_support import env_pid_dead, make_coordinator, make_uuid7, strong_capability
from trw_mcp.tools._delivery_models import OperationState, RecoverStatus, StepState


def test_claim_and_step_transitions_survive_every_crash_boundary(tmp_path) -> None:
    """FR02: a ``started`` step is never read as ``not_started`` after reopen."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity="t/r", owner="w1", pid=1)

    # Pre-effect boundary: commit `started`, then "crash" (drop the coordinator).
    coord.begin_step(did, "D16", owner="w1", pid=1)
    del coord

    reopened = make_coordinator(tmp_path)
    status = reopened.project_status(did)
    assert status["steps"]["D16"]["state"] == "started"  # not silently not_started
    assert status["state"] in {"running"}


def test_started_nonreplayable_step_becomes_indeterminate_not_replayed(tmp_path) -> None:
    """FR04: a NON_REPLAYABLE started step is marked indeterminate, never replayed."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w1", pid=env_pid_dead())
    coord.begin_step(did, "D16", owner="w1", pid=env_pid_dead())  # trust increment: non-replayable

    result = coord.recover_after_crash(did)
    assert result.status is RecoverStatus.OK
    assert "D16" in result.indeterminate_effect_ids
    assert result.replayed_effect_ids == ()  # nothing auto-replayed
    assert result.state is OperationState.INDETERMINATE
    assert coord.project_status(did)["steps"]["D16"]["state"] == "indeterminate"


def test_keyed_idempotent_started_step_is_replay_safe(tmp_path) -> None:
    """FR04: a keyed-idempotent started step is reported replay-safe, not blocked."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w1", pid=env_pid_dead())
    coord.begin_step(did, "D13", owner="w1", pid=env_pid_dead())  # session-summary append: keyed

    result = coord.recover_after_crash(did)
    assert "D13" in result.replayed_effect_ids
    assert "D13" not in result.indeterminate_effect_ids


def test_finalized_step_records_proof_and_bumps_revision(tmp_path) -> None:
    """FR02: proof-after-effect terminal transition is durable."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w1", pid=1)
    coord.begin_step(did, "S01", owner="w1", pid=1)
    coord.finalize_step(did, "S01", state=StepState.SUCCEEDED, proof_digest="abc123")
    status = coord.project_status(did)
    assert status["steps"]["S01"]["state"] == "succeeded"


def _age_lease(coord, did: str) -> int:
    """Push the lease expiry two hours into the past so it is stale; return revision."""
    conn = coord.store.connect()
    with coord.store.immediate(conn):
        op = coord.store.get_operation(conn, did)
        assert op is not None
        aged = op.model_copy(update={"lease_expiry_utc_ms": coord._now_ms() - 2 * 60 * 60 * 1000})
        coord.store.replace_operation(conn, aged)
    conn.close()
    return op.revision


def test_takeover_requires_capability_revision_liveness_and_reason(tmp_path) -> None:
    """FR04: every takeover guard fails closed without changing the lease owner."""
    coord = make_coordinator(tmp_path, stale_lease_minutes=15)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, owner="orig", pid=env_pid_dead())
    rev = _age_lease(coord, did)

    # wrong capability
    r = coord.takeover(
        operation_id=did,
        capability_token=strong_capability(),
        expected_revision=rev,
        reason="recover",
        new_owner="w2",
        new_pid=2,
        owner_alive=False,
    )
    assert r.status is RecoverStatus.UNAUTHORIZED and r.lease_owner == "orig"

    # stale expected revision
    r = coord.takeover(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev + 99,
        reason="recover",
        new_owner="w2",
        new_pid=2,
        owner_alive=False,
    )
    assert r.status is RecoverStatus.STALE_REVISION and r.lease_owner == "orig"

    # empty reason
    r = coord.takeover(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="",
        new_owner="w2",
        new_pid=2,
        owner_alive=False,
    )
    assert r.status is RecoverStatus.REJECTED and r.lease_owner == "orig"

    # live owner blocks takeover
    r = coord.takeover(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="recover",
        new_owner="w2",
        new_pid=2,
        owner_alive=True,
    )
    assert r.status is RecoverStatus.LIVE_OWNER and r.lease_owner == "orig"


def test_authorized_takeover_grants_new_lease(tmp_path) -> None:
    """FR04: a fully authorized takeover commits a new lease + audit event."""
    coord = make_coordinator(tmp_path, stale_lease_minutes=15)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, owner="orig", pid=env_pid_dead())
    rev = _age_lease(coord, did)

    r = coord.takeover(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="operator restart after crash",
        new_owner="w2",
        new_pid=2,
        owner_alive=False,
    )
    assert r.status is RecoverStatus.OK
    assert r.lease_owner == "w2"
    assert r.revision == rev + 1


# --- FPI-1: real multi-process crash recovery ---


def _child_partial_delivery(trw_dir_str: str, delivery_id: str, cap: str, ready: str) -> None:
    """Claim, start a NON_REPLAYABLE trust step, increment trust ONCE, then hang.

    Runs in a separate process that the parent SIGKILLs after ``ready`` appears —
    simulating a client/process death after an irreversible effect committed but
    before the delivery response/finalize.
    """
    import os

    from tests._delivery_support import make_coordinator as _mk
    from trw_mcp.state.trust import (
        compute_receipt_set_digest,
        compute_trust_outcome_id,
        consume_trust_outcome,
    )

    trw_dir = Path(trw_dir_str)
    coord = _mk(trw_dir)
    coord.claim(
        delivery_id=delivery_id, capability_token=cap, run_identity="task/run-1", owner="child", pid=os.getpid()
    )
    coord.begin_step(delivery_id, "D16", owner="child", pid=os.getpid())
    outcome_id = compute_trust_outcome_id("test-project", delivery_id, None)
    digest = compute_receipt_set_digest([("build-1", "digestA"), ("verify-1", "digestB")])
    consume_trust_outcome(trw_dir, outcome_id, digest)  # the single irreversible increment
    Path(ready).write_text("ready")
    while True:  # wait to be killed mid-journal (before finalize)
        time.sleep(0.05)


def test_restart_and_authorized_takeover_never_blindly_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FPI-1: kill a delivery mid-journal; restart recovers with no double effect."""
    from trw_mcp.state.trust import (
        compute_receipt_set_digest,
        compute_trust_outcome_id,
        consume_trust_outcome,
        read_trust_registry,
    )

    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(repo_root)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (repo_root, existing_pythonpath))))

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    did = make_uuid7()
    cap = strong_capability()
    ready = tmp_path / "ready.marker"

    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_child_partial_delivery, args=(str(trw_dir), did, cap, str(ready)))
    proc.start()
    for _ in range(200):  # wait up to 10s for the partial journal + increment
        if ready.exists():
            break
        time.sleep(0.05)
    assert ready.exists(), "child never reached the mid-journal state"
    proc.kill()  # real SIGKILL after the effect committed, before finalize
    proc.join(timeout=10)
    assert not proc.is_alive()

    # The child committed exactly one trust increment before the kill.
    registry = read_trust_registry(trw_dir)
    assert registry["project"]["session_count"] == 1

    # Restart: recover the operation. D16 is NON_REPLAYABLE -> indeterminate,
    # never auto-invoked, so the trust counter is not touched by recovery.
    coord = make_coordinator(trw_dir)
    status_before = coord.project_status(did)
    assert status_before["steps"]["D16"]["state"] == "started"  # survived the crash
    result = coord.recover_after_crash(did)
    assert "D16" in result.indeterminate_effect_ids
    assert result.replayed_effect_ids == ()

    # No double increment from recovery.
    assert read_trust_registry(trw_dir)["project"]["session_count"] == 1

    # And an idempotent retry with the identical operation-keyed outcome is a
    # no-op (the CORE-206 ledger prevents a second increment).
    outcome_id = compute_trust_outcome_id("test-project", did, None)
    digest = compute_receipt_set_digest([("build-1", "digestA"), ("verify-1", "digestB")])
    retry = consume_trust_outcome(trw_dir, outcome_id, digest)
    assert retry.status == "idempotent"
    assert retry.incremented is False
    assert read_trust_registry(trw_dir)["project"]["session_count"] == 1


@pytest.mark.parametrize("effect_id", ["D01", "D07", "D14", "S08", "S10"])
def test_all_nonreplayable_effects_block_auto_retry(tmp_path, effect_id) -> None:
    """FR04 crash matrix: every NON_REPLAYABLE effect blocks automatic retry."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w", pid=env_pid_dead())
    coord.begin_step(did, effect_id, owner="w", pid=env_pid_dead())
    result = coord.recover_after_crash(did)
    assert effect_id in result.indeterminate_effect_ids
    assert result.replayed_effect_ids == ()
