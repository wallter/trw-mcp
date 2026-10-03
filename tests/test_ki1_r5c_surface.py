"""ki1-race-review r5c: probes re-aimed at r5c's real steps. Every probe must fire (else the test errors)."""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401
from ._fs_hazards import atomic_replace, open_fd_writer, race_after

pytestmark = pytest.mark.integration

REL = ".claude/my-settings.json"
U = b'{"mine": 1}\n' * 50  # the user's pre-run (snapshot) bytes
T = b'{"trw": 2}\n' * 50  # this run's own write
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


def _run(repo: Path, snap: Path, arm) -> tuple[list[str], BaseException | None]:
    """This run writes T over the dirty path (recorded); arm() installs probes; then the dirty restore."""
    from trw_mcp._checkout_write import recording_writes, write_checkout_file
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    notes: list[str] = []
    exc = None
    with recording_writes():
        write_checkout_file(repo, repo / REL, T)
        arm()
        try:
            _restore_transaction_file(repo, snap, REL, notes)
        except (OSError, KeyboardInterrupt) as e:  # trw-fail-silent-allow: the test records the error and asserts on it
            exc = e
    return notes, exc


def _must_fire(*probes) -> None:
    for p in probes:
        fired = p.fired if hasattr(p, "fired") else p["fired"]
        assert fired, "UNTESTED: probe never fired"


# ---- (c) a writer at each r5c step of remove_proven_or_keep ----------------------------------


@pytest.mark.parametrize(
    "step",
    [
        "before_capture_rename_atomic",
        "before_capture_rename_inplace",
        "after_capture_rename_new_name",
        "fd_write_after_capture_before_verify",
        "fd_write_after_verify_before_second_rehash",
        pytest.param(
            "fd_write_after_second_rehash_before_drop",
            marks=pytest.mark.xfail(
                strict=True, reason="KI: documented residual window (lead r5c #3)"
            ),  # skip-category: tracked-defect
        ),
        "inplace_then_third_writer_takes_name_before_linkback",
        "name_taken_before_copy_back_create",
        "symlink_planted_before_copy_back_create",
    ],
)
def test_c(initialized_repo, monkeypatch, tmp_path_factory, step) -> None:
    from trw_mcp.bootstrap import _restore_proof as rp
    from trw_mcp.bootstrap import _trash

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    outside = tmp_path_factory.mktemp("out") / "precious.txt"
    outside.write_bytes(OUTSIDE)
    probes: list = []
    held: dict[str, object] = {}

    def inplace(data: bytes) -> None:
        with open(dest, "r+b") as fh:
            fh.truncate(0)
            fh.write(data)

    def arm() -> None:
        if step == "before_capture_rename_atomic":
            probes.append(
                race_after(
                    monkeypatch,
                    target=dest.name,
                    op="rename",
                    when="before",
                    interloper=lambda: atomic_replace(dest, W),
                )
            )
        elif step == "before_capture_rename_inplace":
            probes.append(
                race_after(monkeypatch, target=dest.name, op="rename", when="before", interloper=lambda: inplace(W))
            )
        elif step == "after_capture_rename_new_name":
            probes.append(
                race_after(
                    monkeypatch, target=dest.name, op="rename", when="after", interloper=lambda: dest.write_bytes(W)
                )
            )
        elif step.startswith("fd_write"):
            held["fd"] = open_fd_writer(dest)  # opened on TRW's file before the restore starts
            if step == "fd_write_after_capture_before_verify":
                probes.append(
                    race_after(
                        monkeypatch, target=dest.name, op="rename", when="after", interloper=lambda: held["fd"].write(W)
                    )
                )
            elif step == "fd_write_after_verify_before_second_rehash":
                real = _trash.remove_if_hash
                box = {"fired": False}

                def wrapped(*a, **k):
                    out = real(*a, **k)
                    box["fired"] = True
                    held["fd"].write(W)
                    return out

                monkeypatch.setattr(_trash, "remove_if_hash", wrapped)
                probes.append(box)
            else:
                real_rmtree = shutil.rmtree
                box = {"fired": False}

                def rmtree(path, *a, **k):
                    if not box["fired"] and "/.trw/trash/" in str(path):
                        box["fired"] = True
                        held["fd"].write(W)  # lands after the second re-hash, before the drop
                    return real_rmtree(path, *a, **k)

                monkeypatch.setattr(rp.shutil, "rmtree", rmtree)
                probes.append(box)
        elif step == "inplace_then_third_writer_takes_name_before_linkback":
            probes.append(
                race_after(monkeypatch, target=dest.name, op="rename", when="before", interloper=lambda: inplace(W))
            )
            probes.append(
                race_after(
                    monkeypatch,
                    target=dest.name,
                    op="link",
                    when="before",
                    interloper=lambda: dest.write_bytes(b"third writer\n"),
                )
            )
        elif step == "name_taken_before_copy_back_create":
            probes.append(
                race_after(monkeypatch, target=dest, op="open", when="before", interloper=lambda: dest.write_bytes(W))
            )
        elif step == "symlink_planted_before_copy_back_create":
            probes.append(
                race_after(
                    monkeypatch, target=dest, op="open", when="before", interloper=lambda: os.symlink(outside, dest)
                )
            )

    notes, exc = _run(repo, snap, arm)
    if "fd" in held:
        held["fd"].close()
    monkeypatch.undo()
    _must_fire(*probes)
    shas = _shas(repo)
    print(
        f"\nc {step}: exc={exc!r} dest={'symlink' if dest.is_symlink() else (dest.read_bytes()[:30] if dest.exists() else None)} notes={[n[:110] for n in notes]}"
    )
    assert exc is None
    assert outside.read_bytes() == OUTSIDE, "snapshot bytes written through a symlink outside the project"
    assert _sha(U) in shas, "user's pre-update bytes not anywhere in the project"
    if step != "symlink_planted_before_copy_back_create":
        assert _sha(W) in shas, f"writer's bytes lost at {step}"


