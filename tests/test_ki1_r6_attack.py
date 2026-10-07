"""ki1-race-review r6: attacks on the new r6 code. Every probe must fire (else UNTESTED, an error)."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401
from ._fs_hazards import atomic_replace, open_fd_writer, race_after

pytestmark = pytest.mark.integration

REL = ".claude/my-settings.json"
U = b'{"mine": 1}\n' * 50
T = b'{"trw": 2}\n' * 50
W = b"concurrent writer bytes\n"
OUTSIDE = b"precious bytes outside the project\n"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _shas(root: Path) -> set[str]:
    out = set()
    for dirpath, _d, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                try:
                    out.add(_sha(p.read_bytes()))
                except OSError:  # trw-fail-silent-allow: an unreadable file just has no hash to report
                    pass
    return out


def _setup(repo: Path, factory) -> tuple[Path, Path]:
    snap = factory.mktemp("snap")
    (snap / REL).parent.mkdir(parents=True, exist_ok=True)
    (snap / REL).write_bytes(U)
    (repo / REL).parent.mkdir(parents=True, exist_ok=True)
    return snap, repo / REL


def _refuse_capture(monkeypatch) -> dict:
    """Make the trash capture refuse (status kept), so remove_proven_or_keep takes the in-place clear path."""
    from trw_mcp.bootstrap import _trash

    box = {"fired": False}

    class _O:
        status = "kept"
        reason = "could not capture: [Errno 28] No space left on device (forced)"
        retained_at = None

    def refuse(*a, **k):
        box["fired"] = True
        return _O()

    monkeypatch.setattr(_trash, "remove_if_hash", refuse)
    return box


def _run(repo, snap, arm):
    from trw_mcp._checkout_write import recording_writes, write_checkout_file
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    notes: list[str] = []
    exc = None
    with recording_writes():
        write_checkout_file(repo, repo / REL, T)
        arm()
        try:
            _restore_transaction_file(repo, snap, REL, notes)
        except OSError as e:  # trw-fail-silent-allow: the test records the error and asserts on it
            exc = e
    return notes, exc


# ---- in-place clear path ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "step",
    [
        "baseline_inplace_clear",
        "fd_write_between_rename_and_rehash",
        "new_file_at_name_between_rename_and_rehash",
        "fd_write_then_third_writer_takes_name_before_linkback",
        "symlink_planted_at_sibling_name",
        pytest.param(
            "enospc_rename_then_atomic_writer_before_unlink",
            marks=pytest.mark.xfail(  # skip-category: tracked-defect
                strict=True, reason="KI: full-disk last resort re-hash-then-unlink window (CHANGELOG Known issues)"
            ),
        ),
    ],
)
def test_inplace(initialized_repo, monkeypatch, tmp_path_factory, step) -> None:
    from trw_mcp.bootstrap import _restore_proof as rp

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    outside = tmp_path_factory.mktemp("out") / "precious.txt"
    outside.write_bytes(OUTSIDE)
    probes: list = []
    held: dict = {}
    aside_name = f".{dest.name}.trw-00112233aabbccdd"

    def arm():
        probes.append(_refuse_capture(monkeypatch))
        if step == "fd_write_between_rename_and_rehash":
            held["fd"] = open_fd_writer(dest)
            probes.append(
                race_after(monkeypatch, target=dest, op="rename", when="after", interloper=lambda: held["fd"].write(W))
            )
        elif step == "new_file_at_name_between_rename_and_rehash":
            probes.append(
                race_after(monkeypatch, target=dest, op="rename", when="after", interloper=lambda: dest.write_bytes(W))
            )
        elif step == "fd_write_then_third_writer_takes_name_before_linkback":
            held["fd"] = open_fd_writer(dest)
            probes.append(
                race_after(monkeypatch, target=dest, op="rename", when="after", interloper=lambda: held["fd"].write(W))
            )
            probes.append(
                race_after(
                    monkeypatch,
                    target=dest,
                    op="link",
                    when="before",
                    interloper=lambda: dest.write_bytes(b"third writer\n"),
                )
            )
        elif step == "symlink_planted_at_sibling_name":
            monkeypatch.setattr(rp.secrets, "token_hex", lambda n=8: "00112233aabbccdd")
            os.symlink(outside, dest.with_name(aside_name))
            box = {"fired": True}  # planted up front; asserted below that rename met it
            probes.append(box)
        elif step == "enospc_rename_then_atomic_writer_before_unlink":
            real_rename = rp.os.rename
            box = {"fired": False, "unlink_armed": False}

            def rename(a, b, *x, **k):
                if Path(a) == dest and ".trw-" in str(b):
                    raise OSError(errno.ENOSPC, "No space left on device")
                return real_rename(a, b, *x, **k)

            monkeypatch.setattr(rp.os, "rename", rename)
            probes.append(
                race_after(
                    monkeypatch, target=dest, op="unlink", when="before", interloper=lambda: atomic_replace(dest, W)
                )
            )

    notes, exc = _run(repo, snap, arm)
    if "fd" in held:
        held["fd"].close()
    monkeypatch.undo()
    for p in probes:
        fired = p.fired if hasattr(p, "fired") else p["fired"]
        assert fired, "UNTESTED: probe never fired"
    shas = _shas(repo)
    print(
        f"\ninplace {step}: exc={exc!r} dest={dest.read_bytes()[:20] if dest.exists() else None} notes={[n[:120] for n in notes]}"
    )
    assert outside.read_bytes() == OUTSIDE
    assert not outside.is_symlink()
    assert _sha(U) in shas, "user's pre-update bytes not in the project"
    if step != "baseline_inplace_clear" and step != "symlink_planted_at_sibling_name":
        assert _sha(W) in shas, f"writer's bytes lost at {step}"
    if step == "symlink_planted_at_sibling_name":
        assert not dest.with_name(aside_name).exists() and not dest.with_name(aside_name).is_symlink()


# ---- os.sync retry --------------------------------------------------------------------------


@pytest.mark.parametrize("second_ok", [True, False])
def test_sync_retry(initialized_repo, monkeypatch, tmp_path_factory, second_ok) -> None:
    from trw_mcp.bootstrap import _restore_proof as rp

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    real = shutil.copyfileobj
    calls = {"n": 0, "sync": 0}

    def flaky(fsrc, fdst, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1 or (not second_ok and calls["n"] == 2):
            fdst.write(fsrc.read()[:100])
            fdst.flush()
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fsrc, fdst, *a, **k)

    real_sync = os.sync

    def sync():
        calls["sync"] += 1
        real_sync()

    def arm():
        monkeypatch.setattr(rp.shutil, "copyfileobj", flaky)
        monkeypatch.setattr(rp.os, "sync", sync)

    notes, exc = _run(repo, snap, arm)
    monkeypatch.undo()
    assert calls["n"] >= 2 and calls["sync"] == 1, f"UNTESTED: retry path not taken {calls}"
    state = dest.read_bytes() if dest.exists() else None
    print(
        f"\nsync second_ok={second_ok}: calls={calls} exc={exc!r} dest_len={None if state is None else len(state)} notes={[n[:120] for n in notes]}"
    )
    assert state in (None, U)
    assert _sha(U) in _shas(repo)
    if second_ok:
        assert state == U


# ---- partial-write unlink: inode swap -------------------------------------------------------


@pytest.mark.parametrize(
    "when",
    [
        "before_lstat",
        pytest.param(
            "between_lstat_and_unlink",
            marks=pytest.mark.xfail(  # skip-category: tracked-defect
                strict=True, reason="KI: inherent lstat-then-unlink TOCTOU in the partial-write cleanup (CHANGELOG)"
            ),
        ),
    ],
)
def test_partial_unlink_inode_swap(initialized_repo, monkeypatch, tmp_path_factory, when) -> None:
    from trw_mcp.bootstrap import _restore_proof as rp

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    real = shutil.copyfileobj
    box = {"fired": False}
    other = dest.with_name("other-writer.tmp")

    def swap():
        other.write_bytes(W)
        os.replace(other, dest)  # someone else's file now holds the name
        box["fired"] = True

    def fail_midway(fsrc, fdst, *a, **k):
        if box.get("failed"):
            return real(fsrc, fdst, *a, **k)
        box["failed"] = True
        fdst.write(fsrc.read()[:100])
        fdst.flush()
        if when == "before_lstat":
            swap()
        raise OSError(errno.EIO, "Input/output error")

    def arm():
        monkeypatch.setattr(rp.shutil, "copyfileobj", fail_midway)
        if when == "between_lstat_and_unlink":
            real_lstat = rp.os.lstat
            st = {"armed": False}

            def lstat(p, *a, **k):
                out = real_lstat(p, *a, **k)
                import inspect

                if not box["fired"] and Path(p) == dest and inspect.stack()[1].function == "_unlink_if_ours":
                    swap()
                return out

            monkeypatch.setattr(rp.os, "lstat", lstat)

    notes, exc = _run(repo, snap, arm)
    monkeypatch.undo()
    assert box["fired"], "UNTESTED: swap never fired"
    shas = _shas(repo)
    print(
        f"\nswap {when}: exc={exc!r} dest={dest.read_bytes()[:24] if dest.exists() else None} notes={[n[:120] for n in notes]}"
    )
    assert _sha(U) in shas
    assert _sha(W) in shas, f"someone else's file deleted by the partial-write unlink ({when})"


# ---- retry record ---------------------------------------------------------------------------


def _records_dir() -> Path:
    from trw_mcp.bootstrap._refused_restore import _records_dir as rd

    d = rd(create=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def userdir(tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("trwuser")
    monkeypatch.setenv("TRW_USER_DIR", str(d))
    return d


def _printed_copy(line: str) -> str:
    """The cp command an r7 copy-now line prints (the off-volume alternative, when present, is dropped)."""
    return line.split("then run: ", 1)[1].split(" (or copy it off", 1)[0]


def _update_snapshot(repo: Path | None = None) -> Path:
    """The shape update-project creates (mkdtemp with its prefix under the temp dir); removed by the test."""

    from trw_mcp.bootstrap._refused_restore import new_snapshot_dir

    snap = new_snapshot_dir(repo)  # W1 r9: as update-project creates it (owner marker for *repo*)
    _SNAPSHOTS.append(snap)
    return snap


_SNAPSHOTS: list[Path] = []


@pytest.fixture(autouse=True)
def _remove_snapshots():
    yield
    while _SNAPSHOTS:
        shutil.rmtree(_SNAPSHOTS.pop(), ignore_errors=True)


def test_record_hostile_snapshot_outside(initialized_repo, tmp_path_factory, userdir) -> None:
    """A record naming this project but a snapshot dir OUTSIDE any TRW snapshot: nothing outside may be deleted."""
    from trw_mcp.bootstrap._refused_restore import retry_refused

    repo = initialized_repo
    victim = tmp_path_factory.mktemp("victim_dir")
    (victim / "x").write_bytes(b"x\n")
    (victim / "precious.txt").write_bytes(OUTSIDE)
    rec = _records_dir() / "20260101T000000Z-1-deadbeef.json"
    rec.write_text(json.dumps({"v": 1, "project": os.path.realpath(repo), "snapshot": str(victim), "rels": ["x"]}))
    notes: list[str] = []
    retry_refused(repo, notes)
    print(f"\nhostile snapshot: victim_exists={victim.exists()} notes={notes}")
    assert victim.exists() and (victim / "precious.txt").read_bytes() == OUTSIDE, (
        "retry record rmtree'd a dir outside the snapshot"
    )


def test_record_hostile_rel_traversal(initialized_repo, tmp_path_factory, userdir) -> None:
    from trw_mcp.bootstrap._refused_restore import retry_refused

    repo = initialized_repo
    base = tmp_path_factory.mktemp("trav")
    snap = base / "snap"
    snap.mkdir()
    secret = base / "secret.txt"
    secret.write_bytes(OUTSIDE)
    rec = _records_dir() / "20260101T000000Z-2-deadbeef.json"
    rec.write_text(
        json.dumps({"v": 1, "project": os.path.realpath(repo), "snapshot": str(snap), "rels": ["../secret.txt"]})
    )
    notes: list[str] = []
    retry_refused(repo, notes)
    print(
        f"\ntraversal: secret_exists={secret.exists()} notes={notes} copied_into_project={_sha(OUTSIDE) in _shas(repo)}"
    )
    assert secret.exists()


@pytest.mark.parametrize("payload", ["missing_snapshot", "list", "rels_string", "not_json"])
def test_record_corrupt_does_not_crash_update(initialized_repo, userdir, payload) -> None:
    from trw_mcp.bootstrap import _update_project as up

    repo = initialized_repo
    p = os.path.realpath(repo)
    body = {
        "missing_snapshot": json.dumps({"v": 1, "project": p, "rels": ["a"]}),
        "list": json.dumps([p]),
        "rels_string": json.dumps({"v": 1, "project": p, "snapshot": "/nonexistent-trw-snap", "rels": "abc"}),
        "not_json": "{oops",
    }[payload]
    (_records_dir() / f"20260101T000000Z-3-{payload}.json").write_text(body)
    exc = None
    try:
        result = up.update_project(repo, ide="claude-code")
    except Exception as e:
        exc = e
        result = None
    print(f"\ncorrupt {payload}: exc={exc!r} errors={None if result is None else result['errors'][:2]}")
    assert exc is None, f"a corrupt retry record crashes update-project: {exc!r}"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
def test_record_unwritable_userdir(initialized_repo, tmp_path_factory, monkeypatch) -> None:
    from trw_mcp.bootstrap._refused_restore import record_refused

    d = tmp_path_factory.mktemp("ro_user")
    monkeypatch.setenv("TRW_USER_DIR", str(d))
    os.chmod(d, 0o500)
    try:
        out = record_refused(initialized_repo, Path("/nonexistent-snap"), ["a"])
    finally:
        os.chmod(d, 0o700)
    print(f"\nunwritable userdir: record={out}")
    assert out is None


def test_record_purged_and_legit_move(initialized_repo, tmp_path_factory, userdir) -> None:
    """Legit record: one rel present (moved into trash, never over a user file), one purged."""
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    repo = initialized_repo
    snap = _update_snapshot(repo)  # W1 r7/r9: a record is trusted only for this project's update snapshot
    (snap / REL).parent.mkdir(parents=True)
    (snap / REL).write_bytes(U)
    (repo / REL).parent.mkdir(parents=True, exist_ok=True)
    (repo / REL).write_bytes(W)  # the user's current file at that name must not be overwritten
    rec = record_refused(repo, snap, [REL, ".claude/purged.json"])
    assert rec is not None
    notes: list[str] = []
    retry_refused(repo, notes)
    print(f"\nlegit: notes={notes} rec_exists={rec.exists()} snap_exists={snap.exists()}")
    assert (repo / REL).read_bytes() == W
    assert _sha(U) in _shas(repo / ".trw" / "trash")
    assert not rec.exists()


def test_record_dangling_symlink_counted_as_purged(initialized_repo, tmp_path_factory, userdir) -> None:
    """A parked link in the snapshot whose target is absent: exists() is False, so it is reported 'purged' and rmtree'd."""
    from trw_mcp.bootstrap._refused_restore import record_refused, retry_refused

    repo = initialized_repo
    snap = _update_snapshot(repo)
    (snap / ".claude").mkdir()
    os.symlink("/nonexistent-target-of-user-link", snap / ".claude" / "link.md")
    rec = record_refused(repo, snap, [".claude/link.md"])
    notes: list[str] = []
    retry_refused(repo, notes)
    # rglob("*"), not rglob("link.md"): on Python 3.11 a literal last segment is matched with exists(), which
    # follows the link, so a dangling one is never yielded and a kept link read as discarded.
    found = [p for p in (repo / ".trw").rglob("*") if p.name == "link.md" and p.is_symlink()]
    print(f"\ndangling: notes={notes} saved={found} snap_exists={snap.exists()}")
    assert found or (snap / ".claude" / "link.md").is_symlink(), "the user's symlink was discarded as 'purged'"


