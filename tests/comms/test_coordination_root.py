"""PRD-CORE-274-FR17: coordination root for linked git worktrees (T18 matrix).

Real git repositories, real ``git worktree add``: the back-pointer layout is
git's, not a hand-written imitation of it, so a git change that broke the
resolver would fail here rather than pass against a stale fake.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import make_run_dir, open_slot, write_pin
from tests.comms.conftest import FormationFixture, call_peers, enable_comms
from trw_mcp.comms._identity import derive_group_id
from trw_mcp.formation import (
    create,
    join,
    linked_worktree,
    load,
    record_worktree_member,
    shared_authority_root,
    worktree_record,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    (path / "README").write_text("x\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "commit", "-q", "-m", "init")
    return path.resolve()


@dataclass
class WorktreeScene:
    main: Path
    worktree: Path
    fixture: FormationFixture
    worktree_run: Path
    server: FastMCP
    monkeypatch: pytest.MonkeyPatch

    def at(self, root: Path, pin: str) -> None:
        """Become a client whose project root (and per-worktree pin store) is *root*."""
        from trw_mcp.state import _paths, _pin_store

        trw_dir = root / ".trw"
        self.monkeypatch.setattr(_paths, "resolve_project_root", lambda: root)
        self.monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: trw_dir)
        self.monkeypatch.setattr(_pin_store, "pin_store_path", lambda: trw_dir / "runtime" / "pins.json")
        self.monkeypatch.setenv("TRW_SESSION_ID", pin)
        # A real client is a separate process with its own cache. The in-process
        # read cache is keyed on mtime alone, so on a filesystem whose timestamps
        # are coarse (overlayfs) the previous root's snapshot would answer here.
        _pin_store.invalidate_pin_store_cache()

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        result = asyncio.run(self.server.call_tool(tool, args))
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    def record(self, member: str = "impl-2") -> None:
        record_worktree_member(
            self.fixture.trw_dir,
            self.worktree,
            formation_id="release-train",
            member_id=member,
            manifest_revision=1,
            creating_run=self.fixture.orchestrator_run,
        )


@pytest.fixture
def wt(tmp_path: Path, comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> WorktreeScene:
    main = _repo(tmp_path / "main")
    _git(main, "worktree", "add", "-q", str(tmp_path / "wt"))
    worktree = (tmp_path / "wt").resolve()
    trw_dir = main / ".trw"
    (main / "docs").mkdir()
    (main / "docs" / "rules.md").write_text("rules\n", encoding="utf-8")
    runs = trw_dir / "runs"
    orchestrator = make_run_dir(runs, "orchestrator")
    impl_1 = make_run_dir(runs, "impl-1")
    worktree_run = make_run_dir(worktree / ".trw" / "runs", "impl-2")
    fixture = FormationFixture(main, trw_dir, orchestrator, {"impl-1": impl_1, "impl-2": worktree_run})
    enable_comms(monkeypatch)
    formation_id = create(orchestrator, fixture.payload(), trw_dir=trw_dir).formation_id
    join(formation_id, "impl-1", impl_1, pin_key="pin-a", trw_dir=trw_dir)
    join(formation_id, "impl-2", worktree_run, pin_key="pin-b", trw_dir=trw_dir)
    write_pin(fixture, "pin-a", impl_1)
    worktree_fixture = FormationFixture(worktree, worktree / ".trw", orchestrator, {})
    write_pin(worktree_fixture, "pin-b", worktree_run)  # the worktree's OWN pin store
    return WorktreeScene(main, worktree, fixture, worktree_run, comms_server, monkeypatch)


def test_a_recorded_worktree_member_shares_the_group_and_mailbox(wt: WorktreeScene) -> None:
    wt.record()
    wt.at(wt.main, "pin-a")
    assert call_peers(wt.server, "enroll")["status"] == "ok"
    wt.at(wt.worktree, "pin-b")
    enrolled = call_peers(wt.server, "enroll")
    assert enrolled["status"] == "ok" and enrolled["member_id"] == "impl-2"
    assert not list((wt.worktree / ".trw").rglob("comms.sqlite3")), "the worktree grew its own mailbox"

    wt.at(wt.main, "pin-a")
    assert wt.call("trw_send", recipient_member_id="impl-2", request_key="k", body="across")["status"] == "ok"
    wt.at(wt.worktree, "pin-b")
    page = wt.call("trw_inbox")
    assert [item["body"] for item in page["items"]] == ["across"]
    loaded = load(wt.worktree_run, trw_dir=wt.fixture.trw_dir)
    assert loaded is not None
    assert derive_group_id(wt.main, loaded.manifest_path) == derive_group_id(wt.main, wt.fixture.manifest_path())


def test_stall_status_uses_shared_authority_root_from_linked_worktree(
    wt: WorktreeScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worktree's own root must not hide main-root pending mail."""
    import sqlite3

    from trw_mcp import formation
    from trw_mcp.comms._store import database_path

    wt.record()
    wt.at(wt.main, "pin-a")
    assert call_peers(wt.server, "enroll")["status"] == "ok"
    wt.at(wt.worktree, "pin-b")
    assert call_peers(wt.server, "enroll")["status"] == "ok"
    wt.at(wt.main, "pin-a")
    assert wt.call("trw_send", recipient_member_id="impl-2", request_key="stall", body="body")["status"] == "ok"
    db = database_path(wt.fixture.manifest_path())
    with sqlite3.connect(db) as conn:
        admitted = float(conn.execute("SELECT admitted_at FROM admissions WHERE request_key='stall'").fetchone()[0])
        conn.execute("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (admitted + 601,))
    wt.at(wt.worktree, "pin-b")
    monkeypatch.setattr("trw_mcp.formation._views.time.time", lambda: admitted + 601)
    board = formation.status(run_path=wt.worktree_run, trw_dir=wt.fixture.trw_dir)
    assert board is not None and board.stall_measurement == "measured"
    assert any(item.member_id == "impl-2" and item.pending == 1 for item in board.stalls)
    shared = formation.shared_authority_root()
    assert shared is not None
    assert formation.stamped_ids(wt.worktree_run) == (shared[1].formation_id, shared[1].member_id)
    default_board = formation.status(run_path=wt.worktree_run)
    assert default_board is not None and default_board.stall_measurement == "measured"
    assert any(item.member_id == "impl-2" and item.pending == 1 for item in default_board.stalls)


def test_a_recorded_worktree_member_loads_and_delivers_with_the_default_store(wt: WorktreeScene) -> None:
    """Release-verify Q4: with no explicit trw_dir, a member in a linked worktree must
    resolve the main root's formation, or its deliver gate fails closed on "unknown formation_id"."""
    from trw_mcp.formation import FormationError, mark_member_delivered
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate

    wt.at(wt.worktree, "pin-b")
    with pytest.raises(FormationError, match="unknown formation_id"):
        load(wt.worktree_run)  # unrecorded: the worktree's own index does not list it

    wt.record()
    context = load(wt.worktree_run)
    assert context is not None
    assert (context.manifest.formation_id, context.member_id) == ("release-train", "impl-2")
    assert context.manifest_path == wt.fixture.manifest_path()
    assert evaluate_formation_gate(wt.worktree_run).should_block is False

    manifest = mark_member_delivered(wt.worktree_run)
    assert manifest is not None
    reloaded = load(wt.fixture.orchestrator_run, trw_dir=wt.fixture.trw_dir)
    assert reloaded is not None
    assert {entry.member_id: entry.status for entry in reloaded.manifest.members}["impl-2"] == "delivered"


def test_the_authority_store_leaves_orchestrators_and_locally_listed_formations_alone(wt: WorktreeScene) -> None:
    """The main root is chosen only for a recorded member whose formation the own index does not list."""
    from trw_mcp.formation import authority_trw_dir, stamped_ids
    from trw_mcp.formation._store import register_formation

    wt.record()
    wt.at(wt.worktree, "pin-b")
    assert authority_trw_dir(wt.worktree_run) == wt.fixture.trw_dir
    # A run that holds its own manifest is an orchestrator: its run.yaml is not even read.
    assert authority_trw_dir(wt.fixture.orchestrator_run) is None
    # A formation of the same id registered in the worktree's own store stays local.
    register_formation(wt.worktree / ".trw", "release-train", wt.worktree_run)
    assert authority_trw_dir(wt.worktree_run) is None
    # An undecodable run.yaml is an unreadable stamp, not a crash.
    (wt.worktree_run / "meta" / "run.yaml").write_bytes(b"formation_id: \xff\xfe\n")
    assert stamped_ids(wt.worktree_run) is None


def test_an_unrecorded_worktree_keeps_its_own_root(wt: WorktreeScene) -> None:
    """Negative control for the test above: a consistent back-pointer alone grants nothing."""
    assert linked_worktree(wt.worktree) == (wt.main, wt.worktree)
    wt.at(wt.worktree, "pin-b")
    refused = call_peers(wt.server, "enroll")
    # The pre-amendment answer: the run is stamped for a formation its own root does not index.
    assert (refused["status"], refused["reason"]) == ("refused", "formation_unavailable")


def test_a_record_for_another_member_does_not_bind_this_caller(wt: WorktreeScene) -> None:
    """With a record, the main root is the only answer: no silent fall back to the own root (C review D1)."""
    wt.record(member="impl-1")
    wt.at(wt.worktree, "pin-b")
    refused = call_peers(wt.server, "enroll")
    assert (refused["status"], refused["reason"]) == ("refused", "worktree_record_unbound")


def test_a_recorded_worktree_whose_bind_fails_reports_the_main_root_cause(wt: WorktreeScene) -> None:
    from trw_mcp import formation

    wt.record()
    formation.revise(
        "release-train", wt.fixture.orchestrator_run, {"impl-2": {"status": "abandoned"}}, trw_dir=wt.fixture.trw_dir
    )
    wt.at(wt.worktree, "pin-b")
    refused = call_peers(wt.server, "enroll")
    assert (refused["status"], refused["reason"]) == ("refused", "member_not_eligible")


def test_a_relative_paths_worktree_is_linked_and_its_moved_copy_is_not(tmp_path: Path) -> None:
    """git >= 2.48 can write relative back-pointers; each resolves against its own file's directory."""
    main = _repo(tmp_path / "rel-main")
    added = subprocess.run(
        ["git", "worktree", "add", "-q", "--relative-paths", str(tmp_path / "rel-wt")],
        cwd=main,
        capture_output=True,
        check=False,
    )
    if added.returncode != 0:
        pytest.skip("this git has no --relative-paths")
    worktree = (tmp_path / "rel-wt").resolve()
    assert not (worktree / ".git").read_text(encoding="utf-8").split(":", 1)[1].strip().startswith("/")
    assert linked_worktree(worktree) == (main, worktree)
    moved = worktree.parent / "rel-wt-moved"
    worktree.rename(moved)
    assert linked_worktree(moved) is None


def test_the_record_is_revisioned_and_owner_only(wt: WorktreeScene) -> None:
    import stat

    wt.record()
    wt.record()
    record = worktree_record(wt.fixture.trw_dir, wt.worktree)
    assert record is not None and (record.member_id, record.revision) == ("impl-2", 2)
    records_file = wt.fixture.trw_dir / "runtime" / "worktree-members.json"
    assert stat.S_IMODE(records_file.stat().st_mode) == 0o600


def _copied(wt: WorktreeScene) -> Path:
    copy = wt.worktree.parent / "wt-copy"
    shutil.copytree(wt.worktree, copy, symlinks=True)
    return copy


def _moved(wt: WorktreeScene) -> Path:
    moved = wt.worktree.parent / "wt-moved"
    wt.worktree.rename(moved)
    return moved


def _deleted_admin(wt: WorktreeScene) -> Path:
    shutil.rmtree(wt.main / ".git" / "worktrees" / "wt")
    return wt.worktree


def _submodule_shape(wt: WorktreeScene) -> Path:
    """A ``.git`` FILE pointing into ``.git/modules/`` -- a submodule, not a linked worktree."""
    sub = wt.main / "vendor" / "lib"
    sub.mkdir(parents=True)
    modules = wt.main / ".git" / "modules" / "lib"
    shutil.copytree(wt.main / ".git" / "worktrees" / "wt", modules)
    (sub / ".git").write_text(f"gitdir: {modules}\n", encoding="utf-8")
    return sub


def _nested_repo(wt: WorktreeScene) -> Path:
    return _repo(wt.worktree / "nested")


def _another_repository(wt: WorktreeScene) -> Path:
    other = _repo(wt.main.parent / "other")
    _git(other, "worktree", "add", "-q", str(wt.main.parent / "other-wt"))
    # A copied marker: the main root's record store, dropped into the other repository.
    (other / ".trw" / "runtime").mkdir(parents=True)
    shutil.copy(wt.fixture.trw_dir / "runtime" / "worktree-members.json", other / ".trw" / "runtime")
    return (wt.main.parent / "other-wt").resolve()


def _bare_repository(wt: WorktreeScene) -> Path:
    bare = wt.main.parent / "bare.git"
    _git(wt.main.parent, "clone", "-q", "--bare", str(wt.main), str(bare))
    _git(bare, "worktree", "add", "-q", str(wt.main.parent / "bare-wt"))
    return (wt.main.parent / "bare-wt").resolve()


@pytest.mark.parametrize(
    "make",
    [_copied, _moved, _deleted_admin, _submodule_shape, _nested_repo, _another_repository, _bare_repository],
    ids=lambda f: f.__name__.strip("_"),
)
def test_foreign_or_inconsistent_checkouts_never_reach_the_main_root(wt: WorktreeScene, make: Any) -> None:
    wt.record()
    root = make(wt)
    from trw_mcp.formation import CoordinationRoot

    assert shared_authority_root(CoordinationRoot(root, root / ".trw")) is None
    if make is _another_repository:
        linked = linked_worktree(root)
        assert linked is not None and linked[0] != wt.main, "the other repository coordinates only with itself"
    else:
        assert linked_worktree(root) is None


def test_the_lean_lineage_probe_follows_the_record_from_a_worktree(wt: WorktreeScene) -> None:
    """Lane B's driver may run in the worktree: the probe resolves the main root only via the record."""
    from trw_mcp.comms import _hint

    wt.at(wt.worktree, "pin-b")
    assert _hint.pin_lineage(pin_key="pin-b", formation_id="release-train", member_id="impl-2") is None
    wt.record()
    found = _hint.pin_lineage(pin_key="pin-b", formation_id="release-train", member_id="impl-2")
    assert found is not None and (found.pin_matches, found.pin_run_matches) == (True, True)
    assert found.member_run == str(wt.worktree_run)
    assert _hint.pin_lineage(pin_key="pin-b", formation_id="release-train", member_id="impl-1") is None


def test_a_member_bound_at_the_main_root_records_only_its_own_worktree(wt: WorktreeScene) -> None:
    """Lane B's helper path: the record's member comes from the binding, never from an argument."""
    from trw_mcp.comms import record_own_worktree

    wt.at(wt.main, "pin-a")  # impl-1, bound at the main root
    assert record_own_worktree(wt.worktree) == {"status": "ok", "member_id": "impl-1", "revision": 1}
    record = worktree_record(wt.fixture.trw_dir, wt.worktree)
    assert record is not None and record.member_id == "impl-1"

    other = _repo(wt.main.parent / "elsewhere")
    assert record_own_worktree(other)["reason"] == "worktree_not_linked_here"
    wt.at(wt.worktree, "pin-b")  # a caller inside a linked worktree cannot write records
    assert record_own_worktree(wt.worktree)["reason"] == "not_main_root"
    wt.at(wt.main, "pin-unknown")
    assert record_own_worktree(wt.worktree)["status"] == "refused"


def test_a_worktree_client_announces_first_is_admitted_and_exchanges_messages(
    tmp_path: Path, comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR17 + FR18 acceptance: announce from a worktree BEFORE the formation exists, then admission,
    then pick-up on the next call, then a message delivered across the worktree boundary."""
    main = _repo(tmp_path / "main")
    _git(main, "worktree", "add", "-q", str(tmp_path / "wt"))
    worktree = (tmp_path / "wt").resolve()
    trw_dir = main / ".trw"
    (main / "docs").mkdir()
    (main / "docs" / "rules.md").write_text("rules\n", encoding="utf-8")
    orchestrator = make_run_dir(trw_dir / "runs", "orchestrator")
    worktree_run = make_run_dir(worktree / ".trw" / "runs", "impl-2")
    fixture = FormationFixture(main, trw_dir, orchestrator, {})
    scene = WorktreeScene(main, worktree, fixture, worktree_run, comms_server, monkeypatch)
    enable_comms(monkeypatch)
    write_pin(FormationFixture(worktree, worktree / ".trw", orchestrator, {}), "pin-b", worktree_run)
    write_pin(fixture, "pin-orch", orchestrator)

    scene.at(worktree, "pin-b")
    handle = scene.call("trw_inbox", action="announce")["candidate_id"]  # no formation exists yet

    scene.at(main, "pin-orch")
    create(
        orchestrator,
        {
            **fixture.payload(),
            "members": [
                open_slot("lead", "claude-code"),
                {"member_id": "impl-2", "client": "codex"},
            ],
        },
        trw_dir=trw_dir,
    )
    join("release-train", "lead", orchestrator, pin_key="pin-orch", trw_dir=trw_dir)
    seen = scene.call("trw_inbox", action="discover")
    assert [(c["candidate_id"], c["worktree"]) for c in seen["candidates"]] == [(handle, "wt")]
    from trw_mcp import formation

    formation.revise("release-train", orchestrator, {"impl-2": {"admitted_candidate": handle}}, trw_dir=trw_dir)
    record = worktree_record(trw_dir, worktree)
    assert record is not None and record.member_id == "impl-2", "admission must write the FR17 record"
    assert scene.call("trw_inbox", action="enroll")["status"] == "ok"

    scene.at(worktree, "pin-b")
    picked = scene.call("trw_inbox", action="list")
    assert (picked["status"], picked["member_id"]) == ("ok", "impl-2")
    scene.at(main, "pin-orch")
    assert scene.call("trw_send", recipient_member_id="impl-2", request_key="k", body="welcome")["status"] == "ok"
    scene.at(worktree, "pin-b")
    assert [item["body"] for item in scene.call("trw_inbox")["items"]] == ["welcome"]
    assert not list((worktree / ".trw").rglob("comms.sqlite3"))