# ---- crash (KeyboardInterrupt) at r5c steps ----------------------------------------------------


_CRASH_KI = pytest.mark.xfail(  # skip-category: tracked-defect
    strict=True, reason="KI: component level only; update_project's finally-rollback restores it (lead r5c)"
)


@pytest.mark.parametrize(
    "where",
    [
        pytest.param("after_capture_rename", marks=_CRASH_KI),
        pytest.param("after_drop_before_copy_back", marks=_CRASH_KI),
    ],
)
def test_crash(initialized_repo, monkeypatch, tmp_path_factory, where) -> None:

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    probes: list = []

    def boom():
        raise KeyboardInterrupt

    def arm():
        if where == "after_capture_rename":
            probes.append(race_after(monkeypatch, target=dest.name, op="rename", when="after", interloper=boom))
        else:
            probes.append(race_after(monkeypatch, target=dest, op="open", when="before", interloper=boom))

    notes, exc = _run(repo, snap, arm)
    monkeypatch.undo()
    _must_fire(*probes)
    shas = _shas(repo)
    print(
        f"\ncrash {where}: exc={exc!r} dest_exists={dest.exists()} T_in_tree={_sha(T) in shas} U_in_tree={_sha(U) in shas}"
    )
    assert isinstance(exc, KeyboardInterrupt)
    # after_drop: TRW's own write is gone (fine) and U has not been put back: it is only in the snapshot ($TMPDIR)
    assert _sha(U) in shas or dest.exists(), "a crash left the user's name EMPTY with U only in $TMPDIR"


# ---- (a) partial write / write failure midway in copy_back_exclusive ---------------------------