# ---- real volume: copy-now command as printed, then the next update's retry ------------------


def _fill(volume: Path) -> Path:
    filler = volume / f"filler-{os.getpid()}"
    fd = os.open(filler, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        for size in (1 << 20, 1 << 16, 1 << 12, 512, 1):
            chunk = b"\0" * size
            while True:
                try:
                    if os.write(fd, chunk) < size:
                        break
                except OSError:  # trw-fail-silent-allow: the volume is full, so filling stops
                    break
    finally:
        os.close(fd)
    return filler


@pytest.mark.skipif(
    not os.environ.get("KI1_FULLVOL"), reason="needs a scratch volume in KI1_FULLVOL"
)  # skip-category: opt-in
def test_REAL_copy_now_and_retry(initialized_repo, monkeypatch, userdir) -> None:
    import subprocess

    from trw_mcp.bootstrap import _update_project as up

    vol = Path(os.environ["KI1_FULLVOL"])
    repo = vol / f"cn-{os.getpid()}"
    shutil.copytree(initialized_repo, repo, symlinks=True)
    shutil.rmtree(repo / ".git")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "x",
        "GIT_AUTHOR_EMAIL": "x@x",
        "GIT_COMMITTER_NAME": "x",
        "GIT_COMMITTER_EMAIL": "x@x",
    }
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
    s = repo / ".claude" / "settings.json"
    data = json.loads(s.read_text())
    data["userKey"] = "my uncommitted edit"
    data.get("hooks", {}).pop("SessionStart", None)
    s.write_text(json.dumps(data, indent=2) + "\n")
    user_bytes = s.read_bytes()
    real = up._restore_dirty_files
    filler = vol / f"filler-{os.getpid()}"

    def fill_then_restore(*a, **k):
        _fill(vol)
        return real(*a, **k)

    monkeypatch.setattr(up, "_restore_dirty_files", fill_then_restore)
    try:
        result = up.update_project(repo, ide="claude-code")
        monkeypatch.setattr(up, "_restore_dirty_files", real)
        in_proj = _sha(user_bytes) in _shas(repo)
        cps = [_printed_copy(e) for e in result["errors"] if "then run: cp -n " in e]  # W1 r7 wording
        print(f"\nCOPYNOW {vol.name}: user_in_project={in_proj} errors={[e[:200] for e in result['errors']]}")
        recs = list((userdir / "refused-restores").glob("*.json")) if (userdir / "refused-restores").exists() else []
        print(f"COPYNOW records={len(recs)} cps={cps}")
        if in_proj:
            return
        assert cps, "user's bytes not in project and no copy-now command printed"
        for c in cps:
            r = subprocess.run(c, shell=True, capture_output=True, text=True)
            print(f"COPYNOW run-while-full rc={r.returncode} err={r.stderr.strip()[-90:]}")
        filler.unlink(missing_ok=True)
        for c in cps:
            r = subprocess.run(c, shell=True, capture_output=True, text=True)
            print(f"COPYNOW run-after-free rc={r.returncode} err={r.stderr.strip()[-90:]}")
        pre = Path(str(s) + ".pre-update")
        print(f"COPYNOW pre-update holds user bytes={pre.exists() and pre.read_bytes() == user_bytes}")
        assert pre.read_bytes() == user_bytes
        pre.unlink()  # test the record path on its own
        r2 = up.update_project(repo, ide="claude-code")
        moved = [w for w in r2["warnings"] + r2["errors"] if "pre-update" in w]
        recs2 = list((userdir / "refused-restores").glob("*.json"))
        print(f"COPYNOW retry warnings={[m[:200] for m in moved]} records_left={len(recs2)} errors={r2['errors'][:2]}")
        assert _sha(user_bytes) in _shas(repo / ".trw" / "trash") or (s.exists() and s.read_bytes() == user_bytes)
    finally:
        filler.unlink(missing_ok=True)
        shutil.rmtree(repo, ignore_errors=True)


