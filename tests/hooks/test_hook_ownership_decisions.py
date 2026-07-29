"""Ledger UF-047 — six hooks stop resolving "the active run" by recency.

The dangerous half of this migration is not the positive case. Several of these
hooks already ``exit 0`` when no run resolves, so swapping in ``resolve_owned_run``
naively converts a false POSITIVE (nagging about a foreign run) into a false
NEGATIVE (silently never enforcing). A gate that never fires is worse than one
that fires wrongly, because nobody notices.

So every hook is asserted in BOTH directions, and each hook's "I own no run"
behaviour is its OWN decision, spelled out in the test that pins it:

===================== ============================================================
hook                  what unowned means, and why
===================== ============================================================
completion-gate.sh    "no run-scoped evidence" -> the checkpoint gate STILL blocks.
                      It must not clear itself with a stranger's checkpoint.
helper-idle.sh        same: the idle nudge STILL fires, capped as before.
phase-cycle-stop.sh   exit 0 without enforcing. The phase cycle IS the run; a
                      session that never ran trw_init has no criteria to fail.
pre-compact.sh        STILL writes the snapshot, with run fields empty. Recovery
                      after compaction must be armed, just not with foreign state.
session-end.sh        STILL does housekeeping; skips only the run-scoped warning,
                      whose event count is unverifiable without an owned run.
subagent-start.sh     STILL injects the protocol reminders; omits only the
                      run/phase lines and the phase-selected checklist.
===================== ============================================================

Every fixture carries a foreign run that is newer AND richer than the owned one,
so each assertion is non-vacuous: the value recency would have produced is always
present and always different.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _ownership_harness import (
    BUILD_CHECK,
    BUNDLED_HOOKS,
    CHECKPOINT,
    DELIVER_COMPLETE,
    FILE_MODIFIED,
    FOREIGN_RUN_ID,
    MIRROR_HOOKS,
    OWN_RUN_ID,
    SESSION_ID,
    build_project,
    run_hook,
    write_hook_env,
)

_HOOK_COPIES = pytest.mark.parametrize(
    "hook_dir",
    [pytest.param(BUNDLED_HOOKS, id="bundled"), pytest.param(MIRROR_HOOKS, id="mirror")],
)


def _build_status(root: Path, *, tests_passed: bool) -> None:
    (root / ".trw" / "context").mkdir(parents=True, exist_ok=True)
    (root / ".trw" / "context" / "build-status.yaml").write_text(
        f"tests_passed: {'true' if tests_passed else 'false'}\nfailures:\n  - test_thing\n",
        encoding="utf-8",
    )


# =========================================================================== #
# completion-gate.sh
#   unowned == "no run-scoped evidence", NOT "skip the gate".
# =========================================================================== #
_HELPER_PAYLOAD = {"helper_name": "impl-1", "task_subject": "T1", "session_id": "unused"}


@_HOOK_COPIES
def test_completion_gate_blocks_when_only_a_FOREIGN_run_checkpointed(hook_dir: Path, tmp_path: Path) -> None:
    """The owned run has no checkpoint; the newest run does. The gate must block.

    Under recency this is the silent failure: the helper's checkpoint requirement
    is satisfied by a checkpoint another instance wrote.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(FILE_MODIFIED, CHECKPOINT),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=True)

    res = run_hook(hook_dir / "completion-gate.sh", root, payload=_HELPER_PAYLOAD)

    assert res.returncode == 2, f"gate cleared itself with a foreign checkpoint: {res.stdout}{res.stderr}"
    # Specifically the checkpoint-first message. Asserting only exit 2 would pass
    # on the pre-migration hook, which also exits 2 -- but from the NEXT gate,
    # having already accepted the foreign checkpoint.
    assert "Running trw_checkpoint preserves" in res.stderr, res.stderr
    assert "completion artifact" not in res.stderr


