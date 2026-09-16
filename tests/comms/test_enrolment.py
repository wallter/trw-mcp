"""PRD-CORE-274-FR01 / FR10 — trusted caller binding.

Every case asserts on the REFUSAL VALUE, not merely that something raised. A
test that accepts any exception cannot tell "refused for the right reason" from
"crashed", and this module is the security boundary for the whole feature.

Formations are built through the real public facade (`formation.create`/`join`)
via the shared fixture, never by hand-writing a manifest — a hand-written
manifest tests the test's idea of the format rather than the writer's.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests._formation_test_support import (  # noqa: F401  (formation_env is a fixture)
    FormationFixture,
    formation_env,
    make_run_dir,
    write_pin,
)
from trw_mcp.comms import _identity
from trw_mcp.comms._identity import (
    IdentityError,
    IdentityRefusal,
    derive_group_id,
    resolve_caller,
)
from trw_mcp.formation import create, join, load


def _bind(fixture: FormationFixture, monkeypatch: pytest.MonkeyPatch, pin_key: str):
    """Resolve the caller as `pin_key` would, with no caller-supplied identity."""
    monkeypatch.setenv("TRW_SESSION_ID", pin_key)
    return resolve_caller(None, trw_dir=fixture.trw_dir, project_root=fixture.project_root)


def _refusal(fixture: FormationFixture, monkeypatch: pytest.MonkeyPatch, pin_key: str) -> IdentityRefusal:
    monkeypatch.setenv("TRW_SESSION_ID", pin_key)
    with pytest.raises(IdentityError) as excinfo:
        resolve_caller(None, trw_dir=fixture.trw_dir, project_root=fixture.project_root)
    return excinfo.value.refusal


def _formation(fixture: FormationFixture, **overrides: object):
    return create(fixture.orchestrator_run, fixture.payload(**overrides), trw_dir=fixture.trw_dir)


def test_joined_member_binds_to_exactly_one_member(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _formation(formation_env)
    run = formation_env.member_runs["impl-1"]
    join(manifest.formation_id, "impl-1", run, pin_key="pin-a", trw_dir=formation_env.trw_dir)
    write_pin(formation_env, "pin-a", run)

    binding = _bind(formation_env, monkeypatch, "pin-a")

    assert binding.member_id == "impl-1"
    assert binding.session_id == "pin-a"
    assert binding.run_path.resolve() == run.resolve()
    assert binding.group_id == derive_group_id(formation_env.project_root, formation_env.manifest_path())


def test_unpinned_session_refuses_without_touching_formation(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    _formation(formation_env)
    assert _refusal(formation_env, monkeypatch, "pin-never-written") is IdentityRefusal.NO_PIN


def test_run_outside_any_formation_refuses(formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    stray = make_run_dir(formation_env.trw_dir / "runs", "stray")
    write_pin(formation_env, "pin-stray", stray)
    assert _refusal(formation_env, monkeypatch, "pin-stray") is IdentityRefusal.NO_FORMATION


def test_declared_but_unjoined_member_has_no_formation_at_all(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared-but-unjoined member cannot bind (FR01).

    It refuses at NO_FORMATION rather than NO_MATCH, and that is the real
    contract: the run->formation link IS the join stamp, so a run that never
    joined belongs to no formation to be matched against in the first place.
    """
    _formation(formation_env)
    write_pin(formation_env, "pin-a", formation_env.member_runs["impl-1"])
    assert _refusal(formation_env, monkeypatch, "pin-a") is IdentityRefusal.NO_FORMATION