@pytest.mark.parametrize("trash_ok", [True, False])
def test_a_write_fails_midway(initialized_repo, monkeypatch, tmp_path_factory, trash_ok) -> None:
    from trw_mcp.bootstrap import _restore_proof as rp

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    real = shutil.copyfileobj
    box = {"fired": False}

    def half_then_enospc(fsrc, fdst, *a, **k):
        if not box["fired"]:
            box["fired"] = True
            data = fsrc.read()
            fdst.write(data[: len(data) // 2])
            fdst.flush()
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(fsrc, fdst, *a, **k)

    def arm():
        monkeypatch.setattr(rp.shutil, "copyfileobj", half_then_enospc)
        if not trash_ok:
            real_mkdtemp = rp.tempfile.mkdtemp

            def mkdtemp(*a, **k):
                raise OSError(errno.ENOSPC, "No space left on device")

            monkeypatch.setattr(rp.tempfile, "mkdtemp", mkdtemp)

    notes, exc = _run(repo, snap, arm)
    monkeypatch.undo()
    _must_fire(box)
    half = dest.read_bytes() if dest.exists() else None
    print(
        f"\na_midway trash_ok={trash_ok}: exc={exc!r} dest_len={None if half is None else len(half)} of {len(U)} notes={[n[:120] for n in notes]}"
    )
    assert half in (None, U), f"a HALF-WRITTEN file ({len(half)} of {len(U)} bytes) was left at the user's name"
    assert _sha(U) in _shas(repo), "U not preserved anywhere in the project"


# ---- (c) the pre-update preservation path under ENOSPC: end to end ----------------------------


def _real_git(repo: Path) -> None:
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
    not os.environ.get("KI1_FULLVOL"), reason="needs a small scratch volume in KI1_FULLVOL"
)  # skip-category: opt-in
def test_c_REAL_full_volume_dirty_restore(tmp_path_factory) -> None:
    """No patching: the project sits on a small volume that is completely full when the dirty restore runs."""
    from trw_mcp._checkout_write import recording_writes, write_checkout_file
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    vol = Path(os.environ["KI1_FULLVOL"])
    repo = vol / f"proj-{os.getpid()}"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".trw").mkdir()
    snap = tmp_path_factory.mktemp("snap")
    (snap / REL).parent.mkdir(parents=True)
    (snap / REL).write_bytes(U)
    notes: list[str] = []
    exc = None
    filler = None
    try:
        with recording_writes():
            write_checkout_file(repo, repo / REL, T)
            filler = vol / f"filler-{os.getpid()}"
            _fill(vol)
            try:
                _restore_transaction_file(repo, snap, REL, notes)
            except OSError as e:  # trw-fail-silent-allow: the test records the error and asserts on it
                exc = e
        dest = repo / REL
        state = dest.read_bytes() if dest.exists() else None
        shas = _shas(repo)
        print(
            f"\nREAL full {vol.name}: exc={exc!r} dest={'U' if state == U else 'T' if state == T else (None if state is None else f'{len(state)}B other')} "
            f"U_in_project={_sha(U) in shas} notes={[n[:120] for n in notes]}"
        )
        assert _sha(U) in shas, (
            "on a full disk the user's uncommitted file is not in the project (only in the snapshot)"
        )
    finally:
        if filler is not None:
            filler.unlink()
        shutil.rmtree(repo, ignore_errors=True)


@pytest.mark.skipif(
    not os.environ.get("KI1_FULLVOL"), reason="needs a scratch volume in KI1_FULLVOL"
)  # skip-category: opt-in
def test_N8_REAL_disk_fills_then_rollback(initialized_repo, tmp_path_factory, monkeypatch) -> None:
    """No concurrent writer. The project lives on a small real volume; the disk fills up late in the run (the
    manifest write fails ENOSPC) and the rollback must still restore the snapshot completely."""
    from trw_mcp.bootstrap import _update_project as up
    from trw_mcp.bootstrap._update_transaction import _surface_files
    from trw_mcp.bootstrap._utils import _DATA_DIR

    vol = Path(os.environ["KI1_FULLVOL"])
    repo = vol / f"n8-{os.getpid()}"
    shutil.copytree(initialized_repo, repo, symlinks=True)
    data = tmp_path_factory.mktemp("data") / "data"
    shutil.copytree(_DATA_DIR, data)
    for p in data.rglob("*.sh"):
        p.write_bytes(p.read_bytes() + b"\n# bump\n")

    def tree():
        return {r: _sha((repo / r).read_bytes()) for r in _surface_files(repo) if (repo / r).is_file()}

    before = tree()
    filler = vol / f"filler-{os.getpid()}"

    def fill_then_fail(*a, **k):
        _fill(vol)
        raise OSError(errno.ENOSPC, "No space left on device", ".trw/managed-artifacts.yaml")

    monkeypatch.setattr(up, "enforce_and_write_manifest", fill_then_fail)
    try:
        result = up.update_project(repo, ide="claude-code", data_dir=data)
        after = tree()
        diff = sorted(r for r in set(before) | set(after) if before.get(r) != after.get(r))
        print(f"\nN8REAL {vol.name}: errors={[e[:140] for e in result['errors']]} diff={len(diff)} {diff[:4]}")
        assert not any("Failed to restore" in e for e in result["errors"]), "rollback abandoned on a full disk"
        assert not diff
    finally:
        filler.unlink(missing_ok=True)
        shutil.rmtree(repo, ignore_errors=True)


