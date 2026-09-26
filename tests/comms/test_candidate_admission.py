"""PRD-CORE-274-FR18: candidate registry, orchestrator admission and the strict join gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from trw_mcp import formation
from trw_mcp.formation import FormationError


def _strict_payload(fixture: FormationFixture, **member_overrides: Any) -> dict[str, Any]:
    payload = fixture.payload()
    for member in payload["members"]:
        member.pop("open_join", None)
        member.update(member_overrides)
    return payload


def _announce(fixture: FormationFixture, pin: str, member: str = "impl-2", **kw: Any) -> formation.Candidate:
    arguments: dict[str, Any] = {
        "pin_key": pin,
        "run_path": fixture.member_runs[member],
        "worktree": None,
        "client": "codex",
        "ttl_seconds": 600,
    }
    return formation.announce_candidate(fixture.trw_dir, **{**arguments, **kw})


def test_a_pending_slot_is_not_first_come_by_default(formation_env: FormationFixture) -> None:
    created = formation.create(
        formation_env.orchestrator_run, _strict_payload(formation_env), trw_dir=formation_env.trw_dir
    )
    assert all(m.open_join is False for m in created.members)
    with pytest.raises(FormationError, match="join_not_admitted"):
        formation.join(
            "release-train",
            "impl-1",
            formation_env.member_runs["impl-1"],
            pin_key="pin-a",
            trw_dir=formation_env.trw_dir,
        )
    manifest = formation.load(formation_env.orchestrator_run, trw_dir=formation_env.trw_dir)
    assert manifest is not None and manifest.manifest.member("impl-1").status == "pending"


def test_an_explicitly_open_slot_joins_first_come(formation_env: FormationFixture) -> None:
    formation.create(formation_env.orchestrator_run, formation_env.payload(), trw_dir=formation_env.trw_dir)
    joined = formation.join(
        "release-train", "impl-1", formation_env.member_runs["impl-1"], pin_key="pin-a", trw_dir=formation_env.trw_dir
    )
    assert joined.member("impl-1").status == "joined"


def test_admission_is_the_authentication(formation_env: FormationFixture) -> None:
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b")
    revised = formation.revise(
        "release-train",
        fixture.orchestrator_run,
        {"impl-2": {"admitted_candidate": mine.candidate_id}},
        trw_dir=fixture.trw_dir,
    )
    slot = revised.member("impl-2")
    assert (slot.admitted_candidate, slot.admitted_revision) == (mine.candidate_id, revised.revision)

    run = fixture.member_runs["impl-2"]
    with pytest.raises(FormationError, match="join_not_admitted"):  # no handle presented
        formation.join("release-train", "impl-2", run, pin_key="pin-b", trw_dir=fixture.trw_dir)
    with pytest.raises(FormationError, match="admission_revoked"):  # a copied handle under another pin
        formation.join(
            "release-train", "impl-2", run, pin_key="pin-x", trw_dir=fixture.trw_dir, candidate_id=mine.candidate_id
        )
    with pytest.raises(FormationError, match="join_not_admitted"):  # the handle names another slot
        formation.join(
            "release-train",
            "impl-1",
            fixture.member_runs["impl-1"],
            pin_key="pin-b",
            trw_dir=fixture.trw_dir,
            candidate_id=mine.candidate_id,
        )
    joined = formation.join(
        "release-train", "impl-2", run, pin_key="pin-b", trw_dir=fixture.trw_dir, candidate_id=mine.candidate_id
    )
    assert joined.member("impl-2").status == "joined"


def test_the_admitting_revision_is_server_set(formation_env: FormationFixture) -> None:
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b")
    with pytest.raises(FormationError, match="server-set"):
        formation.revise(
            "release-train",
            fixture.orchestrator_run,
            {"impl-2": {"admitted_candidate": mine.candidate_id, "admitted_revision": 1}},
            trw_dir=fixture.trw_dir,
        )


@pytest.mark.parametrize("case", ["unknown", "expired", "non_orchestrator"])
def test_only_the_orchestrator_admits_only_a_live_candidate(formation_env: FormationFixture, case: str) -> None:
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    handle = "0" * 32
    if case == "expired":
        handle = _announce(fixture, "pin-b", ttl_seconds=-1).candidate_id
    caller = fixture.orchestrator_run
    if case == "non_orchestrator":
        handle = _announce(fixture, "pin-b").candidate_id
        caller = fixture.member_runs["impl-1"]
    with pytest.raises(FormationError):
        formation.revise("release-train", caller, {"impl-2": {"admitted_candidate": handle}}, trw_dir=fixture.trw_dir)


def test_creation_can_admit_a_candidate_that_announced_first(formation_env: FormationFixture) -> None:
    fixture = formation_env
    early = _announce(fixture, "pin-b")  # before the formation exists
    created = formation.create(
        fixture.orchestrator_run,
        {
            **_strict_payload(fixture),
            "members": [
                {"member_id": "impl-1", "client": "claude-code"},
                {"member_id": "impl-2", "client": "codex", "admitted_candidate": early.candidate_id},
            ],
        },
        trw_dir=fixture.trw_dir,
    )
    assert created.member("impl-2").admitted_revision == created.revision == 1


def test_the_registry_caps_live_candidates_without_eviction(formation_env: FormationFixture, tmp_path: Path) -> None:
    fixture = formation_env
    first = _announce(fixture, "pin-0")
    for index in range(1, formation.CANDIDATE_CAP):
        _announce(fixture, f"pin-{index}", run_path=tmp_path / f"run-{index}")
    with pytest.raises(formation.CandidateError) as full:
        _announce(fixture, "pin-late", run_path=tmp_path / "late")
    assert full.value.reason == "candidate_registry_full"
    again = _announce(fixture, "pin-0")  # a re-announce refreshes, it does not consume a slot
    assert again.candidate_id == first.candidate_id and again.expires_at >= first.expires_at
    assert formation.candidate(fixture.trw_dir, first.candidate_id) is not None, "a live record was evicted"
    assert len(formation.live_candidates(fixture.trw_dir)) == formation.CANDIDATE_CAP


def test_expired_candidates_do_not_hold_registry_room(formation_env: FormationFixture, tmp_path: Path) -> None:
    fixture = formation_env
    for index in range(formation.CANDIDATE_CAP):
        _announce(fixture, f"pin-{index}", run_path=tmp_path / f"run-{index}", ttl_seconds=-1)
    assert _announce(fixture, "pin-fresh").state == "active"


def test_the_registry_is_owner_only(formation_env: FormationFixture) -> None:
    import stat

    _announce(formation_env, "pin-b")
    registry = formation_env.trw_dir / "runtime" / "comms-candidates.json"
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600


# --- the public trw_inbox bootstrap actions ---------------------------------


def _peers(server: Any, action: str) -> dict[str, Any]:
    import asyncio

    result = asyncio.run(server.call_tool("trw_inbox", {"action": action}))
    assert isinstance(result.structured_content, dict)
    return result.structured_content


@pytest.fixture
def bootstrap_scene(formation_env: FormationFixture, comms_server: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from tests._formation_test_support import write_pin
    from tests.comms.conftest import enable_comms

    enable_comms(monkeypatch)
    write_pin(formation_env, "pin-b", formation_env.member_runs["impl-2"])
    write_pin(formation_env, "pin-orch", formation_env.orchestrator_run)
    return comms_server


def _lead_formation(fixture: FormationFixture) -> None:
    formation.create(
        fixture.orchestrator_run,
        {
            **fixture.payload(),
            "members": [
                {"member_id": "lead", "client": "claude-code", "open_join": True},
                {"member_id": "impl-2", "client": "codex"},
            ],
        },
        trw_dir=fixture.trw_dir,
    )
    formation.join("release-train", "lead", fixture.orchestrator_run, pin_key="pin-orch", trw_dir=fixture.trw_dir)


def test_announce_needs_only_a_pinned_run_and_returns_nothing_identifying(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "pin-unpinned")
    assert _peers(bootstrap_scene, "announce")["reason"] == "no_pinned_run"

    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")  # no formation exists yet
    first = _peers(bootstrap_scene, "announce")
    assert (first["status"], first["state"]) == ("ok", "candidate")
    again = _peers(bootstrap_scene, "announce")
    assert again["candidate_id"] == first["candidate_id"], "a re-announce keeps its handle"
    flat = repr(first)
    assert "pin-b" not in flat and str(formation_env.trw_dir) not in flat
    assert not list(formation_env.project_root.rglob("comms.sqlite3")), "announcing created a mailbox"


def test_discover_shows_candidates_only_to_the_orchestrator(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    _lead_formation(formation_env)

    seen_by_candidate = _peers(bootstrap_scene, "discover")
    assert [f["formation_id"] for f in seen_by_candidate["formations"]] == ["release-train"]
    assert "candidates" not in seen_by_candidate

    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    seen_by_lead = _peers(bootstrap_scene, "discover")
    assert [c["candidate_id"] for c in seen_by_lead["candidates"]] == [handle]
    assert "pin-b" not in repr(seen_by_lead) and str(formation_env.member_runs["impl-2"]) not in repr(seen_by_lead)


def test_withdraw_removes_the_candidate_from_admission(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    assert _peers(bootstrap_scene, "withdraw")["state"] == "opted_out"
    _lead_formation(formation_env)
    with pytest.raises(FormationError, match="candidate_not_admissible"):
        formation.revise(
            "release-train",
            formation_env.orchestrator_run,
            {"impl-2": {"admitted_candidate": handle}},
            trw_dir=formation_env.trw_dir,
        )


def test_explicit_false_disables_the_bootstrap_actions_too(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import TRWConfig

    config = TRWConfig(comms_enabled=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    assert _peers(bootstrap_scene, "announce")["status"] == "disabled"
    assert not (formation_env.trw_dir / "runtime" / "comms-candidates.json").exists()


# --- pick-up: stages, order independence, revocation ------------------------


def _admit(fixture: FormationFixture, handle: str | None) -> None:
    formation.revise(
        "release-train", fixture.orchestrator_run, {"impl-2": {"admitted_candidate": handle}}, trw_dir=fixture.trw_dir
    )


def _endpoint_members(fixture: FormationFixture) -> set[str]:
    import sqlite3

    found = list(fixture.project_root.rglob("comms.sqlite3"))
    if not found:
        return set()
    conn = sqlite3.connect(found[0])
    try:
        return {row[0] for row in conn.execute("SELECT member_id FROM endpoints")}
    finally:
        conn.close()


@pytest.mark.parametrize("order", ["announce_first", "formation_first"])
def test_an_admitted_candidate_is_picked_up_on_its_next_call(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch, order: str
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    if order == "announce_first":
        handle = _peers(bootstrap_scene, "announce")["candidate_id"]
        _lead_formation(formation_env)
    else:
        _lead_formation(formation_env)
        handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    assert _peers(bootstrap_scene, "list")["status"] == "refused", "a candidate is nobody until admitted"

    _admit(formation_env, handle)
    picked = _peers(bootstrap_scene, "list")  # ANY trw_inbox action picks up
    assert (picked["status"], picked["member_id"]) == ("ok", "impl-2")
    assert _endpoint_members(formation_env) == {"impl-2"}
    assert formation.candidate(formation_env.trw_dir, handle).state == "picked_up"  # type: ignore[union-attr]
    assert _peers(bootstrap_scene, "list")["status"] == "ok", "afterwards it is an ordinary member"


def test_a_copied_handle_under_another_binding_is_not_picked_up(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._formation_test_support import write_pin

    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    _lead_formation(formation_env)
    _admit(formation_env, handle)
    write_pin(formation_env, "pin-thief", formation_env.member_runs["impl-1"])
    monkeypatch.setenv("TRW_SESSION_ID", "pin-thief")
    assert _peers(bootstrap_scene, "enroll")["status"] == "refused"
    with pytest.raises(FormationError, match="admission_revoked"):
        formation.join(
            "release-train",
            "impl-2",
            formation_env.member_runs["impl-1"],
            pin_key="pin-thief",
            trw_dir=formation_env.trw_dir,
            candidate_id=handle,
        )
    assert _endpoint_members(formation_env) == set()


def _crash_after_stage_one(fixture: FormationFixture, handle: str) -> None:
    formation.set_candidate_state(fixture.trw_dir, handle, "joining")
    formation.join(
        "release-train",
        "impl-2",
        fixture.member_runs["impl-2"],
        pin_key="pin-b",
        trw_dir=fixture.trw_dir,
        candidate_id=handle,
    )


def test_a_crash_between_stages_resumes_idempotently(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    _lead_formation(formation_env)
    _admit(formation_env, handle)
    _crash_after_stage_one(formation_env, handle)
    revision = formation.load(formation_env.orchestrator_run, trw_dir=formation_env.trw_dir).manifest.revision  # type: ignore[union-attr]

    resumed = _peers(bootstrap_scene, "heartbeat")
    assert (resumed["status"], resumed["member_id"]) == ("ok", "impl-2")
    after = formation.load(formation_env.orchestrator_run, trw_dir=formation_env.trw_dir)
    assert after is not None and after.manifest.revision == revision, "the resumed stage 1 rewrote the manifest"


def test_revoking_after_stage_one_ends_in_admission_revoked_with_compensation(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    _lead_formation(formation_env)
    _admit(formation_env, handle)
    _crash_after_stage_one(formation_env, handle)
    _admit(formation_env, None)  # the orchestrator withdraws the admission mid pick-up

    refused = _peers(bootstrap_scene, "enroll")
    assert (refused["status"], refused["reason"]) == ("refused", "admission_revoked")
    assert "re-admitted" in refused["detail"]
    assert _endpoint_members(formation_env) == set(), "no endpoint after revocation"
    assert formation.stamped_ids(formation_env.member_runs["impl-2"]) is None, "the run stamp was not revoked"
    assert formation.candidate(formation_env.trw_dir, handle).state == "revoked"  # type: ignore[union-attr]
    again = _peers(bootstrap_scene, "enroll")
    assert again["status"] == "refused" and _endpoint_members(formation_env) == set()


def test_withdrawing_after_admission_prevents_pick_up(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    _lead_formation(formation_env)
    _admit(formation_env, handle)
    assert _peers(bootstrap_scene, "withdraw")["state"] == "opted_out"
    assert _peers(bootstrap_scene, "enroll")["status"] == "refused"
    assert _endpoint_members(formation_env) == set()


# --- lane C review of A7a: M2, D2, M3 ---------------------------------------


def test_an_admission_never_commits_without_its_worktree_record(
    formation_env: FormationFixture, tmp_path: Path
) -> None:
    """M2: the record is written only after every candidate transition committed; a
    record failure commits neither the manifest NOR the candidate transition
    (PRD-FIX-149 review R2 -- rolled back to ACTIVE, not left stranded ADMITTED).
    """
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    worktree = tmp_path / "wt"
    mine = _announce(fixture, "pin-b", worktree=worktree)
    records = fixture.trw_dir / "runtime" / "worktree-members.json"
    records.write_text("{not json", encoding="utf-8")
    with pytest.raises(FormationError):
        _admit(fixture, mine.candidate_id)
    loaded = formation.load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
    assert loaded is not None and loaded.manifest.member("impl-2").admitted_candidate is None
    stranded = formation.candidate(fixture.trw_dir, mine.candidate_id)
    assert stranded is not None and stranded.state == "active" and stranded.admitted_formation is None, (
        "a worktree-record failure must roll the candidate back, not strand it admitted with nothing naming it"
    )

    records.unlink()  # the operator repairs the store; the SAME handle now admits
    _admit(fixture, mine.candidate_id)
    record = formation.worktree_record(fixture.trw_dir, worktree)
    assert record is not None and record.member_id == "impl-2"


def test_a_conflicting_second_candidate_admits_neither_candidate(formation_env: FormationFixture) -> None:
    """PRD-FIX-149 review R2: within ONE admission call, an illegal transition on the
    SECOND candidate (already admitted by a different formation -- the exact shape a
    race between ``admitted_members``' unlocked read and the locked transition would
    produce) must not leave the FIRST, otherwise-legal candidate committed either.
    """
    from trw_mcp.formation._admission import commit_admissions
    from trw_mcp.formation._candidates import CandidateState, StateTransition, set_states
    from trw_mcp.formation._manifest import FormationMember

    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    first = _announce(fixture, "pin-a", member="impl-1")
    second = _announce(fixture, "pin-b", member="impl-2")
    set_states(
        fixture.trw_dir,
        [StateTransition(second.candidate_id, CandidateState.ADMITTED, admitted_formation="hotfix-train")],
    )

    member1 = FormationMember(
        member_id="impl-1", client="claude-code", admitted_candidate=first.candidate_id, admitted_revision=2
    )
    member2 = FormationMember(
        member_id="impl-2", client="claude-code", admitted_candidate=second.candidate_id, admitted_revision=2
    )

    with pytest.raises(FormationError, match="cannot move from admitted"):
        commit_admissions(
            fixture.trw_dir,
            "release-train",
            fixture.orchestrator_run,
            [(member1, first), (member2, second)],
            [],
        )

    first_state = formation.candidate(fixture.trw_dir, first.candidate_id)
    assert first_state is not None and (first_state.state, first_state.admitted_formation) == ("active", None), (
        "the otherwise-valid first candidate must not be admitted when the second in the same batch fails"
    )


def test_release_refuses_a_candidate_admitted_by_a_different_formation(formation_env: FormationFixture) -> None:
    """PRD-FIX-149 review R2: release (ADMITTED -> ACTIVE) is a CAS on ``admitted_formation``;
    a formation may only release a candidate it actually admitted.
    """
    from trw_mcp.formation import FormationError as _FormationError
    from trw_mcp.formation._candidates import CandidateState, StateTransition, set_states

    fixture = formation_env
    mine = _announce(fixture, "pin-b")
    set_states(
        fixture.trw_dir,
        [StateTransition(mine.candidate_id, CandidateState.ADMITTED, admitted_formation="release-train")],
    )

    with pytest.raises(_FormationError, match="'hotfix-train'"):
        set_states(
            fixture.trw_dir,
            [
                StateTransition(
                    mine.candidate_id,
                    CandidateState.ACTIVE,
                    admitted_formation=None,
                    expected_admitted_formation="hotfix-train",
                )
            ],
        )
    unchanged = formation.candidate(fixture.trw_dir, mine.candidate_id)
    assert unchanged is not None and (unchanged.state, unchanged.admitted_formation) == ("admitted", "release-train")


def test_a_candidate_is_admitted_to_one_slot_at_a_time(formation_env: FormationFixture) -> None:
    """D2: admission spends the candidate until the orchestrator releases it."""
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b")
    _admit(fixture, mine.candidate_id)
    assert formation.candidate(fixture.trw_dir, mine.candidate_id).state == "admitted"  # type: ignore[union-attr]
    with pytest.raises(FormationError, match="candidate_not_admissible"):
        formation.revise(
            "release-train",
            fixture.orchestrator_run,
            {"impl-1": {"admitted_candidate": mine.candidate_id}},
            trw_dir=fixture.trw_dir,
        )
    _admit(fixture, None)  # released before pick-up: admissible again
    assert formation.candidate(fixture.trw_dir, mine.candidate_id).state == "active"  # type: ignore[union-attr]
    formation.revise(
        "release-train",
        fixture.orchestrator_run,
        {"impl-1": {"admitted_candidate": mine.candidate_id}},
        trw_dir=fixture.trw_dir,
    )


def test_a_legacy_manifest_pending_slot_refuses_with_the_remedy(formation_env: FormationFixture) -> None:
    """M3: a manifest written before FR18 has no open_join key; its pending slot refuses, saying how to proceed."""
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    manifest = fixture.manifest_path()
    manifest.write_text(
        "\n".join(line for line in manifest.read_text(encoding="utf-8").splitlines() if "open_join" not in line) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(FormationError) as refused:
        formation.join(
            "release-train", "impl-1", fixture.member_runs["impl-1"], pin_key="pin-a", trw_dir=fixture.trw_dir
        )
    assert "join_not_admitted" in str(refused.value) and "open_join" in str(refused.value)


# --- lane C review of A7b: M4, S1 -------------------------------------------


def test_lock_contention_during_stage_one_is_retryable_not_a_revocation(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M4: only a DEFINITIVE admission refusal revokes; contention leaves the candidate resumable."""
    import importlib
    from contextlib import contextmanager

    _join = importlib.import_module("trw_mcp.formation._join")  # the facade re-binds the name to a function

    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    _lead_formation(formation_env)
    _admit(formation_env, handle)
    real = _join.rewrite_manifest

    @contextmanager
    def contended(*_a: Any, **_k: Any) -> Any:
        raise FormationError("could not acquire the formation lock within 0.01s")
        yield  # pragma: no cover

    monkeypatch.setattr(_join, "rewrite_manifest", contended)
    busy = _peers(bootstrap_scene, "enroll")
    assert (busy["status"], busy["reason"], busy.get("retryable")) == ("refused", "storage_contended", True)
    assert formation.candidate(formation_env.trw_dir, handle).state == "joining"  # type: ignore[union-attr]
    assert "revoked_formation_id" not in formation_env.member_runs["impl-2"].joinpath("meta", "run.yaml").read_text()

    monkeypatch.setattr(_join, "rewrite_manifest", real)
    picked = _peers(bootstrap_scene, "enroll")
    assert (picked["status"], picked["member_id"]) == ("ok", "impl-2")
    assert formation.candidate(formation_env.trw_dir, handle).state == "picked_up"  # type: ignore[union-attr]


