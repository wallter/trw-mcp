"""FB-01-KI1-RACE r6 (lead ruling): a refused restore's only copy lives in $TMPDIR, which macOS purges.

The error must name it with a copy-now command, a retry record must reach the TRW user directory, and the
next update-project must move the copy into .trw/trash and say so.
"""

from __future__ import annotations

import errno
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REL = ".claude/settings.json"
_CLEANUP: list[Path] = []


@pytest.fixture(autouse=True)
def _remove_snapshots():  # type: ignore[no-untyped-def]
    yield
    while _CLEANUP:
        path = _CLEANUP.pop()
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "user"))

    from trw_mcp.bootstrap._refused_restore import new_snapshot_dir, release_snapshot

    root = tmp_path / "project"
    snap = new_snapshot_dir(root)  # where update-project creates it: the one place a retry record is trusted for
    release_snapshot(snap)  # the update that made it has ended (a live one keeps its liveness lock)
    _CLEANUP.append(snap)  # it sits in the real temp dir; the autouse fixture removes it
    (root / ".claude").mkdir(parents=True)
    (snap / ".claude").mkdir(parents=True)
    (snap / REL).write_bytes(b'{"user": "pre-update edit"}\n')
    return root, snap


def test_a_refused_rollback_names_the_copy_and_records_a_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _update_project
    from trw_mcp.bootstrap._refused_restore import UnsavedPreUpdate

    root, snap = _project(tmp_path, monkeypatch)
    monkeypatch.setattr(_update_project, "_snapshot_transaction_paths", lambda _root: snap)

    def full_disk_rollback(_root: Path, _snap: Path, _result: dict[str, list[str]]) -> None:
        raise UnsavedPreUpdate(errno.ENOSPC, [REL])

    def fail(*_a: object, **_k: object) -> None:
        raise RuntimeError("writer failed")

    monkeypatch.setattr(_update_project, "_run_core_update_phases", fail)
    monkeypatch.setattr(_update_project, "_rollback", full_disk_rollback)
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "preserved": []}
    _update_project._apply_update(root, root, result, ide=None, on_progress=None, dirty=None, reprovision=None)

    line = next(e for e in result["errors"] if "outside the project: free some space on" in e)
    assert str(snap / REL) in line and "free some space on" in line
    command = shlex.split(line.split("then run: ", 1)[1].split(" (or copy it off", 1)[0])
    assert command[:2] == ["cp", "-n"], "the printed copy must never clobber"
    subprocess.run(command, check=True)  # once space is free, the command works as printed
    pre = root / (REL + ".pre-update")
    assert pre.read_bytes() == (snap / REL).read_bytes()
    pre.write_bytes(b"a later edit\n")
    subprocess.run(command, check=False)  # run again: cp -n leaves an existing copy alone
    assert pre.read_bytes() == b"a later edit\n", "the printed command overwrote an existing copy"
    assert list((tmp_path / "user" / "refused-restores").glob("*.json")), "no retry record was written"


def test_the_next_update_moves_the_copy_into_trash_and_drops_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    root, snap = _project(tmp_path, monkeypatch)
    want = (snap / REL).read_bytes()
    record = record_refused(root, snap, [REL])
    assert record is not None and record.exists()
    notes: list[str] = []
    retry_refused(root, notes)

    moved = [p for p in (root / ".trw" / "trash").rglob("*") if p.is_file() and p.read_bytes() == want]
    assert moved and any(str(moved[0]) in n and "moved your pre-update version" in n for n in notes), notes
    assert not record.exists() and not snap.exists()


def test_a_retry_that_is_still_refused_keeps_the_record_and_repeats_the_copy_now_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _restore_proof
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    root, snap = _project(tmp_path, monkeypatch)
    record = record_refused(root, snap, [REL])
    monkeypatch.setattr(_restore_proof, "save_payload_in_trash", lambda *_a, **_k: None)
    notes: list[str] = []
    retry_refused(root, notes)
    assert record is not None and record.exists() and (snap / REL).exists()
    assert any("free some space on" in n for n in notes), notes


