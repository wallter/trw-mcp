"""ki1-race-review r7: attacks on r7's retry-record validation and on GUARDED-COPY's O_EXCL publish.

Every probe prints FIRED:<name> when its race/arm actually ran; a probe that never fires is UNTESTED.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.integration

REL = ".claude/my-settings.json"
U = b'{"mine": 1}\n' * 50
OUTSIDE = b"precious bytes outside the project\n"
W = b"#!/bin/sh\n# concurrent writer W (r7 review)\n"
_MADE: list[Path] = []


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
    d = tmp_path_factory.mktemp("trwuser")
    monkeypatch.setenv("TRW_USER_DIR", str(d))
    yield d
    for p in _MADE:
        if p.is_symlink():
            p.unlink()
        else:
            shutil.rmtree(p, ignore_errors=True)
    _MADE.clear()


def _records() -> Path:
    from trw_mcp.bootstrap._refused_restore import _records_dir

    d = _records_dir(create=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tmp(name: str | None = None, *, prefix: str | None = None, parent: Path | None = None) -> Path:
    if parent is None and prefix is not None:  # W1 r8: update snapshots live in the TRW user directory
        from trw_mcp.bootstrap._refused_restore import _snapshots_dir

        parent = _snapshots_dir(create=True)
        parent.mkdir(parents=True, exist_ok=True)
    base = parent or Path(tempfile.gettempdir())
    if name is None:
        p = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    else:
        p = base / name
        p.mkdir(parents=True)
    _MADE.append(p)
    return p


def _own(snap: Path, repo: Path) -> None:
    from trw_mcp.bootstrap._refused_restore import _OWNER

    (snap / _OWNER).write_text(json.dumps({"v": 1, "project": os.path.realpath(repo)}))


def _rec(repo: Path, snap: str, rels: list[str], tag: str) -> Path:
    # W1 r9: update-project writes an owner marker into each snapshot it creates; give a prefixed test snapshot
    # the marker for the record's project, as if this project's update had made it.
    from trw_mcp.bootstrap._refused_restore import _OWNER, SNAPSHOT_PREFIX

    folder = Path(snap)
    if folder.name.startswith(SNAPSHOT_PREFIX) and folder.is_dir() and not folder.is_symlink():
        (folder / _OWNER).write_text(json.dumps({"v": 1, "project": os.path.realpath(repo)}))
    r = _records() / f"20260101T000000Z-{tag}-cafebabe.json"
    r.write_text(json.dumps({"v": 1, "project": os.path.realpath(repo), "snapshot": snap, "rels": rels}))
    return r


def _retry(repo: Path) -> tuple[list[str], BaseException | None]:
    from trw_mcp.bootstrap._refused_restore import retry_refused

    notes: list[str] = []
    try:
        retry_refused(repo, notes)
    except Exception as exc:
        return notes, exc
    return notes, None


def _fill(snap: Path, rel: str = REL, data: bytes = U) -> Path:
    (snap / rel).parent.mkdir(parents=True, exist_ok=True)
    (snap / rel).write_bytes(data)
    return snap / rel


# ---- (b) validation ----------------------------------------------------------------------------


def test_b1_tempdir_without_prefix(initialized_repo, userdir) -> None:
    snap = _tmp("rv7-noprefix-" + os.urandom(4).hex())
    victim = _fill(snap)
    _rec(initialized_repo, str(snap), [REL], "b1")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b1 notes={notes}")
    assert exc is None and victim.read_bytes() == U


def test_b2_prefix_nested(initialized_repo, userdir) -> None:
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    outer = _tmp("rv7-outer-" + os.urandom(4).hex())
    snap = outer / (SNAPSHOT_PREFIX + "nested")
    victim = _fill(snap)
    _rec(initialized_repo, str(snap), [REL], "b2")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b2 notes={notes}")
    assert exc is None and victim.read_bytes() == U


def test_b3_symlink_snapshot_to_outside(initialized_repo, userdir, tmp_path_factory) -> None:
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    outside = tmp_path_factory.mktemp("outside")
    victim = _fill(outside, data=OUTSIDE)
    link = Path(tempfile.gettempdir()) / (SNAPSHOT_PREFIX + "link" + os.urandom(4).hex())
    link.symlink_to(outside)
    _MADE.append(link)
    _rec(initialized_repo, str(link), [REL], "b3")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b3 notes={notes}")
    assert exc is None and victim.read_bytes() == OUTSIDE


def test_b3b_symlink_snapshot_to_another_snapshot(initialized_repo, userdir) -> None:
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    real = _tmp(prefix=SNAPSHOT_PREFIX)
    victim = _fill(real)
    link = Path(tempfile.gettempdir()) / (SNAPSHOT_PREFIX + "l2" + os.urandom(4).hex())
    link.symlink_to(real)
    _MADE.append(link)
    _rec(initialized_repo, str(link), [REL], "b3b")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b3b notes={notes}")
    assert exc is None and victim.read_bytes() == U


def test_b4_leaf_symlink_in_snapshot_to_outside(initialized_repo, userdir, tmp_path_factory) -> None:
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    outside = tmp_path_factory.mktemp("outside")
    target = outside / "precious.txt"
    target.write_bytes(OUTSIDE)
    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    (snap / ".claude").mkdir()
    (snap / REL).symlink_to(target)
    _rec(initialized_repo, str(snap), [REL], "b4")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b4 notes={notes} target_exists={target.exists()}")
    assert exc is None and target.read_bytes() == OUTSIDE


def test_b5_folder_symlink_in_snapshot_to_outside(initialized_repo, userdir, tmp_path_factory) -> None:
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    outside = tmp_path_factory.mktemp("outside")
    victim = _fill(outside, rel="my-settings.json", data=OUTSIDE)
    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    (snap / ".claude").symlink_to(outside)
    _rec(initialized_repo, str(snap), [REL], "b5")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b5 notes={notes}")
    assert exc is None and victim.read_bytes() == OUTSIDE


def test_b6_project_folder_symlinked_outside(initialized_repo, userdir, tmp_path_factory) -> None:
    """rel resolves inside the snapshot but its project folder is a symlink out of the project."""
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    repo = initialized_repo
    outside = tmp_path_factory.mktemp("outside")
    (outside / "keep.txt").write_bytes(OUTSIDE)
    (repo / "linked").symlink_to(outside)
    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap, rel="linked/keep.txt")
    rec = _rec(repo, str(snap), ["linked/keep.txt"], "b6")
    notes, exc = _retry(repo)
    print(f"\nFIRED:b6 notes={notes} rec_exists={rec.exists()} snapfile={(snap / 'linked/keep.txt').exists()}")
    assert exc is None and (outside / "keep.txt").read_bytes() == OUTSIDE
    assert (snap / "linked/keep.txt").read_bytes() == U


def test_b6b_trash_symlinked_outside(initialized_repo, userdir, tmp_path_factory) -> None:
    """.trw/trash is a symlink out of the project: where does the retry write the user's copy?"""
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    repo = initialized_repo
    outside = tmp_path_factory.mktemp("outside_trash")
    (outside / "keep.txt").write_bytes(OUTSIDE)
    trash = repo / ".trw" / "trash"
    if trash.exists():
        shutil.rmtree(trash)
    trash.symlink_to(outside)
    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap)
    _rec(repo, str(snap), [REL], "b6b")
    notes, exc = _retry(repo)
    written_outside = sorted(str(p.relative_to(outside)) for p in outside.rglob("*"))
    print(f"\nFIRED:b6b notes={notes} outside_now={written_outside}")
    assert exc is None and (outside / "keep.txt").read_bytes() == OUTSIDE