def test_two_members_sharing_run_and_pin_are_ambiguous(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambiguity must refuse, never pick the first match."""
    manifest = _formation(formation_env)
    run = formation_env.member_runs["impl-1"]
    join(manifest.formation_id, "impl-1", run, pin_key="pin-a", trw_dir=formation_env.trw_dir)
    join(manifest.formation_id, "impl-2", run, pin_key="pin-a", trw_dir=formation_env.trw_dir)
    write_pin(formation_env, "pin-a", run)
    assert _refusal(formation_env, monkeypatch, "pin-a") is IdentityRefusal.AMBIGUOUS


def test_pin_matching_a_different_run_refuses(formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """BOTH run_path and pin must match; either alone is not identity."""
    manifest = _formation(formation_env)
    run_a = formation_env.member_runs["impl-1"]
    run_b = formation_env.member_runs["impl-2"]
    join(manifest.formation_id, "impl-1", run_a, pin_key="pin-a", trw_dir=formation_env.trw_dir)
    join(manifest.formation_id, "impl-2", run_b, pin_key="pin-b", trw_dir=formation_env.trw_dir)
    write_pin(formation_env, "pin-a", run_b)  # impl-1's pin against impl-2's run
    assert _refusal(formation_env, monkeypatch, "pin-a") is IdentityRefusal.NO_MATCH


def test_orchestrator_needs_its_own_joined_member_row(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Being the orchestrator grants NO implicit privilege (FR01).

    The loader reports is_orchestrator=True and member_id=None for the owning
    run; that must not by itself authorize anything.
    """
    _formation(formation_env)
    write_pin(formation_env, "pin-orch", formation_env.orchestrator_run)
    assert _refusal(formation_env, monkeypatch, "pin-orch") is IdentityRefusal.NO_MATCH


def test_orchestrator_with_an_explicit_joined_member_binds(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an explicit member row it binds like anyone else."""
    manifest = _formation(
        formation_env,
        members=[
            {"member_id": "lead", "client": "claude-code", "role": "lead", "owned_paths": ["src/lead"]},
            {"member_id": "impl-1", "client": "codex", "role": "implementer", "owned_paths": ["src/alpha"]},
        ],
    )
    join(
        manifest.formation_id, "lead", formation_env.orchestrator_run, pin_key="pin-orch", trw_dir=formation_env.trw_dir
    )
    write_pin(formation_env, "pin-orch", formation_env.orchestrator_run)

    binding = _bind(formation_env, monkeypatch, "pin-orch")

    assert binding.member_id == "lead"
    assert binding.is_orchestrator is True


def test_same_formation_name_in_two_projects_yields_distinct_groups(tmp_path: Path) -> None:
    """FR10 isolation: the group id is derived from canonical paths, not a name."""
    a = derive_group_id(tmp_path / "proj-a", tmp_path / "proj-a" / ".trw" / "runs" / "o" / "formation.yaml")
    b = derive_group_id(tmp_path / "proj-b", tmp_path / "proj-b" / ".trw" / "runs" / "o" / "formation.yaml")
    assert a != b
    assert len(a) == 32


def test_group_id_is_stable_across_equivalent_paths(tmp_path: Path) -> None:
    """A relative route to the same manifest must not create a second group."""
    root = tmp_path / "proj"
    manifest = root / ".trw" / "runs" / "o" / "formation.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("x", encoding="utf-8")
    direct = derive_group_id(root, manifest)
    indirect = derive_group_id(root, root / ".trw" / "runs" / "o" / ".." / "o" / "formation.yaml")
    assert direct == indirect


def test_resolve_caller_accepts_no_caller_supplied_identity() -> None:
    """NEGATIVE CONTROL for the FR01 property most likely to be eroded.

    If someone adds a `member_id=` / `group_id=` / `pin_key=` parameter so a
    caller can name itself, this fails. Signature inspection is the right tool:
    the property is the ABSENCE of a parameter.
    """
    import inspect

    params = set(inspect.signature(resolve_caller).parameters)
    assert params == {"ctx", "trw_dir", "project_root"}
    forbidden = {"member_id", "group_id", "sender", "sender_member_id", "pin_key", "incarnation", "session_id"}
    assert not (params & forbidden)


def _reindex(fixture: FormationFixture, entries: dict[str, str]) -> None:
    """Rewrite the formation registry index (the file `_store` maintains)."""
    index_path = fixture.trw_dir / "runtime" / "formations.json"
    current = json.loads(index_path.read_text(encoding="utf-8"))
    current.update(entries)
    index_path.write_text(json.dumps(current), encoding="utf-8")


def test_stamped_formation_contradicting_the_manifest_refuses(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run may only bind to the formation it actually joined.

    `formation.load` follows the run's stamped formation_id through the registry
    index and returns whatever manifest that entry points at, WITHOUT checking
    the manifest carries that id. A stale or aliased index entry therefore named
    a formation the run never joined. This was an ACCEPTED case before the guard
    existed — found by a peer's executed reproducer, not by reading the source —
    so it is pinned here permanently.
    """
    manifest = _formation(formation_env)
    run = formation_env.member_runs["impl-1"]
    join(manifest.formation_id, "impl-1", run, pin_key="pin-a", trw_dir=formation_env.trw_dir)
    write_pin(formation_env, "pin-a", run)
    assert _bind(formation_env, monkeypatch, "pin-a").member_id == "impl-1"  # before

    meta = run / "meta" / "run.yaml"
    stamped = yaml.safe_load(meta.read_text(encoding="utf-8"))
    stamped["formation_id"] = "contradictory"
    meta.write_text(yaml.safe_dump(stamped), encoding="utf-8")
    _reindex(formation_env, {"contradictory": str(formation_env.orchestrator_run)})

    loaded = load(run, trw_dir=formation_env.trw_dir)
    assert loaded is not None and loaded.manifest.formation_id == manifest.formation_id
    assert _refusal(formation_env, monkeypatch, "pin-a") is IdentityRefusal.STAMP_MISMATCH


def test_formation_registered_elsewhere_refuses(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry must resolve the formation back to THIS manifest.

    Covers the orchestrator branch, which carries no stamp: it reads the
    manifest out of its own directory, so the round-trip through the index is
    the only thing proving the directory has authority.
    """
    manifest = _formation(formation_env)
    join(
        manifest.formation_id,
        "impl-1",
        formation_env.orchestrator_run,
        pin_key="pin-orch",
        trw_dir=formation_env.trw_dir,
    )
    write_pin(formation_env, "pin-orch", formation_env.orchestrator_run)
    _reindex(formation_env, {manifest.formation_id: str(formation_env.member_runs["impl-2"])})
    assert _refusal(formation_env, monkeypatch, "pin-orch") is IdentityRefusal.UNCANONICAL


def test_unregistered_formation_refuses(formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """NEGATIVE CONTROL for the round-trip check: an absent index entry must
    refuse, not fall through as "nothing to compare against"."""
    manifest = _formation(formation_env)
    join(
        manifest.formation_id,
        "impl-1",
        formation_env.orchestrator_run,
        pin_key="pin-orch",
        trw_dir=formation_env.trw_dir,
    )
    write_pin(formation_env, "pin-orch", formation_env.orchestrator_run)
    index_path = formation_env.trw_dir / "runtime" / "formations.json"
    index_path.write_text("{}", encoding="utf-8")
    assert _refusal(formation_env, monkeypatch, "pin-orch") is IdentityRefusal.UNCANONICAL


def test_manifest_naming_a_different_owner_run_refuses(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest's own declared owner must canonically locate it where it lives.

    NOT implied by the registry round-trip: the index and the loader consult the
    same pointer file, so they agree by construction, and neither reads the
    manifest's declaration of who owns it. A manifest naming a different owner
    while the index still points at the real file passed both earlier checks and
    was accepted. The manifest, not the pointer index, is the membership
    authority, so its own declaration has to agree with where it was found.
    """
    manifest = _formation(formation_env)
    run = formation_env.member_runs["impl-1"]
    join(manifest.formation_id, "impl-1", run, pin_key="pin-a", trw_dir=formation_env.trw_dir)
    write_pin(formation_env, "pin-a", run)
    assert _bind(formation_env, monkeypatch, "pin-a").member_id == "impl-1"  # before

    manifest_path = formation_env.manifest_path()
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    raw["orchestrator_run_path"] = str(formation_env.member_runs["impl-2"])
    manifest_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    assert _refusal(formation_env, monkeypatch, "pin-a") is IdentityRefusal.UNCANONICAL


@pytest.mark.parametrize("corrupt", ["stamp", "registered_elsewhere", "unregistered", "foreign_owner"])
def test_trusted_formation_guard_is_load_bearing(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch, corrupt: str
) -> None:
    """NEGATIVE CONTROL: each case above binds once the guard is neutered.

    The guard is disabled by monkeypatching it in THIS process only. Editing the
    production file to prove the same thing would expose a disarmed security
    check to every peer agent working in this shared checkout for as long as the
    edit sits on disk.

    Each case reuses the run its paired positive test pins, and that distinction
    is load-bearing rather than incidental. The two registry cases are pinned to
    the ORCHESTRATOR run because that is the only branch where this guard is the
    thing doing the work: a MEMBER run reaches its manifest THROUGH the index, so
    a broken index already refuses inside `formation.load` before the guard runs.
    Parametrizing both cases onto a member run would have produced a green
    negative control that proved the loader's behaviour, not this guard's.
    """
    manifest = _formation(formation_env)
    manifest_path = formation_env.manifest_path()
    on_orchestrator = corrupt in {"registered_elsewhere", "unregistered"}
    run = formation_env.orchestrator_run if on_orchestrator else formation_env.member_runs["impl-1"]
    join(manifest.formation_id, "impl-1", run, pin_key="pin-a", trw_dir=formation_env.trw_dir)
    write_pin(formation_env, "pin-a", run)

    if corrupt == "stamp":
        meta = run / "meta" / "run.yaml"
        stamped = yaml.safe_load(meta.read_text(encoding="utf-8"))
        stamped["formation_id"] = "contradictory"
        meta.write_text(yaml.safe_dump(stamped), encoding="utf-8")
        _reindex(formation_env, {"contradictory": str(formation_env.orchestrator_run)})
    elif corrupt == "registered_elsewhere":
        _reindex(formation_env, {manifest.formation_id: str(formation_env.member_runs["impl-2"])})
    elif corrupt == "unregistered":
        (formation_env.trw_dir / "runtime" / "formations.json").write_text("{}", encoding="utf-8")
    else:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        raw["orchestrator_run_path"] = str(formation_env.member_runs["impl-2"])
        manifest_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    expected = IdentityRefusal.STAMP_MISMATCH if corrupt == "stamp" else IdentityRefusal.UNCANONICAL
    assert _refusal(formation_env, monkeypatch, "pin-a") is expected  # guarded: refuses, for this reason

    monkeypatch.setattr(_identity, "_assert_trusted_formation", lambda *a, **k: None)
    assert _bind(formation_env, monkeypatch, "pin-a").member_id == "impl-1"  # unguarded: accepts