def test_a_corrupt_candidate_registry_does_not_break_enrolled_members(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S1: the per-call pick-up check fails open for members; only candidates wait for a repair."""
    _lead_formation(formation_env)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    assert _peers(bootstrap_scene, "enroll")["status"] == "ok"
    (formation_env.trw_dir / "runtime" / "comms-candidates.json").write_text("{corrupt", encoding="utf-8")
    assert _peers(bootstrap_scene, "heartbeat")["status"] == "ok"


def test_a_manifest_without_fr18_use_stays_readable_by_pre_fr18_builds(formation_env: FormationFixture) -> None:
    """Pre-FR18 builds load members with extra="forbid": unset FR18 fields must not be written."""
    import yaml

    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    formation.revise(
        "release-train", fixture.orchestrator_run, {"impl-1": {"note": "touched"}}, trw_dir=fixture.trw_dir
    )
    members = yaml.safe_load(fixture.manifest_path().read_text(encoding="utf-8"))["members"]
    fr18 = {"open_join", "admitted_candidate", "admitted_revision"}
    assert not any(fr18 & set(member) for member in members), members
    reloaded = formation.load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
    assert reloaded is not None and reloaded.manifest.member("impl-1").open_join is False


# --- ledger N2/N3: orchestrator slot changes after creation -------------------


def test_a_slot_added_after_creation_admits_a_late_candidate_who_is_picked_up(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead's N3 acceptance: 2 slots, add a third admitting a candidate, its next trw_inbox call joins."""
    _lead_formation(formation_env)  # lead + impl-2
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    grown = formation.add_slots(
        "release-train",
        formation_env.orchestrator_run,
        [{"member_id": "late-3", "client": "grok", "admitted_candidate": handle}],
        trw_dir=formation_env.trw_dir,
    )
    slot = grown.member("late-3")
    assert (slot.status, slot.admitted_candidate, slot.admitted_revision) == ("pending", handle, grown.revision)
    picked = _peers(bootstrap_scene, "list")
    assert (picked["status"], picked["member_id"]) == ("ok", "late-3")


def test_only_a_pending_slot_is_removed_and_its_admission_is_released(
    bootstrap_scene: Any, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lead_formation(formation_env)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    handle = _peers(bootstrap_scene, "announce")["candidate_id"]
    _admit(formation_env, handle)
    with pytest.raises(FormationError, match="joined or ended"):
        formation.remove_slot("release-train", formation_env.orchestrator_run, "lead", trw_dir=formation_env.trw_dir)
    shrunk = formation.remove_slot(
        "release-train", formation_env.orchestrator_run, "impl-2", trw_dir=formation_env.trw_dir
    )
    assert [m.member_id for m in shrunk.members] == ["lead"]
    assert formation.candidate(formation_env.trw_dir, handle).state == "active"  # type: ignore[union-attr]


@pytest.mark.parametrize("case", ["non_orchestrator", "sets_run", "duplicate_id", "stale_candidate"])
def test_slot_additions_refuse_what_only_the_server_may_set(formation_env: FormationFixture, case: str) -> None:
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    caller, member = fixture.orchestrator_run, {"member_id": "late-3", "client": "grok"}
    if case == "non_orchestrator":
        caller = fixture.member_runs["impl-1"]
    elif case == "sets_run":
        member["run_path"] = str(fixture.member_runs["impl-1"])
    elif case == "duplicate_id":
        member["member_id"] = "impl-1"
    else:
        member["admitted_candidate"] = _announce(fixture, "pin-b", ttl_seconds=-1).candidate_id
    with pytest.raises(FormationError):
        formation.add_slots("release-train", caller, [member], trw_dir=fixture.trw_dir)
    loaded = formation.load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
    assert loaded is not None and [m.member_id for m in loaded.manifest.members] == ["impl-1", "impl-2"]


def test_a_worktree_candidate_added_to_a_new_slot_gets_its_fr17_record(
    formation_env: FormationFixture, tmp_path: Path
) -> None:
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b", worktree=tmp_path / "wt")
    formation.add_slots(
        "release-train",
        fixture.orchestrator_run,
        [{"member_id": "late-3", "client": "grok", "admitted_candidate": mine.candidate_id}],
        trw_dir=fixture.trw_dir,
    )
    record = formation.worktree_record(fixture.trw_dir, tmp_path / "wt")
    assert record is not None and record.member_id == "late-3"


def test_the_admit_cli_verb_admits_by_handle(
    formation_env: FormationFixture, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    from tests._formation_test_support import pin_session
    from trw_mcp.tools._formation_cli import run_formation

    fixture = formation_env
    pin_session(monkeypatch, fixture.orchestrator_run)
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b")
    args = argparse.Namespace(
        formation_command="admit",
        member_id="impl-2",
        candidate_id=mine.candidate_id,
        run_path=str(fixture.orchestrator_run),
    )
    with pytest.raises(SystemExit) as done:
        run_formation(args)
    assert done.value.code == 0, capsys.readouterr().err
    loaded = formation.load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
    assert loaded is not None and loaded.manifest.member("impl-2").admitted_candidate == mine.candidate_id


# --------------------------------------------------------------------------- #
# PRD-FIX-149 FR04/FR05 (ledger R4): two orchestrators, one announced handle.
# --------------------------------------------------------------------------- #


def test_set_state_same_state_different_formation_refuses(formation_env: FormationFixture) -> None:
    """FR05: re-admitting to ANOTHER formation is refused; the same formation's retry is a no-op."""
    from trw_mcp.formation._candidates import CandidateState, set_state

    trw_dir = formation_env.trw_dir
    mine = _announce(formation_env, "pin-b")
    set_state(trw_dir, mine.candidate_id, CandidateState.ADMITTED, admitted_formation="alpha")

    with pytest.raises(FormationError, match="'alpha'"):
        set_state(trw_dir, mine.candidate_id, CandidateState.ADMITTED, admitted_formation="beta")
    after = formation.candidate(trw_dir, mine.candidate_id)
    assert after is not None and after.admitted_formation == "alpha", "the refusal overwrote nothing"

    retried = set_state(trw_dir, mine.candidate_id, CandidateState.ADMITTED, admitted_formation="alpha")
    assert retried is not None and (retried.state, retried.admitted_formation) == ("admitted", "alpha")


def test_concurrent_admission_race_refuses_second_formation(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR04: both orchestrators pass the unlocked ACTIVE read; exactly one admission commits.

    A barrier inside the admission read holds both revisions until each has seen
    the candidate ACTIVE -- the exact interleaving that let the second silently
    take ``admitted_formation`` from the first.
    """
    import threading

    from tests._formation_test_support import make_run_dir
    from trw_mcp.formation import _admission

    fixture = formation_env
    other_run = make_run_dir(fixture.trw_dir / "runs", "orchestrator-b")
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    second = _strict_payload(fixture)
    second["formation_id"] = "hotfix-train"
    formation.create(other_run, second, trw_dir=fixture.trw_dir)
    handle = _announce(fixture, "pin-b").candidate_id

    barrier = threading.Barrier(2, timeout=10)
    unlocked_read = _admission.candidate

    def racing_read(trw_dir: Path, candidate_id: str) -> Any:
        found = unlocked_read(trw_dir, candidate_id)
        barrier.wait()  # both revisions now hold an ACTIVE snapshot
        return found

    outcomes: dict[str, str] = {}

    def admit(formation_id: str, run: Path) -> None:
        try:
            formation.revise(formation_id, run, {"impl-2": {"admitted_candidate": handle}}, trw_dir=fixture.trw_dir)
            outcomes[formation_id] = "admitted"
        except FormationError as exc:
            outcomes[formation_id] = str(exc)

    monkeypatch.setattr(_admission, "candidate", racing_read)
    threads = [
        threading.Thread(target=admit, args=("release-train", fixture.orchestrator_run)),
        threading.Thread(target=admit, args=("hotfix-train", other_run)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    winners = [fid for fid, outcome in outcomes.items() if outcome == "admitted"]
    assert len(outcomes) == 2 and len(winners) == 1, outcomes
    (loser,) = set(outcomes) - set(winners)
    assert "cannot move from admitted" in outcomes[loser] and repr(winners[0]) in outcomes[loser]

    held = formation.candidate(fixture.trw_dir, handle)
    assert held is not None and held.admitted_formation == winners[0]
    loser_run = fixture.orchestrator_run if loser == "release-train" else other_run
    loser_view = formation.load(loser_run, trw_dir=fixture.trw_dir)
    assert loser_view is not None and loser_view.manifest.member("impl-2").admitted_candidate is None


# --------------------------------------------------------------------------- #
# PRD-FIX-149 NFR01 (ledger R5): an interrupted join finishes on retry.
# --------------------------------------------------------------------------- #


def test_interrupted_join_retry_repairs_stamp(formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest committed, the process died before stamping run.yaml; the retry stamps it."""
    import importlib

    from trw_mcp.formation import stamped_ids

    _join = importlib.import_module("trw_mcp.formation._join")  # the package re-binds ``_join`` to a function
    fixture = formation_env
    run = fixture.member_runs["impl-1"]
    formation.create(fixture.orchestrator_run, fixture.payload(), trw_dir=fixture.trw_dir)

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise SystemExit("killed between the manifest commit and the run stamp")

    with monkeypatch.context() as crashed:
        crashed.setattr(_join, "_stamp_run_record", crash)
        with pytest.raises(SystemExit):
            formation.join("release-train", "impl-1", run, pin_key="pin-a", trw_dir=fixture.trw_dir)
    committed = formation.load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
    assert committed is not None and committed.manifest.member("impl-1").status == "joined"
    assert stamped_ids(run) is None, "precondition: the crash window left the run unstamped"
    revision = committed.manifest.revision

    retried = formation.join("release-train", "impl-1", run, pin_key="pin-a", trw_dir=fixture.trw_dir)

    assert stamped_ids(run) == ("release-train", "impl-1")
    assert retried.revision == revision, "the repair is a run.yaml write, not a manifest revision"
    context = formation.load(run, trw_dir=fixture.trw_dir)
    assert context is not None and context.member_id == "impl-1", "the member resolves its formation again"


def test_a_retried_join_does_not_undo_a_revoked_stamp(formation_env: FormationFixture) -> None:
    """The repair fills only a MISSING stamp: FR18 compensation stays final."""
    from trw_mcp.formation import revoke_run_stamp, stamped_ids

    fixture = formation_env
    run = fixture.member_runs["impl-1"]
    formation.create(fixture.orchestrator_run, fixture.payload(), trw_dir=fixture.trw_dir)
    formation.join("release-train", "impl-1", run, pin_key="pin-a", trw_dir=fixture.trw_dir)
    assert revoke_run_stamp(run) is True

    formation.join("release-train", "impl-1", run, pin_key="pin-a", trw_dir=fixture.trw_dir)

    assert stamped_ids(run) is None
    assert "revoked_formation_id: release-train" in (run / "meta" / "run.yaml").read_text(encoding="utf-8")


def test_a_retried_join_after_abandonment_is_refused(formation_env: FormationFixture) -> None:
    """PRD-FIX-149 review R3: a settled orchestrator verdict is never resurrected by a stale retry.

    The idempotent early-return branch that repairs a missing stamp must not
    ALSO repair (or merely no-op through) a member the orchestrator has since
    retired -- that would let a zombie process's retried join silently look
    live again after the orchestrator's ``abandoned`` verdict.
    """
    fixture = formation_env
    run = fixture.member_runs["impl-1"]
    formation.create(fixture.orchestrator_run, fixture.payload(), trw_dir=fixture.trw_dir)
    formation.join("release-train", "impl-1", run, pin_key="pin-a", trw_dir=fixture.trw_dir)
    formation.revise(
        "release-train", fixture.orchestrator_run, {"impl-1": {"status": "abandoned"}}, trw_dir=fixture.trw_dir
    )

    with pytest.raises(FormationError, match="abandoned"):
        formation.join("release-train", "impl-1", run, pin_key="pin-a", trw_dir=fixture.trw_dir)


# --------------------------------------------------------------------------- #
# PRD-FIX-149 round 3 review: R6/R7/R9 (worktree record lifecycle, batch
# rollback, and stranded-self-admission retry).
# --------------------------------------------------------------------------- #


def test_a_released_worktree_record_can_be_admitted_by_another_formation(
    formation_env: FormationFixture, tmp_path: Path
) -> None:
    """R6: nothing ever removed a worktree membership record before this fix, so once F1
    admitted a worktree, F2 could never admit it again -- even after F1 released it.
    """
    from tests._formation_test_support import make_run_dir

    fixture = formation_env
    worktree = tmp_path / "wt"
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    first = _announce(fixture, "pin-b", worktree=worktree)
    _admit(fixture, first.candidate_id)
    record = formation.worktree_record(fixture.trw_dir, worktree)
    assert record is not None and record.formation_id == "release-train"

    _admit(fixture, None)  # F1 releases the candidate before pick-up
    assert formation.worktree_record(fixture.trw_dir, worktree) is None, "the stale record must be deleted on release"

    other_run = make_run_dir(fixture.trw_dir / "runs", "orchestrator-b")
    second_payload = _strict_payload(fixture)
    second_payload["formation_id"] = "hotfix-train"
    formation.create(other_run, second_payload, trw_dir=fixture.trw_dir)
    second = _announce(fixture, "pin-c", worktree=worktree)
    formation.revise(
        "hotfix-train", other_run, {"impl-2": {"admitted_candidate": second.candidate_id}}, trw_dir=fixture.trw_dir
    )
    record2 = formation.worktree_record(fixture.trw_dir, worktree)
    assert record2 is not None and record2.formation_id == "hotfix-train"


def test_a_second_record_write_failure_deletes_the_first_records_in_the_same_batch(
    formation_env: FormationFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7: within ONE ``commit_admissions`` call, a SECOND record write failing must not
    leave the FIRST record (already written earlier in the same batch) behind, and both
    candidates roll back to ``active``.
    """
    from trw_mcp.formation import _admission
    from trw_mcp.formation._admission import commit_admissions
    from trw_mcp.formation._manifest import FormationMember

    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    wt1, wt2 = tmp_path / "wt1", tmp_path / "wt2"
    first = _announce(fixture, "pin-a", member="impl-1", worktree=wt1)
    second = _announce(fixture, "pin-b", member="impl-2", worktree=wt2)

    real_record = _admission.record_worktree_member
    calls: list[Path] = []

    def flaky(trw_dir: Path, worktree: Path, **kw: Any) -> None:
        calls.append(worktree)
        if len(calls) == 2:
            raise OSError("disk full")
        real_record(trw_dir, worktree, **kw)

    monkeypatch.setattr(_admission, "record_worktree_member", flaky)

    member1 = FormationMember(
        member_id="impl-1", client="claude-code", admitted_candidate=first.candidate_id, admitted_revision=2
    )
    member2 = FormationMember(
        member_id="impl-2", client="claude-code", admitted_candidate=second.candidate_id, admitted_revision=2
    )

    with pytest.raises(OSError, match="disk full"):
        commit_admissions(
            fixture.trw_dir, "release-train", fixture.orchestrator_run, [(member1, first), (member2, second)], []
        )

    assert formation.worktree_record(fixture.trw_dir, wt1) is None, "the first record must be deleted on rollback"
    assert formation.worktree_record(fixture.trw_dir, wt2) is None
    for handle in (first.candidate_id, second.candidate_id):
        state = formation.candidate(fixture.trw_dir, handle)
        assert state is not None and (state.state, state.admitted_formation) == ("active", None), handle


def test_a_failed_compensating_rollback_is_chained_onto_the_original_failure(
    formation_env: FormationFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7: when the compensating rollback ITSELF raises, the original record-write
    failure must not be lost -- it is chained onto the rollback error via ``raise ... from``.
    """
    from trw_mcp.formation import _admission
    from trw_mcp.formation._admission import commit_admissions
    from trw_mcp.formation._manifest import FormationMember

    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b", worktree=tmp_path / "wt")

    real_set_states = _admission.set_states
    calls = {"n": 0}

    def flaky_set_states(trw_dir: Path, transitions: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return real_set_states(trw_dir, transitions)
        raise RuntimeError("rollback storage unavailable")

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_admission, "set_states", flaky_set_states)
    monkeypatch.setattr(_admission, "record_worktree_member", boom)

    member = FormationMember(
        member_id="impl-2", client="claude-code", admitted_candidate=mine.candidate_id, admitted_revision=2
    )

    with pytest.raises(RuntimeError, match="rollback storage unavailable") as excinfo:
        commit_admissions(fixture.trw_dir, "release-train", fixture.orchestrator_run, [(member, mine)], [])

    assert isinstance(excinfo.value.__cause__, OSError), "the original write failure must be chained, not lost"


def test_a_candidate_stranded_admitted_by_this_formation_with_no_manifest_is_re_admissible(
    formation_env: FormationFixture,
) -> None:
    """R9: a crash between the candidate's CAS to ``admitted`` and the manifest write can
    strand a candidate ``admitted`` by this formation with NO manifest anywhere naming it;
    a retry by that SAME formation must be able to finish the admission it started.
    """
    from trw_mcp.formation._candidates import set_state

    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b")
    set_state(fixture.trw_dir, mine.candidate_id, "admitted", admitted_formation="release-train")

    revised = formation.revise(
        "release-train",
        fixture.orchestrator_run,
        {"impl-2": {"admitted_candidate": mine.candidate_id}},
        trw_dir=fixture.trw_dir,
    )
    assert revised.member("impl-2").admitted_candidate == mine.candidate_id
    held = formation.candidate(fixture.trw_dir, mine.candidate_id)
    assert held is not None and (held.state, held.admitted_formation) == ("admitted", "release-train")


def test_a_candidate_admitted_by_another_member_in_the_same_formation_is_still_refused(
    formation_env: FormationFixture,
) -> None:
    """R9's exception must not swallow D2: a candidate ALREADY committed to a DIFFERENT
    member of this SAME formation is still refused for a second slot.
    """
    fixture = formation_env
    formation.create(fixture.orchestrator_run, _strict_payload(fixture), trw_dir=fixture.trw_dir)
    mine = _announce(fixture, "pin-b")
    formation.revise(
        "release-train",
        fixture.orchestrator_run,
        {"impl-2": {"admitted_candidate": mine.candidate_id}},
        trw_dir=fixture.trw_dir,
    )

    with pytest.raises(FormationError, match="candidate_not_admissible"):
        formation.revise(
            "release-train",
            fixture.orchestrator_run,
            {"impl-1": {"admitted_candidate": mine.candidate_id}},
            trw_dir=fixture.trw_dir,
        )


# --------------------------------------------------------------------------- #
# PRD-FIX-149 round 3 review: R10 (create() locks its exists() check and write).
# --------------------------------------------------------------------------- #


def test_concurrent_creates_for_the_same_orchestrator_run_serialize_and_refuse_the_loser(
    formation_env: FormationFixture,
) -> None:
    """R10: ``create``'s existence check and its write must happen under the SAME lock
    hold, or two concurrent creates for the same orchestrator run can both pass the
    ``exists()`` check before either writes, and the second silently clobbers the first.
    """
    import threading

    fixture = formation_env
    payload_a = _strict_payload(fixture)
    payload_b = _strict_payload(fixture)
    payload_b["formation_id"] = "release-train-b"

    outcomes: dict[str, str] = {}

    def make(name: str, payload: dict[str, Any]) -> None:
        try:
            outcomes[name] = formation.create(fixture.orchestrator_run, payload, trw_dir=fixture.trw_dir).formation_id
        except FormationError as exc:
            outcomes[name] = str(exc)

    threads = [
        threading.Thread(target=make, args=("a", payload_a)),
        threading.Thread(target=make, args=("b", payload_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    winners = [name for name, outcome in outcomes.items() if outcome in {"release-train", "release-train-b"}]
    assert len(outcomes) == 2 and len(winners) == 1, outcomes
    (loser,) = set(outcomes) - set(winners)
    assert "already exists" in outcomes[loser]

    loaded = formation.load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
    assert loaded is not None and loaded.manifest.formation_id == outcomes[winners[0]], "no partial or clobbered write"