def test_a_retry_ignores_another_projects_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    root, snap = _project(tmp_path, monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    record = record_refused(other, snap, [REL])
    retry_refused(root, [])
    assert record is not None and record.exists() and (snap / REL).exists()
    shutil.rmtree(snap)


@pytest.mark.parametrize("rel", ["../secret.txt", "/etc/hosts", ".claude/../../secret.txt", ""])
def test_a_record_with_an_unsafe_rel_moves_nothing_and_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str
) -> None:
    """Lead r6 HIGH: a record is untrusted; a rel that leaves the snapshot or the project is never acted on."""
    import json
    import os

    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    root, snap = _project(tmp_path, monkeypatch)
    # snap sits directly in the temp dir, so "../secret.txt" would name a file there: plant it, remove it after.
    secret = snap.parent / "secret.txt"
    secret.write_bytes(b"outside\n")
    _CLEANUP.append(secret)
    record = record_refused(root, snap, [REL])
    assert record is not None
    record.write_text(json.dumps({"v": 1, "project": os.path.realpath(root), "snapshot": str(snap), "rels": [rel]}))
    notes: list[str] = []
    retry_refused(root, notes)

    trash = root / ".trw" / "trash"
    assert not trash.exists() or not [p for p in trash.rglob("*") if p.is_file()], "an unsafe rel was copied"
    assert not record.exists() and (snap / REL).exists(), "the record must be set aside and the snapshot left alone"
    assert any("not an update snapshot" in n or "was not trusted" in n for n in notes), notes
    shutil.rmtree(snap)


