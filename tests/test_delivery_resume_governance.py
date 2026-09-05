"""PRD-FIX-127 review follow-ups: a resumed delivery may not inherit a verdict.

Split from ``test_delivery_resume.py`` to keep both under the 350 effective-LOC
gate. Each arm closes an item an independent review raised against the first cut
of the resume path — the critical one being that ``apply_structured_override``
reports refusal by RETURN VALUE, so a boundary that only watches for exceptions
finalized ``succeeded`` on a REJECTED acceptable-failure record, and a resume that
then skipped it let the second attempt proceed on a verdict nobody re-validated
and nothing ledgered.
"""

from __future__ import annotations

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
from tests.test_delivery_resume import resumed_deliver
from trw_mcp.tools._delivery_models import RecoverStatus, RecoveryAction, StepState

_JOURNAL_OWNER = "trw_deliver"

# --- Review finding 1: a decision-shaped effect must not be inheritable --------


def _blocked_deliver(tmp_path: Path, delivery_id: str, cap: str, unverified_reason: str) -> dict:
    """Drive a real deliver whose only override attempt is the given reason."""
    import contextlib

    from tests.conftest import get_tools_sync, make_test_server

    tools = get_tools_sync(make_test_server("ceremony", "checkpoint", "review"))
    with contextlib.ExitStack() as stack:
        for patcher in deliver_patches(tmp_path):
            stack.enter_context(patcher)
        result = tools["trw_deliver"].fn(
            allow_unverified=True,
            unverified_reason=unverified_reason,
            delivery_id=delivery_id,
            capability_token=cap,
        )
    from trw_mcp.tools import _deferred_state as _ds

    thread = _ds._deferred_thread
    if thread is not None:
        thread.join(timeout=90)
    return result


@pytest.mark.integration
def test_a_refused_override_is_never_inherited_by_a_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected acceptable-failure record blocks again on the resumed attempt.

    ``apply_structured_override`` reports refusal by RETURN VALUE and never raises,
    and on the prose path it never reaches ``write_override_ledger`` at all. A
    boundary that finalized S06 ``succeeded`` on that path would record a ledger
    write that did not happen — and a resume that then SKIPPED S06 would let the
    second attempt proceed on a verdict nobody re-validated and nothing ledgered.
    That is a bypass of PRD-CORE-191's structured-override design.
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_OFFLINE", "1")
    run_dir = seed_deliver_run(tmp_path)
    # deliver_gate_mode=block_coding hard-blocks a build-bearing task type with no
    # build evidence, which is the STRUCTURED gate the override is evaluated for.
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: 20260214T000000Z-test\nstatus: active\nphase: deliver\nprd_scope: []\ntask_type: coding\n",
        encoding="utf-8",
    )
    (run_dir / "meta" / "events.jsonl").write_text(
        '{"event": "run_init", "task": "t"}\n{"event": "file_modified", "file": "x0.py"}\n', encoding="utf-8"
    )
    trw_dir = tmp_path / ".trw"
    did = make_uuid7()
    cap = strong_capability()
    prose = "we are pretty sure the build is fine, shipping it"

    first = _blocked_deliver(tmp_path, did, cap, prose)
    assert first["success"] is False
    assert first.get("acceptable_failure_error")

    coord = make_coordinator(trw_dir, stale_lease_minutes=15)
    steps = steps_by_id(coord, did)
    # The step encodes the BUSINESS outcome, not "did the call raise".
    assert steps["S06"].state is StepState.FAILED
    assert steps["S06"].finding_code == "override_refused"
    assert not list((trw_dir / "overrides").glob("*")) if (trw_dir / "overrides").exists() else True

    op = operation_row(coord, did)
    assert op is not None and op.state.value == "blocked"  # non-terminal: resumable

    rev = age_lease(coord, did)
    granted = coord.resume(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="operator retries the blocked delivery",
        new_owner=_JOURNAL_OWNER,
        new_pid=os.getpid(),
    )
    assert granted.status is RecoverStatus.OK, granted.reason_code

    # Same prose on the resumed attempt: the gate MUST re-validate and block again.
    second = _blocked_deliver(tmp_path, did, cap, prose)
    assert second["success"] is False, "a refused override was inherited across a resume"
    assert second.get("acceptable_failure_error")
    assert steps_by_id(coord, did)["S06"].state is StepState.FAILED
    assert not (trw_dir / "overrides").exists() or not list((trw_dir / "overrides").rglob("*.yaml"))


