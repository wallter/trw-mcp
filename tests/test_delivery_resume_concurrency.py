"""PRD-FIX-127 NFR01: concurrent ``resume`` on one delivery operation.

Split out of ``test_delivery_operations_concurrency.py`` (which was already at 324
effective LOC) so both files stay under the 350 effective-LOC gate NFR05 requires.
The property is the PRD-CORE-208 NFR03 convergence property applied to the new
FR01 action: each MCP client spawns its own stdio server against the same
project-local store, so two operators can call resume on one crashed delivery at
the same moment.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

from tests._delivery_support import env_pid_dead, make_coordinator, make_uuid7, strong_capability
from trw_mcp.tools._delivery_models import RecoverStatus, RecoveryAction, StepState

_TWO_HOURS_MS = 2 * 60 * 60 * 1000


def _resume_worker(trw_dir_str: str, did: str, cap: str, revision: int, out) -> None:  # type: ignore[no-untyped-def]
    from tests._delivery_support import make_coordinator as _mk

    coord = _mk(Path(trw_dir_str), stale_lease_minutes=15)
    result = coord.resume(
        operation_id=did,
        capability_token=cap,
        expected_revision=revision,
        reason="concurrent operator resume",
        new_owner="trw_deliver",
    )
    out.put(result.status.value)


def test_concurrent_resume_grants_exactly_one_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-FIX-127 NFR01: eight processes resume one operation; exactly one wins.

    Each MCP client spawns its own stdio server against the same project-local
    store, so two operators can call resume on the same crashed delivery at the
    same moment. The whole action runs in ONE BEGIN IMMEDIATE transaction, so the
    losers must see a typed refusal rather than a second lease or a deadlock.
    """
    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(repo_root)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (repo_root, existing_pythonpath))))

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    did = make_uuid7()
    cap = strong_capability()
    coord = make_coordinator(trw_dir, stale_lease_minutes=15)
    coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1", owner="crashed", pid=env_pid_dead())
    coord.begin_step(did, "S05", owner="crashed", pid=env_pid_dead())
    coord.finalize_step(did, "S05", state=StepState.SUCCEEDED)

    # The crashed owner's lease has aged out; every process sees the same snapshot.
    conn = coord.store.connect()
    with coord.store.immediate(conn):
        op = coord.store.get_operation(conn, did)
        assert op is not None
        coord.store.replace_operation(
            conn, op.model_copy(update={"lease_expiry_utc_ms": coord._now_ms() - _TWO_HOURS_MS})
        )
    conn.close()
    revision = op.revision

    ctx = mp.get_context("spawn")
    out: mp.Queue = ctx.Queue()
    procs = [ctx.Process(target=_resume_worker, args=(str(trw_dir), did, cap, revision, out)) for _ in range(8)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=30)
        assert not proc.is_alive()  # no deadlock

    statuses = [out.get() for _ in range(8)]
    assert statuses.count(RecoverStatus.OK.value) == 1, statuses
    losers = {
        RecoverStatus.STALE_REVISION.value,
        RecoverStatus.NOT_STALE.value,
        RecoverStatus.LIVE_OWNER.value,
    }
    assert all(status in losers for status in statuses if status != RecoverStatus.OK.value), statuses

    conn = coord.store.connect()
    ops = coord.store.iter_operations(conn)
    grants = [e for e in coord.store.get_recovery_events(conn, did) if e.action is RecoveryAction.RESUME]
    conn.close()
    assert len(ops) == 1
    assert len(grants) == 1  # exactly one lease was committed, not eight