@_HOOK_COPIES
def test_completion_gate_accepts_the_OWNED_runs_checkpoint(hook_dir: Path, tmp_path: Path) -> None:
    """The other direction: the owned run's own checkpoint does clear the check.

    It then asks for the completion artifact -- i.e. the gate advanced a step,
    proving the checkpoint branch was actually taken and not skipped.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED, CHECKPOINT),
        foreign_event_lines=(FILE_MODIFIED,),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=True)

    res = run_hook(hook_dir / "completion-gate.sh", root, payload=_HELPER_PAYLOAD)

    assert res.returncode == 2
    assert "completion artifact" in res.stderr, res.stderr


@_HOOK_COPIES
def test_completion_gate_still_blocks_an_unowned_session(hook_dir: Path, tmp_path: Path) -> None:
    """UNOWNED direction: no run means no evidence, so the gate keeps enforcing.

    This is the false-negative guard. The foreign run holds a checkpoint that a
    recency resolver would have accepted.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(FILE_MODIFIED, CHECKPOINT),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=True)

    res = run_hook(hook_dir / "completion-gate.sh", root, payload=_HELPER_PAYLOAD)

    assert res.returncode == 2, "an unowned session silently escaped the checkpoint gate"
    assert "Running trw_checkpoint preserves" in res.stderr, res.stderr
    assert "completion artifact" not in res.stderr


@_HOOK_COPIES
def test_completion_gate_resolves_ownership_from_the_stdin_session_id(hook_dir: Path, tmp_path: Path) -> None:
    """Identity may arrive on stdin when the client exports no session variable.

    Guards the fallback path: a stale or profile-less hook-env.sh must not silently
    demote an identified session to the legacy newest-wins branch.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED, CHECKPOINT),
        foreign_event_lines=(FILE_MODIFIED,),
    )
    write_hook_env(root, client_id="copilot")
    _build_status(root, tests_passed=True)

    res = run_hook(
        hook_dir / "completion-gate.sh",
        root,
        payload={"helper_name": "impl-1", "task_subject": "T1", "session_id": SESSION_ID},
        identified=False,
    )

    assert res.returncode == 2
    assert "completion artifact" in res.stderr, res.stderr


@_HOOK_COPIES
def test_completion_gate_advisory_is_silent_for_an_unowned_session(hook_dir: Path, tmp_path: Path) -> None:
    """The non-helper advisory must not report a foreign run's ceremony state."""
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED, FILE_MODIFIED, FILE_MODIFIED),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=True)

    res = run_hook(hook_dir / "completion-gate.sh", root, payload={"task_subject": "T1"})

    assert res.returncode == 0
    assert "Ceremony pending" not in res.stderr, "advised on a foreign run's ceremony state"


@_HOOK_COPIES
def test_completion_gate_advisory_fires_for_the_owned_run(hook_dir: Path, tmp_path: Path) -> None:
    """...and still fires when THIS session's own run has pending ceremony."""
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED, FILE_MODIFIED, FILE_MODIFIED),
        foreign_event_lines=(DELIVER_COMPLETE,),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=True)

    res = run_hook(hook_dir / "completion-gate.sh", root, payload={"task_subject": "T1"})

    assert res.returncode == 0
    assert "Ceremony pending" in res.stderr, res.stderr


# =========================================================================== #
# helper-idle.sh
#   unowned == "no run-scoped ceremony evidence", nudge still fires.
# =========================================================================== #
_IDLE_PAYLOAD = {"helper_name": "impl-1", "workstream_name": "ws-1", "session_id": "unused"}


@_HOOK_COPIES
def test_helper_idle_nudges_when_only_a_FOREIGN_run_checkpointed(hook_dir: Path, tmp_path: Path) -> None:
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(CHECKPOINT,),
    )
    write_hook_env(root)

    res = run_hook(hook_dir / "helper-idle.sh", root, payload=_IDLE_PAYLOAD)

    assert res.returncode == 2, "a foreign checkpoint silenced this helper's idle nudge"
    assert "trw_checkpoint" in res.stderr


@_HOOK_COPIES
def test_helper_idle_is_silenced_by_the_OWNED_runs_checkpoint(hook_dir: Path, tmp_path: Path) -> None:
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(CHECKPOINT,),
        foreign_event_lines=(FILE_MODIFIED,),
    )
    write_hook_env(root)

    res = run_hook(hook_dir / "helper-idle.sh", root, payload=_IDLE_PAYLOAD)

    assert res.returncode == 0, res.stderr