def test_decision_effects_are_never_skipped_on_resume(tmp_path: Path) -> None:
    """The never-skip set is enumerated in the registry, not inferred at the seam."""
    from trw_mcp.tools._delivery_effect_registry import ALWAYS_REEVALUATE_EFFECTS, get_descriptor
    from trw_mcp.tools._delivery_journal_wiring import DeliverJournal
    from trw_mcp.tools._delivery_models import StepRecord

    assert ALWAYS_REEVALUATE_EFFECTS == {"S06", "S07", "S23"}

    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w", pid=os.getpid())
    prior = {
        eid: StepRecord(
            effect_id=eid,
            state=StepState.SUCCEEDED,
            replay_class=get_descriptor(eid).replay_class,
            attempt=1,
        )
        for eid in ("S05", "S06", "S07", "S23")
    }
    journal = DeliverJournal(coordinator=coord, operation_id=did, mode="enforce", resume_steps=prior)

    with journal.step("S05") as run_ordinary:
        assert run_ordinary is False  # an ordinary succeeded step is skipped
    for decision_effect in sorted(ALWAYS_REEVALUATE_EFFECTS):
        with journal.step(decision_effect) as run_decision:
            assert run_decision is True, f"{decision_effect} was inherited across a resume"


# --- Review finding 3: an unsettled step blocks the grant ---------------------


def test_resume_refuses_while_any_step_is_still_started(tmp_path: Path) -> None:
    """A ``started`` keyed_idempotent step is unsettled, not replay-safe.

    ``apply_crash_recovery_locked`` calls the non-NON_REPLAYABLE classes "replay
    safe" on the strength of their registered idempotency keys — but those keys are
    not wired at the call sites (OQ-004): ``_log_deliver_event`` appends S20 with no
    effect id at all. Re-running such a step would genuinely duplicate, so resume
    refuses until an operator settles it. This is what makes FR02's
    zero-duplicated-effects claim true rather than aspirational.
    """
    coord = make_coordinator(tmp_path, stale_lease_minutes=15)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, owner="crashed", pid=env_pid_dead())
    coord.begin_step(did, "S20", owner="crashed", pid=env_pid_dead())  # keyed_idempotent, required
    rev = age_lease(coord, did)

    blocked = coord.resume(
        operation_id=did, capability_token=cap, expected_revision=rev, reason="resume after a mid-effect crash"
    )
    assert blocked.status is RecoverStatus.REJECTED
    assert blocked.reason_code == "reconciliation_required"
    assert "S20" in blocked.indeterminate_effect_ids
    assert not [e for e in recovery_events(coord, did) if e.action is RecoveryAction.RESUME]

    # Settled explicitly, the grant follows.
    coord.reconcile_effect(
        operation_id=did,
        effect_id="S20",
        applied=True,
        capability_token=cap,
        expected_revision=operation_row(coord, did).revision,
        reason="operator confirmed the completion event landed once",
        evidence_ref="meta/events.jsonl#trw_deliver_complete",
    )
    rev = age_lease(coord, did)
    granted = coord.resume(
        operation_id=did, capability_token=cap, expected_revision=rev, reason="resume after reconciliation"
    )
    assert granted.status is RecoverStatus.OK


# --- Review finding 4: the forward-only guard and the S02 mirror --------------