def test_a_damaged_record_is_set_aside_not_retried_forever(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    root, snap = _project(tmp_path, monkeypatch)
    record = record_refused(root, snap, [REL])
    assert record is not None
    record.write_text("[1, 2]")
    notes: list[str] = []
    retry_refused(root, notes)
    assert not record.exists() and list(record.parent.glob(record.name + ".bad.*")), "set aside under a unique name"
    assert any("is damaged; it was set aside as" in n for n in notes), notes
    shutil.rmtree(snap)


def test_update_snapshots_live_in_the_trw_user_directory_not_tmpdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead r7 MEDIUM 2: a durable, single location, so a record validates the same way on every run."""
    import tempfile

    from trw_mcp.bootstrap._update_transaction import _snapshot_transaction_paths

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "user"))
    root = tmp_path / "project"
    (root / ".claude").mkdir(parents=True)
    snap = _snapshot_transaction_paths(root)
    _CLEANUP.append(snap)
    assert snap.parent == tmp_path / "user" / "update-snapshots", snap
    assert Path(tempfile.gettempdir()) not in snap.parents


def test_an_orphaned_snapshot_of_this_project_is_named_on_the_next_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead r8 L5: on a full volume the retry record itself may not be written; the next update names the
    snapshot it left (this project's, old enough not to be a running update's) instead of losing track of it."""
    import os
    import time

    from trw_mcp.bootstrap._refused_restore import new_snapshot_dir, retry_refused

    root, orphan = _project(tmp_path, monkeypatch)  # a snapshot of root with REL in it, and no record
    other = new_snapshot_dir(tmp_path / "another-project")
    _CLEANUP.append(other)
    (other / REL).parent.mkdir(parents=True)
    (other / REL).write_bytes(b"theirs\n")
    fresh = new_snapshot_dir(root)  # a running update's snapshot: too new to be called orphaned
    _CLEANUP.append(fresh)
    (fresh / REL).parent.mkdir(parents=True)
    (fresh / REL).write_bytes(b"in flight\n")
    old = time.time() - 3600
    for snap in (orphan, other):
        os.utime(snap, (old, old))

    notes: list[str] = []
    retry_refused(root, notes)
    named = [n for n in notes if "left its pre-update copies in" in n]
    assert len(named) == 1 and str(orphan) in named[0] and REL in named[0], notes
    assert (orphan / REL).exists(), "an orphan is named, never moved or deleted"


def test_an_unusable_user_dir_falls_back_and_the_update_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead r8 M1 + L3: the user dir is refused by its trust check (not an OSError); the snapshot falls back to
    the temporary folder, is never trusted by a record, and the update result says so."""
    from trw_memory.exceptions import UntrustedDirectoryError

    from trw_mcp.bootstrap import _refused_restore, _update_project

    def refused(*, create: bool) -> Path:
        raise UntrustedDirectoryError("refused for the test", path=str(tmp_path / "user"))

    monkeypatch.setattr(_refused_restore, "_snapshots_dir", refused)
    monkeypatch.setattr(_refused_restore, "_records_dir", refused)
    root = tmp_path / "project"
    (root / ".claude").mkdir(parents=True)
    snap = _refused_restore.new_snapshot_dir(root)
    _CLEANUP.append(snap)
    assert not _refused_restore.is_durable_snapshot(snap)

    notes: list[str] = []
    _refused_restore.retry_refused(root, notes)  # must not raise
    assert any("TRW user directory is unusable" in n for n in notes), notes

    monkeypatch.setattr(_update_project, "_snapshot_transaction_paths", lambda _root: snap)
    monkeypatch.setattr(_update_project, "_run_core_update_phases", lambda *a, **k: None)
    result: dict[str, list[str]] = {"errors": [], "warnings": [], "preserved": []}
    try:
        _update_project._apply_update(root, root, result, ide=None, on_progress=None, dirty=None, reprovision=None)
    except Exception:  # trw-fail-silent-allow: later phases may fail on this bare tree; only the warning is asserted
        pass
    assert any("snapshot is in the temporary folder" in w for w in result["warnings"]), result


def test_a_fully_recovered_snapshot_is_removed_with_every_copy_it_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-143: after the last refused copy is recovered the whole snapshot goes, including the copy of
    .trw/config.yaml (it can hold platform_api_key) that every update snapshot carries."""
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    root, snap = _project(tmp_path, monkeypatch)
    (snap / ".trw").mkdir()
    (snap / ".trw" / "config.yaml").write_text("platform_api_key: secret\n")  # restored by the rollback already
    (root / ".trw").mkdir()
    (root / ".trw" / "config.yaml").write_text("platform_api_key: secret\n")  # what the rollback put back
    record = record_refused(root, snap, [REL])
    assert record is not None
    notes: list[str] = []
    retry_refused(root, notes)

    assert not snap.exists(), f"a recovered snapshot (with a config.yaml copy) was left behind: {notes}"
    assert not record.exists()


def test_an_orphan_whose_files_are_all_back_in_the_project_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-143 (2): with no record, a snapshot holding nothing the project lacks is removed, not kept forever."""
    import os
    import time

    from trw_mcp.bootstrap._refused_restore import retry_refused

    root, orphan = _project(tmp_path, monkeypatch)  # REL in the snapshot
    (root / REL).write_bytes((orphan / REL).read_bytes())  # the project already holds the same bytes
    old = time.time() - 3600
    os.utime(orphan, (old, old))
    notes: list[str] = []
    retry_refused(root, notes)
    assert not orphan.exists(), notes
    assert any("removed" in n and str(orphan) in n for n in notes), notes


def test_an_orphan_holding_a_file_the_project_lacks_is_kept_and_names_only_that_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    from trw_mcp.bootstrap._refused_restore import retry_refused

    root, orphan = _project(tmp_path, monkeypatch)
    (orphan / ".trw").mkdir()
    (orphan / ".trw" / "config.yaml").write_text("same\n")
    (root / ".trw").mkdir()
    (root / ".trw" / "config.yaml").write_text("same\n")  # back in the project: not worth naming
    old = time.time() - 3600
    os.utime(orphan, (old, old))
    notes: list[str] = []
    retry_refused(root, notes)
    named = [n for n in notes if "left its pre-update copies in" in n]
    assert orphan.exists() and len(named) == 1, notes
    assert REL in named[0] and ".trw/config.yaml" not in named[0], named[0]


def test_a_recovered_record_never_drops_a_snapshot_still_holding_a_file_the_project_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-143 safety: the snapshot goes only when nothing else in it is missing from the project."""
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    root, snap = _project(tmp_path, monkeypatch)
    (snap / ".claude" / "other.json").write_bytes(b"not in the project\n")
    record = record_refused(root, snap, [REL])
    assert record is not None
    notes: list[str] = []
    retry_refused(root, notes)
    assert (snap / ".claude" / "other.json").exists(), notes
    assert any("kept" in n and ".claude/other.json" in n for n in notes), notes


def test_an_update_snapshot_folder_is_private_to_the_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-143 (3): the snapshot must hold .trw/config.yaml (the update writes it, so a rollback restores it),
    but no other user can read into it, and its files keep their own modes so a restore never changes them."""
    import stat

    _root, snap = _project(tmp_path, monkeypatch)
    assert stat.S_IMODE(snap.stat().st_mode) == 0o700


def test_a_running_updates_snapshot_is_never_treated_as_an_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-143 r2 (codex B3): age alone is no proof. An update that runs longer than the orphan age still
    holds its snapshot's liveness lock, so another update neither names nor removes its rollback snapshot."""
    import os
    import time

    from trw_mcp.bootstrap._refused_restore import new_snapshot_dir, release_snapshot, retry_refused

    root, _done = _project(tmp_path, monkeypatch)
    live = new_snapshot_dir(root)  # held by "this" update, still running
    _CLEANUP.append(live)
    (live / REL).parent.mkdir(parents=True)
    (live / REL).write_bytes(b"in flight\n")
    (root / REL).write_bytes(b"in flight\n")  # identical in the project: removable by content alone
    old = time.time() - 3600
    os.utime(live, (old, old))
    notes: list[str] = []
    retry_refused(root, notes)
    assert live.exists() and (live / REL).exists(), notes
    assert not any(str(live) in n for n in notes), notes
    release_snapshot(live)


def test_a_fifo_in_a_snapshot_never_stalls_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-143 r2 (codex KI2): a FIFO in place of a copied file is not read; the snapshot is kept, named."""
    import os
    import threading
    import time

    from trw_mcp.bootstrap._refused_restore import retry_refused

    root, orphan = _project(tmp_path, monkeypatch)
    os.mkfifo(orphan / ".claude" / "pipe")
    old = time.time() - 3600
    os.utime(orphan, (old, old))
    notes: list[str] = []
    worker = threading.Thread(target=retry_refused, args=(root, notes), daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), "the orphan check blocked on a FIFO"
    assert orphan.exists() and any(".claude/pipe" in n for n in notes), notes


def test_a_snapshots_folder_swapped_after_the_checks_never_deletes_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-143 r2 (codex B2): the old drop deleted by pathname after its descriptors closed, so a snapshots
    folder replaced by a symlink to an outside folder holding the same snapshot name was deleted there."""
    import os

    from trw_mcp.bootstrap import _refused_restore as rr

    root, snap = _project(tmp_path, monkeypatch)
    (root / REL).write_bytes((snap / REL).read_bytes())  # all back in the project: the drop goes ahead
    outside = tmp_path / "outside"
    (outside / snap.name / ".claude").mkdir(parents=True)
    (outside / snap.name / ".claude" / "keep.txt").write_bytes(b"not TRW's\n")
    real_live = rr._is_live
    fired = {"n": 0}

    def swap_after_checks(sfd: int) -> bool:
        live = real_live(sfd)
        if not fired["n"]:
            fired["n"] += 1
            base = snap.parent
            base.rename(base.with_name(base.name + "-moved"))
            base.symlink_to(outside)
        return live

    monkeypatch.setattr(rr, "_is_live", swap_after_checks)
    notes: list[str] = []
    rr._drop_snapshot(snap, os.path.realpath(root), notes)
    assert fired["n"] == 1
    assert (outside / snap.name / ".claude" / "keep.txt").read_bytes() == b"not TRW's\n", notes
    assert sorted(p.name for p in outside.iterdir()) == [snap.name], "nothing may be created outside either"


def _swap_capture_after_verify(
    monkeypatch: pytest.MonkeyPatch, base: Path, inside: str, victim: Path
) -> dict[str, int]:
    """After the captured snapshot is verified, move it aside inside its capture folder and put *victim* (a
    folder of unique bytes) at ``s/<inside>`` (or at ``s`` itself when *inside* is empty)."""
    from trw_mcp.bootstrap import _refused_restore as rr

    real_missing = rr._missing_by_fd
    fired = {"n": 0}

    def verify_then_swap(sfd: int, project: Path, prefix: str = "", **kw: object) -> list[str]:
        left = real_missing(sfd, project, prefix, **kw)  # type: ignore[arg-type]
        if not fired["n"] and not prefix:
            fired["n"] += 1
            (capture,) = [p for p in base.iterdir() if p.name.startswith(".trw-dropping-")]
            if inside:
                (capture / "s" / inside).rename(capture / "aside")
                victim.rename(capture / "s" / inside)
            else:
                (capture / "s").rename(capture / "aside")
                victim.rename(capture / "s")
        return left

    monkeypatch.setattr(rr, "_missing_by_fd", verify_then_swap)
    return fired


@pytest.mark.parametrize("inside", ["", ".claude"], ids=["capture-root", "nested-folder"])
def test_a_folder_swapped_in_after_the_verify_is_never_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inside: str
) -> None:
    """E2E-INC-143 r3 (codex r2 block): the drop reopened capture/s by name after verifying its inode, so a folder
    moved into that name between the two had its only copy of unrelated files deleted."""
    import os

    from trw_mcp.bootstrap import _refused_restore as rr

    root, snap = _project(tmp_path, monkeypatch)
    (root / REL).write_bytes((snap / REL).read_bytes())  # all back in the project: the drop goes ahead
    victim = tmp_path / "victim"
    (victim / "sub").mkdir(parents=True)
    (victim / "unique.txt").write_bytes(b"only copy\n")
    (victim / "sub" / "deep.txt").write_bytes(b"only copy, deeper\n")
    fired = _swap_capture_after_verify(monkeypatch, snap.parent, inside, victim)
    notes: list[str] = []
    rr._drop_snapshot(snap, os.path.realpath(root), notes)
    assert fired["n"] == 1
    found = {p.read_bytes() for p in snap.parent.rglob("*") if p.is_file() and not p.is_symlink()}
    assert {b"only copy\n", b"only copy, deeper\n"} <= found, notes