@_HOOK_COPIES
def test_helper_idle_still_nudges_an_unowned_session(hook_dir: Path, tmp_path: Path) -> None:
    """UNOWNED direction: the nudge keeps firing rather than silently vanishing."""
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(CHECKPOINT,),
    )
    write_hook_env(root)

    res = run_hook(hook_dir / "helper-idle.sh", root, payload=_IDLE_PAYLOAD)

    assert res.returncode == 2, "an unowned session silently escaped the idle nudge"


# =========================================================================== #
# phase-cycle-stop.sh
#   unowned == exit 0 WITHOUT enforcing: the phase cycle is the run.
# =========================================================================== #
@_HOOK_COPIES
def test_phase_cycle_blocks_on_the_OWNED_runs_unmet_phase(hook_dir: Path, tmp_path: Path) -> None:
    """Owned run is stuck in VALIDATE; the newest run has already delivered.

    Recency would read the foreign run's trw_deliver_complete and short-circuit to
    exit 0, so this test fails loudly if the hook ever regresses.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED, BUILD_CHECK),
        foreign_event_lines=(FILE_MODIFIED, DELIVER_COMPLETE),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=False)

    res = run_hook(hook_dir / "phase-cycle-stop.sh", root, payload={"session_id": "unused"})

    assert res.returncode == 2, f"foreign delivery cleared our phase gate: {res.stderr}"
    assert "TRW BLOCK [validate" in res.stderr, res.stderr


@_HOOK_COPIES
def test_phase_cycle_allows_when_the_OWNED_runs_criteria_are_met(hook_dir: Path, tmp_path: Path) -> None:
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(FILE_MODIFIED, BUILD_CHECK),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=False)

    res = run_hook(hook_dir / "phase-cycle-stop.sh", root, payload={"session_id": "unused"})

    assert res.returncode == 0, res.stderr


@_HOOK_COPIES
def test_phase_cycle_does_not_enforce_for_an_unowned_session(hook_dir: Path, tmp_path: Path) -> None:
    """UNOWNED direction: no owned run, no phase cycle, no block -- and no state.

    Justified because every criterion this hook evaluates is read out of a run's
    own events.jsonl, and its reversion path WRITES back into that run. A session
    with no run of its own has nothing to evaluate and must not touch another's.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        own_event_lines=(FILE_MODIFIED, BUILD_CHECK),
        foreign_event_lines=(FILE_MODIFIED, BUILD_CHECK),
    )
    write_hook_env(root)
    _build_status(root, tests_passed=False)

    res = run_hook(hook_dir / "phase-cycle-stop.sh", root, payload={"session_id": "unused"})

    assert res.returncode == 0, res.stderr
    assert not (root / ".claude" / "trw-phase-cycle.local.md").exists(), "wrote phase state from a foreign run"