def _child_delivery_killed_between_phase_write_and_mirror(
    tmp_path_str: str, delivery_id: str, cap: str, ready: str
) -> None:
    """Hang inside update_run_phase, after the run.yaml write and before S02."""
    import contextlib
    from unittest.mock import patch

    tmp_path = Path(tmp_path_str)
    os.environ["TRW_PROJECT_ROOT"] = str(tmp_path)
    os.environ["TRW_OFFLINE"] = "1"

    from tests.conftest import get_tools_sync, make_test_server

    tools = get_tools_sync(make_test_server("ceremony", "checkpoint", "review"))

    def _hang(*_args: object, **_kwargs: object) -> None:
        Path(ready).write_text("ready")
        while True:
            time.sleep(0.05)

    with contextlib.ExitStack() as stack:
        for patcher in deliver_patches(tmp_path):
            stack.enter_context(patcher)
        stack.enter_context(patch("trw_mcp.state.phase._sync_ceremony_phase", _hang))
        tools["trw_deliver"].fn(
            allow_unverified=True,
            unverified_reason="test fixture: no build_check recorded for this synthetic run",
            delivery_id=delivery_id,
            capability_token=cap,
        )


@pytest.mark.integration
def test_s02_mirror_completes_on_resume_despite_the_forward_only_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between the run.yaml phase write and the S02 mirror is repairable.

    ``update_run_phase`` is forward-only, so the second attempt sees the phase
    already at ``deliver`` and used to return BEFORE the mirror — leaving a
    ``required`` effect permanently unreachable and the mirror permanently
    disagreeing with the run. The mirror now converges on the already-at-target
    path too, inside its own boundary.
    """
    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(repo_root)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (repo_root, os.environ.get("PYTHONPATH", "")))))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_OFFLINE", "1")

    run_dir = seed_deliver_run(tmp_path)
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: test\nstatus: active\nphase: review\nprd_scope: []\n", encoding="utf-8"
    )
    trw_dir = tmp_path / ".trw"
    did = make_uuid7()
    cap = strong_capability()
    ready = tmp_path / "phase.marker"

    ctx = mp.get_context("spawn")
    proc = ctx.Process(
        target=_child_delivery_killed_between_phase_write_and_mirror, args=(str(tmp_path), did, cap, str(ready))
    )
    proc.start()
    for _ in range(600):
        if ready.exists():
            break
        time.sleep(0.1)
    assert ready.exists(), "child never reached the phase-mirror state"
    proc.kill()
    proc.join(timeout=30)
    assert not proc.is_alive()

    coord = make_coordinator(trw_dir, stale_lease_minutes=15)
    crashed = steps_by_id(coord, did)
    assert crashed["S01"].state is StepState.STARTED  # the enclosing boundary is open
    assert "S02" not in crashed or crashed["S02"].state is not StepState.SUCCEEDED
    assert "phase: deliver" in (run_dir / "meta" / "run.yaml").read_text(encoding="utf-8")

    # An unsettled started step blocks the grant until the operator settles it.
    rev = age_lease(coord, did)
    refused = coord.resume(
        operation_id=did, capability_token=cap, expected_revision=rev, reason="resume the killed phase write"
    )
    assert refused.status is RecoverStatus.REJECTED
    assert "S01" in refused.indeterminate_effect_ids

    # The crash was INSIDE the S02 boundary, which is nested in S01's, so both are
    # left started and both must be settled before a grant.
    # `not_applied` is the truthful verdict for BOTH: the run.yaml value landed but
    # neither enclosing step completed, and `update_run_phase` is forward-only and
    # therefore safe to re-enter. That is exactly the `confirmed_not_applied` ->
    # new attempt contract PRD-CORE-208 FR04 documents.
    for effect_id in sorted(refused.indeterminate_effect_ids):
        coord.reconcile_effect(
            operation_id=did,
            effect_id=effect_id,
            applied=False,
            capability_token=cap,
            expected_revision=operation_row(coord, did).revision,
            reason="the phase value landed but neither step completed; re-run is idempotent",
            evidence_ref="meta/run.yaml#phase",
        )
    rev = age_lease(coord, did)
    granted = coord.resume(
        operation_id=did,
        capability_token=cap,
        expected_revision=rev,
        reason="resume after reconciling the phase write",
        new_owner=_JOURNAL_OWNER,
        new_pid=os.getpid(),
    )
    assert granted.status is RecoverStatus.OK, granted.reason_code

    resumed_deliver(tmp_path, did, cap)
    after = steps_by_id(coord, did)
    assert after["S02"].state is StepState.SUCCEEDED, "the required S02 mirror never completed"