@pytest.mark.skipif(
    not os.environ.get("KI1_FULLVOL"), reason="needs a scratch volume in KI1_FULLVOL"
)  # skip-category: opt-in
def test_REAL_N8_copy_now_and_retry(initialized_repo, tmp_path_factory, monkeypatch, userdir) -> None:
    import subprocess

    from trw_mcp.bootstrap import _update_project as up
    from trw_mcp.bootstrap._update_transaction import _surface_files
    from trw_mcp.bootstrap._utils import _DATA_DIR

    vol = Path(os.environ["KI1_FULLVOL"])
    repo = vol / f"cn8-{os.getpid()}"
    shutil.copytree(initialized_repo, repo, symlinks=True)
    data = tmp_path_factory.mktemp("data") / "data"
    shutil.copytree(_DATA_DIR, data)
    for p in data.rglob("*.sh"):
        p.write_bytes(p.read_bytes() + b"\n# bump\n")
    before = {r: (repo / r).read_bytes() for r in _surface_files(repo) if (repo / r).is_file()}
    filler = vol / f"filler-{os.getpid()}"

    def fill_then_fail(*a, **k):
        _fill(vol)
        raise OSError(errno.ENOSPC, "No space left on device", ".trw/managed-artifacts.yaml")

    real_ew = up.enforce_and_write_manifest
    monkeypatch.setattr(up, "enforce_and_write_manifest", fill_then_fail)
    try:
        result = up.update_project(repo, ide="claude-code", data_dir=data)
        monkeypatch.setattr(up, "enforce_and_write_manifest", real_ew)
        lost = sorted(r for r in before if not (repo / r).exists() or (repo / r).read_bytes() != before[r])
        cps = [_printed_copy(e) for e in result["errors"] if "then run: cp -n " in e]  # W1 r7 wording
        recs = sorted((userdir / "refused-restores").glob("*.json")) if (userdir / "refused-restores").exists() else []
        print(f"\nCOPYNOW8 {vol.name}: lost={lost} cps={len(cps)} records={len(recs)}")
        for r in lost:
            print(f"COPYNOW8 {r}: at_name={'absent' if not (repo / r).exists() else 'other'}")
        if not lost:
            pytest.skip("N8 did not reproduce the refusal on this run")  # skip-category: opt-in
        assert len(cps) == len(lost), "a lost file is not named with a copy-now command"
        snap_paths = [Path(json.loads(recs[0].read_text())["snapshot"])] if recs else []
        for c in cps:
            r = subprocess.run(c, shell=True, capture_output=True, text=True)
            print(f"COPYNOW8 run-while-full rc={r.returncode} err={r.stderr.strip()[-90:]}")
        filler.unlink(missing_ok=True)
        for c in cps:
            r = subprocess.run(c, shell=True, capture_output=True, text=True)
            print(f"COPYNOW8 run-after-free rc={r.returncode} err={r.stderr.strip()[-90:]}")
        ok = all(Path(str(repo / r) + ".pre-update").read_bytes() == before[r] for r in lost)
        print(f"COPYNOW8 pre-update copies hold the pre-update bytes: {ok}")
        assert ok
        for r in lost:
            Path(str(repo / r) + ".pre-update").unlink()
        r2 = up.update_project(repo, ide="claude-code", data_dir=data)
        moved = [w for w in r2["warnings"] if "pre-update" in w]
        recs2 = sorted((userdir / "refused-restores").glob("*.json"))
        trash = _shas(repo / ".trw" / "trash")
        print(
            f"COPYNOW8 retry: warnings={[m[:160] for m in moved]} records_left={len(recs2)} "
            f"snapshot_left={[p.exists() for p in snap_paths]} all_in_trash={all(_sha(before[r]) in trash for r in lost)} errors={r2['errors'][:2]}"
        )
        assert all(_sha(before[r]) in trash for r in lost)
        assert not recs2
    finally:
        filler.unlink(missing_ok=True)
        shutil.rmtree(repo, ignore_errors=True)