@_HOOK_COPIES
def test_phase_cycle_still_enforces_for_a_client_with_no_identity(hook_dir: Path, tmp_path: Path) -> None:
    """The anti-false-negative guard: legacy newest-wins survives for such clients.

    A client that publishes no session id cannot own a run by definition. Treating
    that as "unowned" would disable this gate outright for every one of them, so
    the identity-unknown branch keeps today's single-instance behaviour.

    "No identity" has to mean BOTH channels: no session variable AND no session_id
    in the Stop payload. A payload-only id is still an identity, and a session that
    has one but no pin is positively unowned, not unknown.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED, BUILD_CHECK),
    )
    write_hook_env(root, client_id="copilot")
    _build_status(root, tests_passed=False)

    res = run_hook(hook_dir / "phase-cycle-stop.sh", root, payload={"transcript_path": ""}, identified=False)

    assert res.returncode == 2, "identity-unknown clients lost the phase gate entirely"


# =========================================================================== #
# pre-compact.sh
#   unowned == snapshot IS written, with run fields empty.
# =========================================================================== #
def _snapshot(root: Path) -> dict[str, object]:
    raw = (root / ".trw" / "context" / "pre_compact_state.json").read_text(encoding="utf-8")
    parsed: dict[str, object] = json.loads(raw)
    return parsed


@_HOOK_COPIES
def test_pre_compact_snapshots_the_owned_run(hook_dir: Path, tmp_path: Path) -> None:
    root, own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(FILE_MODIFIED,) * 18,
        own_phase="implement",
        foreign_phase="deliver",
    )
    write_hook_env(root)

    res = run_hook(hook_dir / "pre-compact.sh", root, payload={"source": "manual", "session_id": "unused"})

    assert res.returncode == 0
    state = _snapshot(root)
    assert str(own) in str(state["run_path"])
    assert FOREIGN_RUN_ID not in str(state["run_path"])
    assert state["phase"] == "implement", state
    assert state["ownership"] == "owned"


@_HOOK_COPIES
def test_pre_compact_writes_an_empty_snapshot_when_unowned(hook_dir: Path, tmp_path: Path) -> None:
    """UNOWNED direction: still armed, just honest.

    post-compact.sh keys recovery on a non-empty run_path and prints "No active
    run found in pre-compaction snapshot" otherwise, so an empty snapshot degrades
    correctly -- whereas skipping the write would disarm recovery AND leave the
    injected-learning dedup file uncleared.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED,) * 18,
        foreign_phase="deliver",
    )
    write_hook_env(root)
    injected = root / ".trw" / "context" / "injected_learning_ids.txt"
    injected.write_text("L-one\nL-two\n", encoding="utf-8")

    res = run_hook(hook_dir / "pre-compact.sh", root, payload={"source": "manual", "session_id": "unused"})

    assert res.returncode == 0
    state = _snapshot(root)
    assert state["run_path"] == "", state
    assert state["phase"] == ""
    assert state["events_logged"] == 0
    assert state["ownership"] == "unowned"
    assert FOREIGN_RUN_ID not in json.dumps(state), "foreign run leaked into the recovery snapshot"
    # The non-run-scoped duties still ran.
    assert injected.read_text(encoding="utf-8") == ""
    assert state["trigger"] == "manual"


@_HOOK_COPIES
def test_pre_compact_keeps_legacy_behaviour_with_no_identity(hook_dir: Path, tmp_path: Path) -> None:
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED,) * 18,
        foreign_phase="deliver",
    )
    write_hook_env(root, client_id="copilot")

    run_hook(hook_dir / "pre-compact.sh", root, payload={"source": "manual"}, identified=False)

    state = _snapshot(root)
    assert FOREIGN_RUN_ID in str(state["run_path"]), "single-instance recovery lost its snapshot"
    assert state["ownership"] == "identity-unknown"


# =========================================================================== #
# session-end.sh
#   unowned == housekeeping still runs; only the run-scoped warning is skipped.
# =========================================================================== #
@_HOOK_COPIES
def test_session_end_warns_with_the_owned_runs_event_count(hook_dir: Path, tmp_path: Path) -> None:
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED,) * 3,
        foreign_event_lines=(FILE_MODIFIED,) * 18,
    )
    write_hook_env(root)

    res = run_hook(hook_dir / "session-end.sh", root)

    assert "3 events logged" in res.stderr, res.stderr
    assert "18 events" not in res.stderr