def test_a_failed_capture_names_where_the_snapshot_really_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-143 r3 (codex r2 KI): a capture folder that could not be made was named as the snapshot's location."""
    import os

    from trw_mcp.bootstrap import _refused_restore as rr

    root, snap = _project(tmp_path, monkeypatch)
    (root / REL).write_bytes((snap / REL).read_bytes())
    real_mkdir = os.mkdir

    def full_disk(path, *a, **k):  # type: ignore[no-untyped-def]
        if str(path).startswith(".trw-dropping-"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_mkdir(path, *a, **k)

    monkeypatch.setattr(rr.os, "mkdir", full_disk)
    notes: list[str] = []
    assert rr._drop_snapshot(snap, os.path.realpath(root), notes) is False
    assert snap.is_dir() and any(f"it is at {snap}:" in n for n in notes), notes


def _captured(base: Path) -> Path:
    (capture,) = [p for p in base.iterdir() if p.name.startswith(".trw-dropping-")]
    return capture / "s" / ".claude"


def test_a_file_swapped_only_for_the_compare_is_never_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-143 r3 (luna blocker): the compare recorded a file's identity by stat, then read its bytes by name, so
    bytes swapped in only for the read (matching the project) vouched for the real file, which was then deleted."""
    import os

    from trw_mcp.bootstrap import _refused_restore as rr
    from trw_mcp.bootstrap import _snapshot_fd as sf

    root, snap = _project(tmp_path, monkeypatch)
    (root / REL).write_bytes(b'{"project": "now"}\n')  # the snapshot's bytes are its ONLY copy
    real_open = os.open
    state = {"swapped": 0, "restored": 0}

    def swapping_open(path, flags, *a, **k):  # type: ignore[no-untyped-def]
        if path == "settings.json" and k.get("dir_fd") is not None and not state["swapped"]:
            state["swapped"] = 1
            here = _captured(snap.parent)
            (here / "settings.json").rename(here / "real.aside")
            (here / "settings.json").write_bytes((root / REL).read_bytes())
        elif path == str(root / REL) and state["swapped"] and not state["restored"]:
            state["restored"] = 1
            here = _captured(snap.parent)
            os.replace(here / "real.aside", here / "settings.json")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(sf.os, "open", swapping_open)
    notes: list[str] = []
    rr._drop_snapshot(snap, os.path.realpath(root), notes)
    monkeypatch.undo()
    assert state == {"swapped": 1, "restored": 1}
    found = {p.read_bytes() for p in snap.parent.rglob("*") if p.is_file() and not p.is_symlink()}
    assert b'{"user": "pre-update edit"}\n' in found, notes


def test_a_file_replaced_right_before_the_delete_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-143 r3 (luna major): a file renamed in under a verified name just before TRW's act on that name
    (the unlink, or the move that precedes it) must never be deleted."""
    import os

    from trw_mcp.bootstrap import _refused_restore as rr
    from trw_mcp.bootstrap import _snapshot_fd as sf

    root, snap = _project(tmp_path, monkeypatch)
    (root / REL).write_bytes((snap / REL).read_bytes())  # all back in the project: the drop goes ahead
    victim = tmp_path / "victim.json"
    victim.write_bytes(b"someone else's only copy\n")
    fired = {"n": 0}

    def swap_first(real):  # type: ignore[no-untyped-def]
        def act(src, *a, **k):  # type: ignore[no-untyped-def]
            if src == "settings.json" and not fired["n"] and (k.get("dir_fd") or k.get("src_dir_fd")) is not None:
                fired["n"] += 1
                os.replace(victim, _captured(snap.parent) / "settings.json")
            return real(src, *a, **k)

        return act

    monkeypatch.setattr(sf.os, "unlink", swap_first(os.unlink))
    monkeypatch.setattr(sf.os, "rename", swap_first(os.rename))
    notes: list[str] = []
    rr._drop_snapshot(snap, os.path.realpath(root), notes)
    monkeypatch.undo()
    assert fired["n"] == 1
    found = {p.read_bytes() for p in snap.parent.rglob("*") if p.is_file() and not p.is_symlink()}
    assert b"someone else's only copy\n" in found, notes


def test_a_quarantine_name_already_taken_is_never_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-143 r3 (luna r2): a plain rename into q/<name> replaces whatever is already at that name."""
    import os
    import secrets

    from trw_mcp.bootstrap import _refused_restore as rr
    from trw_mcp.bootstrap import _snapshot_fd as sf

    root, snap = _project(tmp_path, monkeypatch)
    (root / REL).write_bytes((snap / REL).read_bytes())  # all back in the project: the drop goes ahead
    real_hex = secrets.token_hex
    taken = {"n": 0}

    def colliding(nbytes: int | None = None) -> str:
        quarantine = [p / "q" for p in snap.parent.iterdir() if p.name.startswith(".trw-dropping-")]
        if quarantine and quarantine[0].is_dir() and not taken["n"]:
            taken["n"] += 1
            (quarantine[0] / "taken").write_bytes(b"already here, only copy\n")
            return "taken"
        return real_hex(nbytes)

    monkeypatch.setattr(sf.secrets, "token_hex", colliding)
    notes: list[str] = []
    rr._drop_snapshot(snap, os.path.realpath(root), notes)
    monkeypatch.undo()
    assert taken["n"] == 1
    found = {p.read_bytes() for p in snap.parent.rglob("*") if p.is_file() and not p.is_symlink()}
    assert b"already here, only copy\n" in found, notes


def test_an_entry_gone_before_its_move_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E2E-INC-143 r3 (luna r2 minor): an entry that vanished between the listing and its move was skipped silently."""
    import os

    from trw_mcp.bootstrap import _refused_restore as rr
    from trw_mcp.bootstrap import _snapshot_fd as sf

    root, snap = _project(tmp_path, monkeypatch)
    (root / REL).write_bytes((snap / REL).read_bytes())
    real_rename = os.rename
    gone = {"n": 0}

    def vanish_first(src, *a, **k):  # type: ignore[no-untyped-def]
        if src == "settings.json" and not gone["n"]:
            gone["n"] += 1
            (_captured(snap.parent) / "settings.json").unlink()
        return real_rename(src, *a, **k)

    monkeypatch.setattr(sf.os, "rename", vanish_first)
    notes: list[str] = []
    assert rr._drop_snapshot(snap, os.path.realpath(root), notes) is False
    monkeypatch.undo()
    assert gone["n"] == 1 and any("changed while being removed" in n for n in notes), notes
