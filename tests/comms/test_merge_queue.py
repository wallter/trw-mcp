"""CORE296 FR07: real Git queue stops safely and replays uncertain outcomes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env, make_run_dir, write_pin  # noqa: F401
from trw_mcp import formation
from trw_mcp.formation._merge_git import MergeGitError, apply, prepare
from trw_mcp.tools._evidence_writers import record_build_receipt
from trw_mcp.tools._formation_cli import add_formation_subcommands, run_formation


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def queue_scene(formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch) -> tuple[FormationFixture, Path]:
    fixture = formation_env
    repo = fixture.project_root
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".gitignore").write_text(".trw/\ndocs/\n", encoding="utf-8")
    (repo / "src" / "alpha").mkdir(parents=True)
    (repo / "src" / "alpha" / "a.py").write_text("x = 0\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "src/alpha/a.py")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "integration")
    _git(repo, "switch", "-qc", "feature")
    (repo / "src" / "alpha" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "src/alpha/a.py")
    _git(repo, "commit", "-qm", "feature")

    integrator_run = make_run_dir(repo / ".trw" / "runs", "integrator")
    formation.create(
        fixture.orchestrator_run,
        {
            "formation_id": "release-train",
            "members": [
                {"member_id": "lead", "client": "codex", "role": "orchestrator", "open_join": True},
                {
                    "member_id": "impl-1",
                    "client": "codex",
                    "role": "implementer",
                    "open_join": True,
                    "owned_paths": ["src/alpha"],
                },
                {"member_id": "integrator", "client": "codex", "role": "integrator", "open_join": True},
            ],
        },
        trw_dir=fixture.trw_dir,
    )
    for member, run, pin in (
        ("lead", fixture.orchestrator_run, "pin-lead"),
        ("impl-1", fixture.member_runs["impl-1"], "pin-impl"),
        ("integrator", integrator_run, "pin-integrator"),
    ):
        formation.join("release-train", member, run, pin_key=pin, trw_dir=fixture.trw_dir)
        write_pin(fixture, pin, run)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-lead")
    return fixture, integrator_run


def _receipt(fixture: FormationFixture, *, passed: bool = True) -> str:
    run = fixture.member_runs["impl-1"]
    result = record_build_receipt(
        run,
        fixture.project_root,
        tests_passed=passed,
        static_checks_clean=True,
        scope_label="unit",
        coverage_pct=None,
        policy_mode="observe",
    )
    assert result is not None and result.ok
    return result.receipt_id


def _lead(fixture: FormationFixture) -> formation.FormationContext:
    found = formation.load(fixture.orchestrator_run, trw_dir=fixture.trw_dir)
    assert found is not None
    return found


def _approved(fixture: FormationFixture, receipt_id: str) -> formation.MergeItem:
    return formation.merge_enqueue(
        _lead(fixture),
        branch="feature",
        sha=_git(fixture.project_root, "rev-parse", "HEAD"),
        member_id="impl-1",
        receipt_id=receipt_id,
    )


def test_one_owned_merge_and_exact_enqueue_replay(queue_scene: tuple[FormationFixture, Path]) -> None:
    fixture, _ = queue_scene
    receipt = _receipt(fixture)
    item = _approved(fixture, receipt)
    assert _approved(fixture, receipt).approval_id == item.approval_id
    ledger = (fixture.orchestrator_run / "meta" / "events.jsonl").read_text(encoding="utf-8")
    assert "pin-lead" not in ledger
    assert item.approver_member_id == "lead" and len(item.approver_pin_digest) == 64
    assert "pin-lead" not in repr(formation.merge_list(_lead(fixture)))
    previous = _git(fixture.project_root, "rev-parse", "integration")
    merged = formation.merge_run_one(_lead(fixture), repo=fixture.project_root, target="integration")
    assert merged.state == "merged"
    assert merged.merge_sha == _git(fixture.project_root, "rev-parse", "integration")
    assert _git(fixture.project_root, "rev-list", "--parents", "-n", "1", "integration").split()[1:] == [
        previous,
        item.sha,
    ]
    assert formation.merge_list(_lead(fixture))[0].state == "merged"


def test_integrator_pin_can_run_but_other_member_cannot(
    queue_scene: tuple[FormationFixture, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, integrator_run = queue_scene
    _approved(fixture, _receipt(fixture))
    monkeypatch.setenv("TRW_SESSION_ID", "pin-impl")
    member = formation.load(fixture.member_runs["impl-1"], trw_dir=fixture.trw_dir)
    assert member is not None
    with pytest.raises(formation.MergeQueueError, match="integrator"):
        formation.merge_run_one(member, repo=fixture.project_root, target="integration")
    with pytest.raises(formation.MergeQueueError, match="orchestrator"):
        formation.merge_enqueue(
            member,
            branch="feature",
            sha=_git(fixture.project_root, "rev-parse", "HEAD"),
            member_id="impl-1",
            receipt_id="build-" + "a" * 32,
        )
    monkeypatch.setenv("TRW_SESSION_ID", "pin-integrator")
    integrator = formation.load(integrator_run, trw_dir=fixture.trw_dir)
    assert integrator is not None
    assert formation.merge_run_one(integrator, repo=fixture.project_root, target="integration").state == "merged"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("stale_sha", "stale_sha"),
        ("unowned_path", "unowned_path"),
        ("receipt_sha_mismatch", "receipt_sha_mismatch"),
        ("dirty_tree_receipt", "receipt_sha_mismatch"),
        ("legacy_receipt", "receipt_sha_mismatch"),
        ("red_gate", "red_gate"),
    ],
)
def test_refusal_keeps_target_unchanged(queue_scene: tuple[FormationFixture, Path], failure: str, reason: str) -> None:
    fixture, _ = queue_scene
    repo = fixture.project_root
    if failure == "unowned_path":
        (repo / "outside.py").write_text("bad = 1\n", encoding="utf-8")
        _git(repo, "add", "outside.py")
        _git(repo, "commit", "-qm", "unowned")
    if failure == "dirty_tree_receipt":
        # A build run on a dirty tree binds no commit: git_sha stays None.
        (repo / "scratch.py").write_text("wip = 1\n", encoding="utf-8")
    receipt = _receipt(fixture, passed=failure != "red_gate")
    receipt_path = fixture.member_runs["impl-1"] / "meta" / "receipts" / "build" / f"{receipt}.json"
    if failure == "dirty_tree_receipt":
        (repo / "scratch.py").unlink()
        assert json.loads(receipt_path.read_text(encoding="utf-8"))["git_sha"] is None
    if failure == "legacy_receipt":
        # A receipt written before git_sha existed parses with git_sha=None.
        raw = json.loads(receipt_path.read_text(encoding="utf-8"))
        del raw["git_sha"]
        receipt_path.write_text(json.dumps(raw), encoding="utf-8")
    if failure == "receipt_sha_mismatch":
        # The only approval names a different valid SHA; the receipt binds feature HEAD.
        formation.merge_enqueue(
            _lead(fixture),
            branch="feature",
            sha=_git(repo, "rev-parse", "integration"),
            member_id="impl-1",
            receipt_id=receipt,
        )
    else:
        _approved(fixture, receipt)
    if failure == "stale_sha":
        (repo / "src" / "alpha" / "a.py").write_text("x = 2\n", encoding="utf-8")
        _git(repo, "add", "src/alpha/a.py")
        _git(repo, "commit", "-qm", "moved")
    old_target = _git(repo, "rev-parse", "integration")
    with pytest.raises((formation.MergeQueueError, MergeGitError)) as error:
        formation.merge_run_one(_lead(fixture), repo=repo, target="integration")
    assert error.value.reason == reason
    assert _git(repo, "rev-parse", "integration") == old_target
    assert formation.merge_list(_lead(fixture))[0].state == "stopped"


def test_lost_merge_ack_replays_as_unknown_not_success(
    queue_scene: tuple[FormationFixture, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, _ = queue_scene
    _approved(fixture, _receipt(fixture))
    from trw_mcp.formation import _merge_queue

    real_append = _merge_queue._append

    def lose_ack(path: Path, event: str, item: formation.MergeItem, **extra: str) -> None:
        if event == "formation_merge_merged":
            raise OSError("simulated crash after Git CAS")
        real_append(path, event, item, **extra)

    monkeypatch.setattr(_merge_queue, "_append", lose_ack)
    with pytest.raises(OSError, match="simulated crash"):
        formation.merge_run_one(_lead(fixture), repo=fixture.project_root, target="integration")
    assert formation.merge_list(_lead(fixture))[0].state == "attempted"
    with pytest.raises(formation.MergeQueueError) as error:
        formation.merge_run_one(_lead(fixture), repo=fixture.project_root, target="integration")
    assert error.value.reason == "requires_reconciliation"


def test_missing_receipt_stops_and_new_approval_can_recover(queue_scene: tuple[FormationFixture, Path]) -> None:
    fixture, _ = queue_scene
    repo = fixture.project_root
    item = _approved(fixture, "build-" + "a" * 32)
    old = _git(repo, "rev-parse", "integration")
    with pytest.raises(formation.MergeQueueError) as missing:
        formation.merge_run_one(_lead(fixture), repo=repo, target="integration")
    assert missing.value.reason == "receipt_unavailable"
    assert _git(repo, "rev-parse", "integration") == old
    assert formation.merge_list(_lead(fixture))[0].state == "stopped"
    receipt = _receipt(fixture)
    new = _approved(fixture, receipt)
    assert new.approval_id != item.approval_id
    assert formation.merge_run_one(_lead(fixture), repo=repo, target="integration").state == "merged"


def test_conflict_and_checked_out_target_never_mutate_target(queue_scene: tuple[FormationFixture, Path]) -> None:
    fixture, _ = queue_scene
    repo = fixture.project_root
    _approved(fixture, _receipt(fixture))
    old = _git(repo, "rev-parse", "integration")
    _git(repo, "switch", "integration")
    with pytest.raises(MergeGitError) as checked_out:
        formation.merge_run_one(_lead(fixture), repo=repo, target="integration")
    assert checked_out.value.reason == "target_checked_out"
    assert _git(repo, "rev-parse", "integration") == old

    # Explicit reapproval after the recorded stop; conflicting change on target.
    _git(repo, "switch", "feature")
    receipt = _receipt(fixture)
    formation.merge_enqueue(
        _lead(fixture), branch="feature", sha=_git(repo, "rev-parse", "HEAD"), member_id="impl-1", receipt_id=receipt
    )
    _git(repo, "switch", "integration")
    (repo / "src" / "alpha" / "a.py").write_text("x = 99\n", encoding="utf-8")
    _git(repo, "add", "src/alpha/a.py")
    _git(repo, "commit", "-qm", "conflicting integration change")
    conflicting_target = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "feature")
    with pytest.raises(MergeGitError) as conflict:
        formation.merge_run_one(_lead(fixture), repo=repo, target="integration")
    assert conflict.value.reason == "conflict"
    assert _git(repo, "rev-parse", "integration") == conflicting_target


def test_git_version_gate_and_cas_movement(
    queue_scene: tuple[FormationFixture, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, _ = queue_scene
    repo = fixture.project_root
    from trw_mcp.formation import _merge_git

    original = _merge_git._git
    monkeypatch.setattr(
        _merge_git,
        "_git",
        lambda root, *args, **kw: "git version 2.37.0" if args == ("--version",) else original(root, *args, **kw),
    )
    with pytest.raises(MergeGitError) as old:
        prepare(repo, "feature", _git(repo, "rev-parse", "HEAD"), "integration")
    assert old.value.reason == "git_too_old"
    monkeypatch.setattr(_merge_git, "_git", original)
    sha = _git(repo, "rev-parse", "HEAD")
    old_ref, tree, _ = prepare(repo, "feature", sha, "integration")
    _git(repo, "update-ref", "refs/heads/integration", sha, old_ref)
    with pytest.raises(MergeGitError) as moved:
        apply(repo, "feature", sha, "integration", old_ref, tree)
    assert moved.value.reason == "target_moved"


def test_public_cli_enqueue_list_run_one(
    queue_scene: tuple[FormationFixture, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture, _ = queue_scene
    receipt = _receipt(fixture)
    sha = _git(fixture.project_root, "rev-parse", "HEAD")
    parser = argparse.ArgumentParser()
    add_formation_subcommands(parser.add_subparsers(dest="command"))

    def call(*args: str) -> object:
        with pytest.raises(SystemExit) as done:
            run_formation(parser.parse_args(["formation", "merge", *args]))
        assert done.value.code == 0
        return json.loads(capsys.readouterr().out)

    approved = call("enqueue", "feature", sha, "impl-1", receipt)
    assert isinstance(approved, dict) and approved["sha"] == sha
    listed = call("list")
    assert isinstance(listed, list) and listed[0]["approval_id"] == approved["approval_id"]
    assert "pin-lead" not in repr(listed)
    merged = call("run-one", "integration")
    assert isinstance(merged, dict) and merged["state"] == "merged"


@pytest.mark.parametrize("protected", ["main", "master"])
def test_queue_never_targets_local_default_names(queue_scene: tuple[FormationFixture, Path], protected: str) -> None:
    fixture, _ = queue_scene
    repo = fixture.project_root
    with pytest.raises(MergeGitError) as stopped:
        prepare(repo, "feature", _git(repo, "rev-parse", "HEAD"), protected)
    assert stopped.value.reason == "protected_target"


def test_queue_never_targets_nonstandard_origin_default(queue_scene: tuple[FormationFixture, Path]) -> None:
    fixture, _ = queue_scene
    repo = fixture.project_root
    _git(repo, "branch", "release-default")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/release-default")
    with pytest.raises(MergeGitError) as stopped:
        prepare(repo, "feature", _git(repo, "rev-parse", "HEAD"), "release-default")
    assert stopped.value.reason == "protected_target"


def test_unreadable_default_branch_is_not_a_missing_default(
    queue_scene: tuple[FormationFixture, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture, _ = queue_scene
    from trw_mcp.formation import _merge_git

    real_run = _merge_git.subprocess.run

    def broken_origin(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if isinstance(command, list) and "symbolic-ref" in command:
            return subprocess.CompletedProcess(command, 128, "", "damaged ref")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(_merge_git.subprocess, "run", broken_origin)
    with pytest.raises(MergeGitError) as stopped:
        prepare(fixture.project_root, "feature", _git(fixture.project_root, "rev-parse", "HEAD"), "integration")
    assert stopped.value.reason == "default_branch_unavailable"