@pytest.mark.skipif(
    not os.environ.get("KI1_FULLVOL"), reason="needs a scratch volume in KI1_FULLVOL"
)  # skip-category: opt-in
def test_dirty_REAL_disk_full_at_dirty_restore_e2e(initialized_repo, monkeypatch) -> None:
    """Real update_project, real git, an uncommitted edit to .claude/settings.json that TRW rewrites; the disk
    fills up just before the dirty-file restore. No concurrent writer."""
    import json

    from trw_mcp.bootstrap import _update_project as up

    vol = Path(os.environ["KI1_FULLVOL"])
    repo = vol / f"dirty-{os.getpid()}"
    shutil.copytree(initialized_repo, repo, symlinks=True)
    _real_git(repo)
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
        at_name = s.read_bytes() if s.exists() else None
        print(
            f"\nDIRTYREAL {vol.name}: errors={[e[:150] for e in result['errors']]} "
            f"name_holds={'USER' if at_name == user_bytes else 'other' if at_name else None} "
            f"user_in_project={_sha(user_bytes) in _shas(repo)}"
        )
        assert _sha(user_bytes) in _shas(repo), "user's uncommitted settings.json not in the project"
    finally:
        filler.unlink(missing_ok=True)
        shutil.rmtree(repo, ignore_errors=True)


@pytest.mark.parametrize("where", ["after_capture_rename", "before_copy_back_create"])
def test_crash_e2e_ctrl_c_during_dirty_restore(initialized_repo, monkeypatch, where) -> None:
    """Ctrl-C inside the dirty restore of a real update_project: the finally-rollback must put the user's file back."""
    import json

    from trw_mcp.bootstrap import _update_project as up

    repo = initialized_repo
    _real_git(repo)
    s = repo / ".claude" / "settings.json"
    data = json.loads(s.read_text())
    data["userKey"] = "my uncommitted edit"
    data.get("hooks", {}).pop("SessionStart", None)
    s.write_text(json.dumps(data, indent=2) + "\n")
    user_bytes = s.read_bytes()
    real = up._restore_dirty_files
    box = {"probe": None}

    def boom():
        raise KeyboardInterrupt

    def armed(*a, **k):
        if where == "after_capture_rename":
            box["probe"] = race_after(monkeypatch, target=s.name, op="rename", when="after", interloper=boom)
        else:
            box["probe"] = race_after(monkeypatch, target=s, op="open", when="before", interloper=boom)
        return real(*a, **k)

    monkeypatch.setattr(up, "_restore_dirty_files", armed)
    with pytest.raises(KeyboardInterrupt):
        up.update_project(repo, ide="claude-code")
    monkeypatch.undo()
    assert box["probe"] is not None and box["probe"].fired, "UNTESTED: probe never fired"
    print(
        f"\nCTRLC {where}: name_holds_user={s.exists() and s.read_bytes() == user_bytes} in_project={_sha(user_bytes) in _shas(repo)}"
    )
    assert _sha(user_bytes) in _shas(repo)
