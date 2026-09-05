"""PRD-FIX-127 FR01/FR02/FR04 + NFR02/NFR03: a delivery crash you can finish.

Every arm runs against the REAL SQLite journal store, and the FR02 arm SIGKILLs a
real spawned process running a real ``trw_deliver`` — the FPI-1 pattern from
``test_delivery_operation_crash_matrix.py``, extended past the crash to the
resume. Before this PRD there was no code path at all to test here: ``resume`` did
not exist, ``begin_step``/``finalize_step`` had exactly two call sites (both inside
the forward journal handle), and every repeat claim on a non-terminal operation was
converted to a zero-effect refusal.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from tests._delivery_support import (
    age_lease,
    deliver_patches,
    env_pid_dead,
    make_coordinator,
    make_uuid7,
    operation_row,
    recovery_events,
    seed_deliver_run,
    steps_by_id,
    strong_capability,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._delivery_effect_registry import DELIVERY_EFFECT_REGISTRY
from trw_mcp.tools._delivery_models import RecoverStatus, RecoveryAction, StepState

_JOURNAL_OWNER = "trw_deliver"


# --- FR01 -------------------------------------------------------------------


def test_resume_classifies_then_grants_a_fresh_lease(tmp_path: Path) -> None:
    """FR01: an indeterminate step blocks resume until the operator reconciles it.

    ``D16`` is the NON_REPLAYABLE trust increment. Left ``started`` by a crash it
    cannot be replayed and cannot be assumed applied, so resume must refuse — and
    it must refuse with a reason code that names the action that unblocks it.
    """
    coord = make_coordinator(tmp_path, stale_lease_minutes=15)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, owner="crashed", pid=env_pid_dead())
    coord.begin_step(did, "D16", owner="crashed", pid=env_pid_dead())
    rev = age_lease(coord, did)

    blocked = coord.resume(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="operator resumes a delivery killed mid-batch",
        new_owner=_JOURNAL_OWNER,
        new_pid=os.getpid(),
    )
    assert blocked.status is RecoverStatus.REJECTED
    assert blocked.reason_code == "reconciliation_required"
    assert blocked.indeterminate_effect_ids == ("D16",)
    assert steps_by_id(coord, did)["D16"].state is StepState.INDETERMINATE
    assert not [e for e in recovery_events(coord, did) if e.action is RecoveryAction.RESUME]

    # The operator settles D16 through the EXISTING reconciliation action.
    reconciled = coord.reconcile_effect(
        operation_id=did,
        effect_id="D16",
        applied=True,
        capability_token=cap,
        expected_revision=operation_row(coord, did).revision,
        reason="operator verified the atomic trust ledger",
        evidence_ref="audit/trust-ledger#row-1",
    )
    assert reconciled.status is RecoverStatus.OK
    rev = age_lease(coord, did)

    granted = coord.resume(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="operator resumes a delivery killed mid-batch",
        new_owner=_JOURNAL_OWNER,
        new_pid=os.getpid(),
    )
    assert granted.status is RecoverStatus.OK
    assert granted.lease_owner == _JOURNAL_OWNER
    op = operation_row(coord, did)
    assert op is not None and op.lease_owner == _JOURNAL_OWNER and op.lease_pid == os.getpid()
    resume_rows = [e for e in recovery_events(coord, did) if e.action is RecoveryAction.RESUME]
    assert len(resume_rows) == 1


def test_resume_refuses_a_terminaloperation_row(tmp_path: Path) -> None:
    """FR01 step 2: a succeeded/failed/cancelled operation is never resumable."""
    from trw_mcp.tools._delivery_models import OperationState

    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, owner="w", pid=env_pid_dead())
    coord.mark_operation_state(did, OperationState.SUCCEEDED)
    result = coord.resume(
        operation_id=did,
        capability_token=cap,
        expected_revision=operation_row(coord, did).revision,
        reason="try to resume a finished delivery",
    )
    assert result.status is RecoverStatus.REJECTED
    assert result.reason_code == "operation_terminal"


# --- NFR02 ------------------------------------------------------------------


def test_resume_guards_all_fail_closed(tmp_path: Path) -> None:
    """NFR02: four refusal inputs, each leaving the lease owner and steps untouched."""
    coord = make_coordinator(tmp_path, stale_lease_minutes=15)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, owner="orig", pid=env_pid_dead())
    fresh_rev = operation_row(coord, did).revision

    # (c) a still-fresh lease refuses BEFORE the lease is aged.
    still_fresh = coord.resume(
        operation_id=did, capability_token=cap, expected_revision=fresh_rev, reason="premature resume"
    )
    assert still_fresh.status is RecoverStatus.NOT_STALE

    rev = age_lease(coord, did)
    arms = {
        "wrong capability": coord.resume(
            operation_id=did, capability_token=strong_capability(), expected_revision=rev, reason="r"
        ),
        "stale revision": coord.resume(operation_id=did, capability_token=cap, expected_revision=rev + 99, reason="r"),
        "live owner": coord.resume(
            operation_id=did, capability_token=cap, expected_revision=rev, reason="r", owner_alive=True
        ),
    }
    assert arms["wrong capability"].status is RecoverStatus.UNAUTHORIZED
    assert arms["stale revision"].status is RecoverStatus.STALE_REVISION
    assert arms["live owner"].status is RecoverStatus.LIVE_OWNER

    op = operation_row(coord, did)
    assert op is not None and op.lease_owner == "orig"
    assert all(step.state is StepState.NOT_STARTED for step in steps_by_id(coord, did).values())
    assert not [e for e in recovery_events(coord, did) if e.action is RecoveryAction.RESUME]


# --- NFR03 ------------------------------------------------------------------


def test_resume_is_bounded_and_effect_free(tmp_path: Path) -> None:
    """NFR03: O(census) inside the busy-timeout budget, with zero product effects."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    registry = trw_dir / "trust-registry.yaml"
    registry.write_text("project:\n  session_count: 1\n", encoding="utf-8")
    events = trw_dir / "context" / "session-events.jsonl"
    events.write_text('{"event": "seed"}\n', encoding="utf-8")

    coord = make_coordinator(trw_dir, stale_lease_minutes=15)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, owner="crashed", pid=env_pid_dead())
    # Carry the FULL step census so the transaction is measured at its real width.
    for effect_id in sorted(DELIVERY_EFFECT_REGISTRY):
        coord.begin_step(did, effect_id, owner="crashed", pid=env_pid_dead())
        coord.finalize_step(did, effect_id, state=StepState.SUCCEEDED)
    rev = age_lease(coord, did)

    before = (registry.read_bytes(), events.read_bytes())
    started = time.perf_counter()
    result = coord.resume(
        operation_id=did, capability_token=cap, expected_revision=rev, reason="bounded resume", new_pid=os.getpid()
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert result.status is RecoverStatus.OK
    assert elapsed_ms < TRWConfig().delivery_busy_timeout_ms, f"resume took {elapsed_ms:.1f}ms"
    # Zero product effects: only step rows, the operation row, and one audit row.
    assert (registry.read_bytes(), events.read_bytes()) == before


# --- FR02: real SIGKILL, real resume ----------------------------------------


def _child_delivery_killed_between_s05_and_s08(tmp_path_str: str, delivery_id: str, cap: str, ready: str) -> None:
    """Run a REAL deliver and hang after S05 finalizes, before S08 begins.

    The parent SIGKILLs it there, which is the state PRD-FIX-127 FR02 is written
    against: S05 durably ``succeeded``, S08 still ``not_started``, nothing
    indeterminate.
    """
    import contextlib
    from unittest.mock import patch

    tmp_path = Path(tmp_path_str)
    os.environ["TRW_PROJECT_ROOT"] = str(tmp_path)
    os.environ["TRW_OFFLINE"] = "1"

    from tests.conftest import get_tools_sync, make_test_server

    tools = get_tools_sync(make_test_server("ceremony", "checkpoint", "review"))

    def _hang(*_args: object, **_kwargs: object) -> bool:
        Path(ready).write_text("ready")
        while True:
            time.sleep(0.05)

    with contextlib.ExitStack() as stack:
        for patcher in deliver_patches(tmp_path):
            stack.enter_context(patcher)
        stack.enter_context(patch("trw_mcp.tools._deliver_gate_dispatch.evaluate_delivery_gates", _hang))
        tools["trw_deliver"].fn(
            allow_unverified=True,
            unverified_reason="test fixture: no build_check recorded for this synthetic run",
            delivery_id=delivery_id,
            capability_token=cap,
        )


def resumed_deliver(tmp_path: Path, delivery_id: str, cap: str) -> dict:
    """Re-invoke trw_deliver under the SAME id, in this process, with real gates."""
    import contextlib

    from tests.conftest import get_tools_sync, make_test_server

    tools = get_tools_sync(make_test_server("ceremony", "checkpoint", "review"))
    with contextlib.ExitStack() as stack:
        for patcher in deliver_patches(tmp_path):
            stack.enter_context(patcher)
        result = tools["trw_deliver"].fn(
            allow_unverified=True,
            unverified_reason="test fixture: no build_check recorded for this synthetic run",
            delivery_id=delivery_id,
            capability_token=cap,
        )
    from trw_mcp.tools import _deferred_state as _ds

    thread = _ds._deferred_thread
    if thread is not None:
        thread.join(timeout=90)
    return result


@pytest.mark.integration
def test_sigkilled_delivery_resumes_with_zero_duplicated_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR02: finish a SIGKILLed delivery under the SAME id, re-running nothing."""
    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(repo_root)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (repo_root, os.environ.get("PYTHONPATH", "")))))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_OFFLINE", "1")

    run_dir = seed_deliver_run(tmp_path)
    trw_dir = tmp_path / ".trw"
    did = make_uuid7()
    cap = strong_capability()
    ready = tmp_path / "ready.marker"

    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_child_delivery_killed_between_s05_and_s08, args=(str(tmp_path), did, cap, str(ready)))
    proc.start()
    for _ in range(600):  # up to 60s for a cold spawned interpreter + real deliver
        if ready.exists():
            break
        time.sleep(0.1)
    assert ready.exists(), "child never reached the mid-journal state"
    proc.kill()  # real SIGKILL between S05 and S08
    proc.join(timeout=30)
    assert not proc.is_alive()

    coord = make_coordinator(trw_dir, stale_lease_minutes=15)
    crashed_steps = steps_by_id(coord, did)
    assert crashed_steps["S05"].state is StepState.SUCCEEDED
    assert "S08" not in crashed_steps or crashed_steps["S08"].state is StepState.NOT_STARTED
    pre_crash_succeeded = {eid for eid, s in crashed_steps.items() if s.state is StepState.SUCCEEDED}
    assert "S05" in pre_crash_succeeded

    # Before FR01 this was the end of the road: the only way forward was a NEW
    # delivery id, which re-runs every effect from the top.
    rev = age_lease(coord, did)
    granted = coord.resume(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="operator resumes the SIGKILLed delivery",
        new_owner=_JOURNAL_OWNER,
        new_pid=os.getpid(),
    )
    assert granted.status is RecoverStatus.OK, granted.reason_code

    result = resumed_deliver(tmp_path, did, cap)
    assert result.get("delivery_operation", {}).get("resume_mode") is True

    op = operation_row(coord, did)
    assert op is not None
    assert op.state.value == "succeeded", f"resumed operation ended {op.state.value}"

    after = steps_by_id(coord, did)
    for effect_id in sorted(pre_crash_succeeded):
        assert after[effect_id].attempt == 1, f"{effect_id} was re-executed (attempt {after[effect_id].attempt})"
        assert after[effect_id].state is StepState.SUCCEEDED
    # The two the resumed journal actually reached are recorded as skipped work.
    # S02 is nested inside S01, so skipping S01 means S02 is never re-entered at
    # all — its row keeps the disposition its one successful run gave it.
    for effect_id in ("S01", "S05"):
        assert after[effect_id].disposition.value == "skipped_no_work"

    events = (run_dir / "meta" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    completions = [line for line in events if json.loads(line).get("event") == "trw_deliver_complete"]
    assert len(completions) == 1


# --- FR04 -------------------------------------------------------------------


@pytest.mark.integration
def test_evidence_and_audit_writes_precede_the_terminal_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: no step finalizes after the operation's terminal timestamp."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_OFFLINE", "1")
    run_dir = seed_deliver_run(tmp_path)
    did = make_uuid7()

    resumed_deliver(tmp_path, did, strong_capability())

    coord = make_coordinator(tmp_path / ".trw")
    op = operation_row(coord, did)
    assert op is not None and op.terminal_utc_ms > 0
    steps = steps_by_id(coord, did)

    late = {eid: s.updated_utc_ms for eid, s in steps.items() if s.updated_utc_ms > op.terminal_utc_ms}
    assert late == {}, f"steps finalized after the terminal state: {late}"

    # D23/D24 are the two that used to run AFTER the terminal transition, where
    # begin_step refuses outright — so they could not be journaled at all.
    assert steps["D23"].state is StepState.SUCCEEDED
    assert steps["D24"].state is StepState.SUCCEEDED

    # D22's boundary now encloses the run-yaml write it is registered for, not the
    # pure-compute metrics step: it is succeeded exactly when the yaml carries the key.
    run_yaml = (run_dir / "meta" / "run.yaml").read_text(encoding="utf-8")
    assert (steps["D22"].state is StepState.SUCCEEDED) == ("session_metrics" in run_yaml)
