"""ki1-race-review r8: attacks on the user-dir snapshot location, samefile trust, no-follow retry, .bad naming,
and GUARDED-COPY add_mode/fchmod.

Every probe prints FIRED:<name> when its race/arm actually ran; a probe that never fires is UNTESTED.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.integration

REL = ".claude/my-settings.json"
U = b'{"mine": 1}\n' * 50
U2 = b'{"theirs": 2}\n' * 40
OUTSIDE = b"precious bytes outside the snapshot\n"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _shas(root: Path) -> set[str]:
    out = set()
    for dirpath, _d, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                out.add(_sha(p.read_bytes()))
    return out


@pytest.fixture
def userdir(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("trwuser8")
    monkeypatch.setenv("TRW_USER_DIR", str(d))
    return d


def _legit(repo: Path, data: bytes = U, rels: list[str] | None = None) -> tuple[Path, Path]:
    from trw_mcp.bootstrap._refused_restore import new_snapshot_dir, record_refused

    snap = new_snapshot_dir(repo)  # W1 r9: owner marker
    for rel in rels or [REL]:
        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
        (snap / rel).write_bytes(data)
    rec = record_refused(repo, snap, rels or [REL])
    assert rec is not None
    return snap, rec


def _retry(repo: Path) -> tuple[list[str], BaseException | None]:
    from trw_mcp.bootstrap._refused_restore import retry_refused

    notes: list[str] = []
    try:
        retry_refused(repo, notes)
    except BaseException as exc:  # trw-fail-silent-allow: the test records the error and asserts on it
        return notes, exc
    return notes, None


def _write_rec(repo: Path, snap: str, rels: list[str], tag: str) -> Path:
    from trw_mcp.bootstrap._refused_restore import _records_dir

    d = _records_dir(create=True)
    d.mkdir(parents=True, exist_ok=True)
    r = d / f"20260101T000000Z-{tag}-cafebabe.json"
    r.write_text(json.dumps({"v": 1, "project": os.path.realpath(repo), "snapshot": snap, "rels": rels}))
    return r


# ---- d1: TRW_USER_DIR is a symlink ---------------------------------------------------------------


def test_r8_d1_userdir_is_symlink_legit_roundtrip(initialized_repo, tmp_path_factory, monkeypatch) -> None:
    real = tmp_path_factory.mktemp("real_ud")
    link = tmp_path_factory.mktemp("ud_link_parent") / "ud"
    link.symlink_to(real)
    monkeypatch.setenv("TRW_USER_DIR", str(link))
    snap, rec = _legit(initialized_repo)
    notes, exc = _retry(initialized_repo)
    in_trash = _sha(U) in _shas(initialized_repo / ".trw" / "trash")
    print(
        f"\nFIRED:d1 snap={snap} under_real={str(snap).startswith(str(real.resolve()))} exc={exc!r} in_trash={in_trash} rec={rec.exists()} notes={[n[:90] for n in notes]}"
    )
    assert exc is None and in_trash and not rec.exists()


# ---- d2: TRW_USER_DIR inside the project -----------------------------------------------------------


@pytest.mark.parametrize("where", [".claude/trwuser", ".trw/trwuser", "trwuser"])
@pytest.mark.parametrize("fail", [False, True])
def test_r8_d2_userdir_inside_project(initialized_repo, monkeypatch, where, fail) -> None:
    from trw_mcp.bootstrap import _update_project as up

    repo = initialized_repo
    ud = repo / where
    ud.mkdir(parents=True, exist_ok=True)
    (ud / "precious.db").write_bytes(OUTSIDE)
    monkeypatch.setenv("TRW_USER_DIR", str(ud))
    (repo / REL).write_bytes(U)  # an untracked user file in the surface
    if fail:

        def boom(*a, **k):
            raise OSError(5, "injected failure after the writers")

        monkeypatch.setattr(up, "enforce_and_write_manifest", boom)
    exc = None
    result = None
    try:
        result = up.update_project(repo, ide="claude-code")
    except BaseException as e:  # trw-fail-silent-allow: the test records the error and asserts on it
        exc = e
    snaps = list((ud / "update-snapshots").glob("*")) if (ud / "update-snapshots").exists() else []
    precious = (ud / "precious.db").exists() and (ud / "precious.db").read_bytes() == OUTSIDE
    print(
        f"\nFIRED:d2 where={where} fail={fail} exc={exc!r} precious={precious} U={_sha(U) in _shas(repo)} "
        f"snaps_left={len(snaps)} errors={None if result is None else [e[:140] for e in result['errors']][:3]}"
    )
    assert exc is None, f"update-project crashed with TRW_USER_DIR inside the project: {exc!r}"
    assert precious, "the TRW user dir's own file was lost"
    assert _sha(U) in _shas(repo)


# ---- d3: unusable user dir: $TMPDIR fallback, never trusted; and the non-OSError refusal ----------


def test_r8_d3_fallback_snapshot_never_trusted(initialized_repo, userdir) -> None:
    from trw_mcp.bootstrap._refused_restore import new_snapshot_dir, record_refused

    (userdir / "update-snapshots").write_bytes(b"not a folder")  # snapshots folder unusable, records folder fine
    snap = new_snapshot_dir(initialized_repo)
    fallback = not str(snap).startswith(str(userdir.resolve()))
    (snap / REL).parent.mkdir(parents=True)
    (snap / REL).write_bytes(U)
    rec = record_refused(initialized_repo, snap, [REL])
    notes, exc = _retry(initialized_repo)
    print(
        f"\nFIRED:d3 fallback={fallback} snap={snap} record_written={rec is not None} exc={exc!r} "
        f"U_still_in_fallback={(snap / REL).exists()} notes={[n[:140] for n in notes]}"
    )
    try:
        assert fallback and exc is None and (snap / REL).read_bytes() == U
        assert _sha(U) not in _shas(initialized_repo / ".trw" / "trash")
    finally:
        shutil.rmtree(snap, ignore_errors=True)


@pytest.mark.parametrize("how", ["memory_is_file", "memory_untrusted_dir"])
def test_r8_d3b_user_dir_refused_by_trust_check(initialized_repo, tmp_path_factory, monkeypatch, how) -> None:
    """The user dir is refused with UntrustedDirectoryError (not an OSError): update-project must not crash."""
    from trw_mcp.bootstrap import _update_project as up

    ud = tmp_path_factory.mktemp("bad_ud")
    monkeypatch.setenv("TRW_USER_DIR", str(ud))
    if how == "memory_is_file":
        (ud / "memory").write_bytes(b"x")
    else:
        from trw_memory import _dir_trust, user_paths
        from trw_memory.exceptions import UntrustedDirectoryError

        def refuse(fd, path, **k):
            raise UntrustedDirectoryError(
                f"{path} refused (simulated exFAT dir whose 0777 cannot be hardened)", path=str(path)
            )

        monkeypatch.setattr(user_paths, "verify_and_harden_dir_fd", refuse, raising=False)
        monkeypatch.setattr(_dir_trust, "verify_and_harden_dir_fd", refuse)
    (initialized_repo / REL).write_bytes(U)
    exc = None
    result = None
    try:
        result = up.update_project(initialized_repo, ide="claude-code")
    except BaseException as e:  # trw-fail-silent-allow: the test records the error and asserts on it
        exc = e
    print(
        f"\nFIRED:d3b how={how} exc={type(exc).__name__ if exc else None}: {str(exc)[:160] if exc else ''} "
        f"errors={None if result is None else [e[:140] for e in result['errors']][:3]}"
    )
    assert exc is None, f"update-project crashed on an unusable TRW user dir ({how}): {exc!r}"


# ---- d4: samefile / no-follow under swaps -------------------------------------------------------


def test_r8_d4_ancestor_symlink_swapped_after_trust(initialized_repo, userdir, tmp_path_factory, monkeypatch) -> None:
    """A record whose snapshot path runs through a symlinked parent (bind-like); the parent is repointed after
    _trusted: the open is O_NOFOLLOW only on the last component."""
    from trw_mcp.bootstrap import _refused_restore as rr

    snap, legit_rec = _legit(initialized_repo)
    legit_rec.unlink()
    base = tmp_path_factory.mktemp("bind")
    link = base / "snaps"
    link.symlink_to(snap.parent)
    victim_parent = tmp_path_factory.mktemp("victim_parent")
    victim = victim_parent / snap.name / REL
    victim.parent.mkdir(parents=True)
    victim.write_bytes(OUTSIDE)
    _write_rec(initialized_repo, str(link / snap.name), [REL], "d4")
    real = rr._trusted
    box = {"n": 0}

    def trusted_then_swap(root, snapshot, rels):
        ok = real(root, snapshot, rels)
        if ok and not box["n"]:
            box["n"] += 1
            link.unlink()
            link.symlink_to(victim_parent)
        return ok

    monkeypatch.setattr(rr, "_trusted", trusted_then_swap)
    notes, exc = _retry(initialized_repo)
    print(
        f"\nFIRED:d4 n={box['n']} exc={exc!r} victim_kept={victim.exists()} "
        f"victim_in_trash={_sha(OUTSIDE) in _shas(initialized_repo / '.trw' / 'trash')} notes={[n[:160] for n in notes]}"
    )
    assert box["n"] == 1, "UNTESTED"
    assert exc is None
    assert victim.exists() and victim.read_bytes() == OUTSIDE, (
        "a file outside the user dir's snapshot was moved/deleted"
    )


def test_r8_d4b_snapshot_replaced_by_symlink_after_trust(
    initialized_repo, userdir, tmp_path_factory, monkeypatch
) -> None:
    from trw_mcp.bootstrap import _refused_restore as rr

    snap, rec = _legit(initialized_repo)
    victim_dir = tmp_path_factory.mktemp("victim4b")
    (victim_dir / REL).parent.mkdir(parents=True)
    (victim_dir / REL).write_bytes(OUTSIDE)
    real = rr._trusted
    box = {"n": 0}

    def trusted_then_swap(root, snapshot, rels):
        ok = real(root, snapshot, rels)
        if ok and not box["n"]:
            box["n"] += 1
            os.rename(snap, snap.with_name(snap.name + "-moved"))
            snap.symlink_to(victim_dir)
        return ok

    monkeypatch.setattr(rr, "_trusted", trusted_then_swap)
    notes, exc = _retry(initialized_repo)
    moved = snap.with_name(snap.name + "-moved")
    print(
        f"\nFIRED:d4b n={box['n']} exc={exc!r} victim={(victim_dir / REL).exists()} U_kept={(moved / REL).exists()} notes={[n[:160] for n in notes]}"
    )
    assert (
        box["n"] == 1 and exc is None and (victim_dir / REL).read_bytes() == OUTSIDE and (moved / REL).read_bytes() == U
    )


def test_r8_d4c_snapshot_replaced_by_real_dir_after_open(initialized_repo, userdir, monkeypatch) -> None:
    from trw_mcp.bootstrap import _refused_restore as rr

    snap, rec = _legit(initialized_repo)
    real_move = rr._move_recorded
    box = {"n": 0}
    other = snap.with_name(snap.name + "-orig")

    def swap_then_move(root, snapshot, sfd, rels, record, notes):
        box["n"] += 1
        os.rename(snap, other)
        (snap / REL).parent.mkdir(parents=True)
        (snap / REL).write_bytes(OUTSIDE)
        return real_move(root, snapshot, sfd, rels, record, notes)

    monkeypatch.setattr(rr, "_move_recorded", swap_then_move)
    notes, exc = _retry(initialized_repo)
    trash = _shas(initialized_repo / ".trw" / "trash")
    print(
        f"\nFIRED:d4c n={box['n']} exc={exc!r} U_moved={_sha(U) in trash} imposter_kept={(snap / REL).exists()} rec={rec.exists()} notes={[n[:120] for n in notes]}"
    )
    # descriptor-anchored: the move reads the ORIGINAL snapshot (by fd); the imposter at the name is untouched
    assert exc is None and (snap / REL).read_bytes() == OUTSIDE and (_sha(U) in trash or (other / REL).exists())


@pytest.mark.parametrize("step", ["stat_to_open_symlink", "read_to_unlink_replace"])
def test_r8_d4d_leaf_swap(initialized_repo, userdir, tmp_path_factory, monkeypatch, step) -> None:
    from trw_mcp.bootstrap import _refused_restore as rr

    snap, rec = _legit(initialized_repo)
    leaf = snap / REL
    outside = tmp_path_factory.mktemp("leafout") / "o"
    outside.write_bytes(OUTSIDE)
    box = {"n": 0}
    if step == "stat_to_open_symlink":
        real_stat = rr.os.stat

        def stat_then_swap(name, *a, **k):
            out = real_stat(name, *a, **k)
            if k.get("dir_fd") is not None and name == leaf.name and not box["n"]:
                box["n"] += 1
                os.unlink(leaf)
                os.symlink(outside, leaf)
            return out

        monkeypatch.setattr(rr.os, "stat", stat_then_swap)
    else:
        from trw_mcp.bootstrap import _restore_proof

        real_save = _restore_proof.save_payload_in_trash

        def save_then_swap(*a, **k):
            out = real_save(*a, **k)
            if not box["n"]:
                box["n"] += 1
                os.rename(leaf, leaf.with_name("U-kept-aside"))
                leaf.write_bytes(U2)  # a different file now holds the name
            return out

        monkeypatch.setattr(_restore_proof, "save_payload_in_trash", save_then_swap)
    notes, exc = _retry(initialized_repo)
    monkeypatch.undo()
    trash = _shas(initialized_repo / ".trw" / "trash")
    print(
        f"\nFIRED:d4d step={step} n={box['n']} exc={exc!r} outside_kept={outside.read_bytes() == OUTSIDE} "
        f"U_in_trash={_sha(U) in trash} U2_at_name={leaf.exists() and not leaf.is_symlink() and leaf.read_bytes() == U2} "
        f"notes={[n[:140] for n in notes]}"
    )
    assert box["n"] == 1 and exc is None and outside.read_bytes() == OUTSIDE


# ---- d5: a record naming ANOTHER project's snapshot ------------------------------------------------


def test_r8_d5_record_for_A_names_Bs_snapshot(initialized_repo, userdir, tmp_path_factory) -> None:
    repo_a = initialized_repo
    repo_b = tmp_path_factory.mktemp("projB") / "b"
    shutil.copytree(repo_a, repo_b, symlinks=True)
    snap_b, rec_b = _legit(repo_b, data=U2)
    forged = _write_rec(repo_a, str(snap_b), [REL], "d5")
    notes_a, exc_a = _retry(repo_a)
    b_in_a = _sha(U2) in _shas(repo_a / ".trw" / "trash")
    notes_b, exc_b = _retry(repo_b)
    b_in_b = _sha(U2) in _shas(repo_b / ".trw" / "trash")
    print(
        f"\nFIRED:d5 exc={exc_a!r},{exc_b!r} Bbytes_in_A_trash={b_in_a} Bbytes_in_B_trash={b_in_b} forged_left={forged.exists()} "
        f"notes_a={[n[:110] for n in notes_a]} notes_b={[n[:110] for n in notes_b]}"
    )
    assert exc_a is None and exc_b is None
    assert not b_in_a, "A's record moved project B's pre-update copy into project A"


# ---- d6: concurrent updates on two projects sharing the user dir -----------------------------------

_CHILD = r"""
import sys, json
from pathlib import Path
from trw_mcp.bootstrap import _update_project as up
r = up.update_project(Path(sys.argv[1]), ide="claude-code")
print("RESULT " + json.dumps({"errors": r["errors"], "warnings": [w for w in r["warnings"] if "pre-update" in w or "retry record" in w]}))
"""


def test_r8_d6_two_projects_concurrent_update(initialized_repo, userdir, tmp_path_factory) -> None:
    repo_a = initialized_repo
    repo_b = tmp_path_factory.mktemp("projB6") / "b"
    shutil.copytree(repo_a, repo_b, symlinks=True)
    _legit(repo_a, data=U)
    _legit(repo_b, data=U2)
    from trw_mcp.bootstrap._refused_restore import _records_dir

    (_records_dir(create=True) / "20260101T000000Z-zz-damaged.json").write_text("{oops")
    # Children run a real update; without this each could autostart a memory daemon that outlives the test (the
    # session-end reaper then fails the run). The retry this test checks needs no daemon.
    env = {**os.environ, "TRW_USER_DIR": str(userdir), "MEMORY_DAEMON_AUTOSTART": "false"}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(r)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for r in (repo_a, repo_b, repo_a)
    ]
    outs = [p.communicate(timeout=300) for p in procs]
    rcs = [p.returncode for p in procs]
    a_ok = _sha(U) in _shas(repo_a / ".trw" / "trash")
    b_ok = _sha(U2) in _shas(repo_b / ".trw" / "trash")
    left = sorted(p.name for p in (userdir / "update-snapshots").iterdir())
    recs = sorted(p.name for p in _records_dir(create=False).iterdir())
    res = [next((ln for ln in o.splitlines() if ln.startswith("RESULT")), "NO RESULT " + e[-300:]) for o, e in outs]
    print(f"\nFIRED:d6 rcs={rcs} A_in_trash={a_ok} B_in_trash={b_ok} snapshots_left={left} records={recs}")
    for r in res:
        print(f"d6 {r[:400]}")
    assert rcs == [0, 0, 0] and a_ok and b_ok


def test_r8_d6b_same_record_two_threads(initialized_repo, userdir) -> None:
    snap, rec = _legit(initialized_repo)
    out: list = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        out.append(_retry(initialized_repo))

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    trash = initialized_repo / ".trw" / "trash"
    copies = sum(1 for p in trash.rglob("*") if p.is_file() and p.read_bytes() == U)
    bad = list(rec.parent.glob("*.bad.*"))
    print(
        f"\nFIRED:d6b excs={[o[1] for o in out]} copies_in_trash={copies} bad={len(bad)} notes={[[n[:100] for n in o[0]] for o in out]}"
    )
    assert all(o[1] is None for o in out) and copies >= 1


# ---- d7: .bad naming under races ---------------------------------------------------------------------


def test_r8_d7_set_aside_rename_races(initialized_repo, userdir, monkeypatch) -> None:
    from trw_mcp.bootstrap import _refused_restore as rr

    rec = _write_rec(initialized_repo, "/nonexistent", [REL], "d7")
    rec.write_text("{oops")
    real_rename = rr.os.rename
    box = {"n": 0}

    def racing_rename(a, b, *x, **k):
        if not box["n"] and str(a) == str(rec):
            box["n"] += 1
            real_rename(a, str(b) + "-other-process")  # another update-project set it aside first
        return real_rename(a, b, *x, **k)

    monkeypatch.setattr(rr.os, "rename", racing_rename)
    notes, exc = _retry(initialized_repo)
    monkeypatch.undo()
    names = sorted(p.name for p in rec.parent.iterdir())
    print(f"\nFIRED:d7 n={box['n']} exc={exc!r} names={names} notes={notes}")
    assert box["n"] == 1 and exc is None
    assert any("could not be set aside" in n for n in notes), "a failed rename was reported as set aside"


def test_r8_d8_one_bad_rel_strands_good_rel(initialized_repo, userdir) -> None:
    """rel #1 cannot be walked (its folder is a file in the snapshot); rel #2 is a legit copy. Is #2 moved, or at
    least named with a copy command, or is the whole record set aside with #2 left unnamed?"""
    from trw_mcp.bootstrap._refused_restore import new_snapshot_dir, record_refused

    snap = new_snapshot_dir(initialized_repo)
    (snap / ".claude").mkdir()
    (snap / ".claude" / "blocker").write_bytes(b"file, not folder")
    (snap / REL).write_bytes(U)
    rec = record_refused(initialized_repo, snap, [".claude/blocker/x.json", REL])
    notes, exc = _retry(initialized_repo)
    moved = _sha(U) in _shas(initialized_repo / ".trw" / "trash")
    named = any(str(snap / REL) in n for n in notes)
    print(
        f"\nFIRED:d8 exc={exc!r} U_moved={moved} U_named={named} U_in_snap={(snap / REL).exists()} notes={[n[:160] for n in notes]}"
    )
    assert exc is None and (moved or named or (snap / REL).exists())


# The (g) GUARDED-COPY tests of this suite live on that branch (tests/test_ki1_r8_guarded_copy.py).