def test_b7_case_variant_of_tempdir_drops_legit_record(initialized_repo, userdir) -> None:
    """APFS is case-insensitive: the same snapshot spelled with another case of the temp dir."""
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap)
    s = str(snap)
    variant = s[: len(s) - len(snap.name) - 1].swapcase() + "/" + snap.name
    if not Path(variant).exists():
        pytest.skip("temp dir is on a case-sensitive volume")  # skip-category: platform
    rec = _rec(initialized_repo, variant, [REL], "b7")
    notes, exc = _retry(initialized_repo)
    print(
        f"\nFIRED:b7 notes={notes} rec_exists={rec.exists()} U_in_trash={_sha(U) in _shas(initialized_repo / '.trw' / 'trash')}"
    )
    assert (exc is None and (snap / REL).exists()) or _sha(U) in _shas(initialized_repo / ".trw" / "trash")
    assert _sha(U) in _shas(initialized_repo / ".trw" / "trash") or rec.exists(), "legit record dropped and U not saved"


def test_b8_tempdir_changed_between_runs_drops_legit_record(
    initialized_repo, userdir, monkeypatch, tmp_path_factory
) -> None:
    """The refusing run had TMPDIR=A (e.g. a sandboxed agent shell); the next update runs with TMPDIR=B."""
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX, record_refused

    repo = initialized_repo
    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap)
    _own(snap, initialized_repo)  # W1 r9: the owner marker update-project writes
    rec = record_refused(repo, snap, [REL])
    assert rec is not None
    other = tmp_path_factory.mktemp("other_tmpdir")
    monkeypatch.setattr(tempfile, "tempdir", str(other))
    notes, exc = _retry(repo)
    monkeypatch.undo()
    monkeypatch.setenv("TRW_USER_DIR", str(userdir))
    saved = _sha(U) in _shas(repo / ".trw" / "trash")
    print(f"\nFIRED:b8 notes={notes} rec_exists={rec.exists()} U_saved={saved} U_still_in_snap={(snap / REL).exists()}")
    assert exc is None
    assert saved or rec.exists(), (
        "a legit record was deleted because TMPDIR differed; U is left only in a purgeable temp dir, unnamed"
    )