@_HOOK_COPIES
def test_session_end_is_silent_but_still_tidies_when_unowned(hook_dir: Path, tmp_path: Path) -> None:
    """UNOWNED direction: no foreign event count is quoted, housekeeping still runs.

    The warning's premise ("N events were logged into your run") is unverifiable
    without an owned run, and stop-ceremony.sh -- not this advisory echo -- is the
    hook that actually gates delivery for an unpinned session.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED,) * 18,
    )
    write_hook_env(root)
    stale = root / ".trw" / "context" / "idle_block_impl-1"
    stale.write_text("1", encoding="utf-8")

    res = run_hook(hook_dir / "session-end.sh", root)

    assert res.returncode == 0
    assert "18 events" not in res.stderr, "quoted a foreign run's event count"
    assert "events logged" not in res.stderr
    assert not stale.exists(), "housekeeping was lost along with the run-scoped warning"


@_HOOK_COPIES
def test_session_end_tidies_even_when_the_owned_run_already_delivered(hook_dir: Path, tmp_path: Path) -> None:
    """Housekeeping is session-scoped, so it must not sit behind a run-scoped exit.

    Both runs are delivered, so the pre-migration hook returns early on either one
    and never tidies -- the assertion below is not satisfiable by the old code.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED, DELIVER_COMPLETE),
        foreign_event_lines=(FILE_MODIFIED, DELIVER_COMPLETE),
    )
    write_hook_env(root)
    stale = root / ".trw" / "context" / "tc_block_impl-1_T1"
    stale.write_text("1", encoding="utf-8")

    res = run_hook(hook_dir / "session-end.sh", root)

    assert res.returncode == 0
    assert not stale.exists(), "delivered sessions skipped housekeeping"


@_HOOK_COPIES
def test_session_end_still_warns_a_client_with_no_identity(hook_dir: Path, tmp_path: Path) -> None:
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED,) * 18,
    )
    write_hook_env(root, client_id="copilot")

    res = run_hook(hook_dir / "session-end.sh", root, identified=False)

    assert "18 events logged" in res.stderr, "single-instance clients lost the delivery reminder"


# =========================================================================== #
# subagent-start.sh
#   unowned == protocol reminders still injected; only run/phase lines omitted.
# =========================================================================== #
@_HOOK_COPIES
def test_subagent_start_injects_the_owned_run_and_its_phase(hook_dir: Path, tmp_path: Path) -> None:
    root, own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(FILE_MODIFIED,),
        own_phase="implement",
        foreign_phase="validate",
    )
    write_hook_env(root)

    res = run_hook(hook_dir / "subagent-start.sh", root, payload={"agent_type": "impl", "session_id": "unused"})

    assert f"Active run: {own}" in res.stdout, res.stdout
    assert OWN_RUN_ID in res.stdout
    assert FOREIGN_RUN_ID not in res.stdout
    # The phase selects the guidance, so the wrong phase coaches the wrong work.
    # Anchored on the implement branch's surviving text rather than the 5-step
    # self-review checklist it used to print — that checklist was removed as
    # over-verification (Opus 5 verifies unprompted), and pinning deleted prose
    # would have made this ownership test fail for an unrelated reason.
    assert "Integration is part of done" in res.stdout
    assert "VALIDATE PHASE:" not in res.stdout


@_HOOK_COPIES
def test_subagent_start_omits_run_state_but_keeps_the_protocol_when_unowned(hook_dir: Path, tmp_path: Path) -> None:
    """UNOWNED direction: the hook's actual job (protocol injection) still happens."""
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED,),
        foreign_phase="validate",
    )
    write_hook_env(root)

    res = run_hook(hook_dir / "subagent-start.sh", root, payload={"agent_type": "impl", "session_id": "unused"})

    assert res.returncode == 0
    assert "Active run:" not in res.stdout, "asserted a foreign run to a subagent"
    assert FOREIGN_RUN_ID not in res.stdout
    assert "Current phase:" not in res.stdout
    assert "VALIDATE PHASE:" not in res.stdout
    # ...and the non-run-scoped reminders, which are why this hook exists, survive.
    assert "trw_recall" in res.stdout
    assert "trw_checkpoint" in res.stdout


@_HOOK_COPIES
def test_subagent_start_keeps_legacy_context_with_no_identity(hook_dir: Path, tmp_path: Path) -> None:
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        foreign_event_lines=(FILE_MODIFIED,),
        foreign_phase="validate",
    )
    write_hook_env(root, client_id="copilot")

    res = run_hook(hook_dir / "subagent-start.sh", root, payload={"agent_type": "impl"}, identified=False)

    assert FOREIGN_RUN_ID in res.stdout, "single-instance subagents lost their run context"
    assert "VALIDATE PHASE:" in res.stdout
