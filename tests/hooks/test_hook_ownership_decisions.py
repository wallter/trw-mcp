"""Ledger UF-047 — six hooks stop resolving "the active run" by recency.

Three of those six (``completion-gate.sh``, ``helper-idle.sh``,
``phase-cycle-stop.sh``) were deleted by PRD-CORE-250 FR01-FR03: no shipped
template registered any of them, so none could ever fire and the ownership
decision they carried was unreachable. Their cases went with them; the three
that ship keep both directions asserted below.

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
import sys
from pathlib import Path

import pytest
from _ownership_harness import (
    BUNDLED_HOOKS,
    DELIVER_COMPLETE,
    FILE_MODIFIED,
    FOREIGN_RUN_ID,
    MIRROR_HOOKS,
    OWN_RUN_ID,
    build_project,
    run_hook,
    write_hook_env,
)

_HOOK_COPIES = pytest.mark.parametrize(
    "hook_dir",
    [pytest.param(BUNDLED_HOOKS, id="bundled"), pytest.param(MIRROR_HOOKS, id="mirror")],
)


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


# --- PRD-CORE-265-FR10: the editor hook warns, and can never block -----------


def _formation_project(tmp_path: Path) -> Path:
    """A project with a live formation whose ``impl-2`` owns ``src/beta``.

    The calling session is pinned to ``impl-1``'s run, and a foreign, newer run
    is present as always, so an advisory that resolved identity by recency would
    attribute the write to the wrong member and this fixture would catch it.
    """
    import yaml

    root, own, _foreign = build_project(tmp_path, own_pin=True, own_events=3, foreign_events=18)
    orchestrator = root / ".trw" / "runs" / "orchestrator-task" / "20260101T000000Z-orch"
    (orchestrator / "meta").mkdir(parents=True)
    (orchestrator / "meta" / "run.yaml").write_text("task: orchestrator\n", encoding="utf-8")
    manifest = {
        "formation_id": "fr10",
        "revision": 3,
        "created_utc": "2026-09-04T00:00:00+00:00",
        "updated_utc": "2026-09-04T00:00:00+00:00",
        "orchestrator_run_path": str(orchestrator),
        "members": [
            {"member_id": "impl-1", "client": "claude-code", "owned_paths": ["src/alpha"], "run_path": str(own)},
            {
                "member_id": "impl-2",
                "client": "claude-code",
                "owned_paths": ["src/beta"],
                "run_path": str(root / ".trw" / "runs" / "other"),
            },
        ],
    }
    (orchestrator / "formation.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (root / ".trw" / "runtime" / "formations.json").write_text(
        json.dumps({"fr10": str(orchestrator)}), encoding="utf-8"
    )
    own_yaml = own / "meta" / "run.yaml"
    own_yaml.write_text(own_yaml.read_text(encoding="utf-8") + "formation_id: fr10\nmember_id: impl-1\n", "utf-8")

    # An exec WRAPPER, never a symlink: CPython derives sys.prefix from the
    # interpreter's own path, so a symlinked python looks like an empty venv at
    # the fixture root, cannot import trw_mcp, and the advisory would silently
    # do nothing while the test still passed.
    venv_python = root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    venv_python.chmod(0o755)
    return root


_WRITE_PAYLOAD = {"tool_name": "Edit", "tool_input": {"file_path": "src/beta"}}


def test_intent_guard_warns_but_never_blocks_on_foreign_owned_path(tmp_path: Path) -> None:
    """FR10. Warn mode advises and exits 0; off mode is silent and exits 0.

    ATTRIBUTION. Guards ``_trw_formation_advisory`` in
    ``data/hooks/lib-intent-guard.sh`` and its call site ahead of the unenrolled
    fail-open exit. Delete the call and the warn case loses its message; move it
    below that exit and it becomes dead in every project that never enrolled in
    the intent contract, which is most of them. The exit-status assertions are
    the load-bearing half: this advisory must never be able to turn an allow
    into a block on any client.
    """
    root = _formation_project(tmp_path)
    hook = BUNDLED_HOOKS / "pre-tool-intent-guard.sh"

    warned = run_hook(hook, root, payload=_WRITE_PAYLOAD)
    assert warned.returncode == 0, warned.stdout + warned.stderr
    assert "TRW formation advisory" in warned.stderr, warned.stderr
    assert "impl-2" in warned.stderr and "src/beta" in warned.stderr
    assert "warning only" in warned.stderr, "the advisory must say the commit boundary is what refuses"

    silent = run_hook(
        hook, root, payload=_WRITE_PAYLOAD, TRW_FORMATION_MEMBER="", TRW_FORMATION_HOOK_OWNERSHIP_MODE="off"
    )
    assert silent.returncode == 0, silent.stdout + silent.stderr
    assert "TRW formation advisory" not in silent.stderr, silent.stderr

    own_path = run_hook(hook, root, payload={"tool_name": "Edit", "tool_input": {"file_path": "src/alpha"}})
    assert own_path.returncode == 0
    assert "TRW formation advisory" not in own_path.stderr


def test_intent_guard_advisory_is_inert_without_a_formation(tmp_path: Path) -> None:
    """FR10 / NFR02. No formation index means no spawn and no output at all."""
    root = _formation_project(tmp_path)
    (root / ".trw" / "runtime" / "formations.json").unlink()

    result = run_hook(BUNDLED_HOOKS / "pre-tool-intent-guard.sh", root, payload=_WRITE_PAYLOAD)
    assert result.returncode == 0
    assert "TRW formation advisory" not in result.stderr


def test_formation_hook_mode_vocabulary_excludes_block() -> None:
    """FR10. ``block`` is not spellable, in the knob or in the shell.

    A configuration that could turn this hook into a gate is the failure mode
    the PRD names: the guard's own decision path must stay the intent contract's.
    """
    import typing

    from trw_mcp.models.config._fields_formation import _FormationFields

    # ``get_type_hints`` because the module uses ``from __future__ import
    # annotations``: reading ``__annotations__`` directly yields the SOURCE
    # STRING, and ``set(getattr(str, "__args__", ()))`` is the empty set, which
    # would make this assertion pass against any vocabulary at all.
    annotation = typing.get_type_hints(_FormationFields)["formation_hook_ownership_mode"]
    assert set(typing.get_args(annotation)) == {"warn", "off"}
    for path in (BUNDLED_HOOKS / "lib-intent-guard.sh", MIRROR_HOOKS / "lib-intent-guard.sh"):
        body = path.read_text(encoding="utf-8")
        assert "formation_hook_ownership_mode: block" not in body
        advisory = body[body.index("_trw_formation_advisory() {") : body.index("# _trw_guard_main")]
        assert "exit 2" not in advisory, "the advisory body must contain no blocking exit"