def test_b9_snapshot_swapped_after_validation(initialized_repo, userdir, monkeypatch, tmp_path_factory) -> None:
    """Record raced: the validated snapshot dir is replaced by a symlink to outside before the move."""
    from trw_mcp.bootstrap import _refused_restore as rr
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    outside = tmp_path_factory.mktemp("outside")
    victim = _fill(outside, data=OUTSIDE)
    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap)
    _rec(initialized_repo, str(snap), [REL], "b9")
    real = rr._trusted
    fired = {"n": 0}

    def swap(*a, **k):
        ok = real(*a, **k)
        if ok:
            fired["n"] += 1
            shutil.rmtree(snap)
            snap.symlink_to(outside)
        return ok

    monkeypatch.setattr(rr, "_trusted", swap)
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b9 n={fired['n']} notes={notes} victim_exists={victim.exists()}")
    assert fired["n"] == 1
    assert victim.exists() and victim.read_bytes() == OUTSIDE, "outside file deleted after a post-validation swap"


def test_b10_bad_quarantine_overwrites_existing_bad(initialized_repo, userdir) -> None:
    d = _records()
    name = "20260101T000000Z-b10-cafebabe.json"
    (d / (name + ".bad")).write_text("EARLIER BAD RECORD (only pointer to an old snapshot)")
    (d / name).write_text("{not json")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b10 notes={notes} bad={(d / (name + '.bad')).read_text()[:40]!r}")
    assert exc is None
    assert (d / (name + ".bad")).read_text().startswith("EARLIER"), ".bad quarantine overwrote an existing .bad"


@pytest.mark.parametrize("rel", [".", "./", ".claude", "a/./b/.."])
def test_b11_rel_names_a_directory(initialized_repo, userdir, rel) -> None:
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX

    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap)
    rec = _rec(initialized_repo, str(snap), [rel], "b11")
    notes, exc = _retry(initialized_repo)
    print(f"\nFIRED:b11[{rel}] exc={exc!r} notes={notes} U_in_snap={(snap / REL).exists()} rec={rec.exists()}")
    assert exc is None, f"retry crashed on rel={rel!r}: {exc!r}"
    assert (snap / REL).exists() or _sha(U) in _shas(initialized_repo / ".trw" / "trash")


def test_b11_full_update_with_directory_rel(initialized_repo, userdir) -> None:
    """Does a record whose rel is a snapshot directory crash every update-project?"""
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX
    from trw_mcp.bootstrap._update_project import update_project

    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap)
    _rec(initialized_repo, str(snap), [".claude"], "b11u")
    exc = None
    res = None
    try:
        res = update_project(initialized_repo, ide="claude-code")
    except Exception as e:
        exc = e
    print(f"\nFIRED:b11u exc={exc!r} errors={(res or {}).get('errors', [])[:2]}")
    assert exc is None, f"update-project crashed: {exc!r}"


def test_b12_legit_control(initialized_repo, userdir) -> None:
    from trw_mcp.bootstrap._refused_restore import SNAPSHOT_PREFIX, record_refused

    repo = initialized_repo
    snap = _tmp(prefix=SNAPSHOT_PREFIX)
    _fill(snap)
    _own(snap, initialized_repo)  # W1 r9: the owner marker update-project writes
    other = _fill(snap, rel=".claude/other.json", data=b"other\n")
    rec = record_refused(repo, snap, [REL])
    notes, exc = _retry(repo)
    print(f"\nFIRED:b12 notes={notes} rec={rec.exists()} snap={snap.exists()} other={other.exists()}")
    assert exc is None and _sha(U) in _shas(repo / ".trw" / "trash")
    assert not (snap / REL).exists() and other.read_bytes() == b"other\n" and not rec.exists()


# The (c) GUARDED-COPY tests of this suite live on that branch (tests/test_ki1_r7_guarded_copy.py).
