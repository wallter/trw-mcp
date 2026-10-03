"""ki1-race-review r5: attacks on remove_proven_or_keep / _remove_own_write / copy_back_exclusive."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401
from ._fs_hazards import atomic_replace, open_fd_writer, race_after

pytestmark = pytest.mark.integration

REL = ".claude/my-settings.json"
U = b'{"mine": 1}\n'  # the user's pre-run (snapshot) bytes
T = b'{"trw": 2}\n'  # this run's own write
W = b"concurrent writer bytes\n"
VICTIM = b"bytes that already lived at the sibling name\n"


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
    return snap, repo / REL


def _restore(repo: Path, snap: Path, write_t: bool = True) -> list[str]:
    """This run wrote T over the dirty path (recorded), then the dirty restore puts U back."""
    from trw_mcp._checkout_write import recording_writes, write_checkout_file
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    notes: list[str] = []
    with recording_writes():
        if write_t:
            write_checkout_file(repo, repo / REL, T)
        _restore_transaction_file(repo, snap, REL, notes)
    return notes


class _OsNoLink:
    """os, as _restore_proof sees it on a filesystem without hard links (exFAT, SMB)."""

    def __init__(self, err: int) -> None:
        self.err = err

    def __getattr__(self, name: str):
        return getattr(os, name)

    def link(self, *a, **k):
        raise OSError(self.err, os.strerror(self.err))


def _sib(dest: Path, n: int = 0) -> Path:
    return dest.with_name(f".{dest.name}.trw-{os.getpid()}-{n}")


def _race_capture(monkeypatch, dest: Path, op: str, when: str, interloper):
    """r5c: the clear is remove_if_hash's dir_fd-relative ``os.rename``/``os.link``, so match on the bare name."""
    from ._fs_hazards import Probe, _fire

    probe = Probe()
    real = getattr(os, op)

    def wrapper(*args, **kwargs):
        hit = dest.name in (args[0], args[1]) and ("src_dir_fd" in kwargs)
        if probe._busy or probe.fired or not hit:
            return real(*args, **kwargs)
        if when == "before":
            _fire(probe, interloper)
        out = real(*args, **kwargs)
        if when == "after":
            _fire(probe, interloper)
        return out

    monkeypatch.setattr(os, op, wrapper)
    return probe


# ---- (a) copy_back_exclusive ------------------------------------------------------------------


@pytest.mark.parametrize("what", ["file", "dir", "symlink"])
def test_a1_name_created_between_clear_and_link(initialized_repo, monkeypatch, tmp_path_factory, what) -> None:
    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    outside = tmp_path_factory.mktemp("out") / "t"
    outside.write_bytes(W)

    def land():
        if what == "file":
            dest.write_bytes(W)
        elif what == "dir":
            dest.mkdir()
            (dest / "inner").write_bytes(W)
        else:
            os.symlink(outside, dest)

    probe = race_after(monkeypatch, target=dest, op="open", when="before", interloper=land)  # r5c: O_EXCL create
    notes = _restore(repo, snap)
    assert probe.fired
    assert _sha(W) in _shas(repo) or (what == "symlink" and outside.read_bytes() == W)
    assert _sha(U) in _shas(repo) or any("kept" in n for n in notes)


def test_a2_staged_name_preexisting_user_file(initialized_repo, tmp_path_factory) -> None:
    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    for n in range(2):
        _sib(dest, n).write_bytes(VICTIM + bytes([n]))
    _restore(repo, snap)
    assert dest.read_bytes() == U
    assert all(_sib(dest, n).read_bytes() == VICTIM + bytes([n]) for n in range(2))


@pytest.mark.parametrize("plant", ["file", "symlink_outside"])
def test_a3_staged_name_planted_between_lexists_and_copy2(
    initialized_repo, monkeypatch, tmp_path_factory, plant
) -> None:
    """_free_sibling checks lexists, then shutil.copy2 opens the name with O_TRUNC (no O_EXCL) and follows a
    symlink there."""

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    outside = tmp_path_factory.mktemp("out") / "precious.txt"
    outside.write_bytes(VICTIM)
    fired = {"n": 0}

    def plant_at_dest() -> None:  # r5c: no staged name exists; the create itself is the only open
        fired["n"] += 1
        if plant == "file":
            dest.write_bytes(VICTIM)
        else:
            os.symlink(outside, dest)

    race_after(monkeypatch, target=dest, op="open", when="before", interloper=plant_at_dest)
    _restore(repo, snap)
    monkeypatch.undo()
    assert fired["n"] == 1
    assert outside.read_bytes() == VICTIM, "snapshot bytes were written THROUGH a symlink outside the project"
    assert _sha(VICTIM) in _shas(repo) or plant == "symlink_outside", "the planted file was truncated and deleted"


@pytest.mark.parametrize("err", [errno.ENOTSUP, errno.EPERM, errno.EXDEV])
def test_a4_no_hardlink_filesystem_dirty_restore(initialized_repo, monkeypatch, tmp_path_factory, err) -> None:
    """exFAT / SMB: link() is unsupported. r5's copy-back is link-only: the dirty restore clears TRW's write,
    then cannot put the user's file back."""
    from trw_mcp.bootstrap import _restore_proof as rp

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)

    monkeypatch.setattr(rp, "os", _OsNoLink(err))
    exc = None
    try:
        _restore(repo, snap)
    except OSError as e:
        exc = e
    monkeypatch.undo()
    print(f"\na4 {errno.errorcode[err]}: exc={exc!r} dest_exists={dest.exists()}")
    assert _sha(U) in _shas(repo), "the user's uncommitted file is gone from the project (only in the snapshot)"


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


def test_a4_no_hardlink_filesystem_end_to_end(initialized_repo, monkeypatch) -> None:
    """Real update_project, real git, the user has an uncommitted edit to .claude/settings.json; link() unsupported."""
    from trw_mcp.bootstrap import _restore_proof as rp
    from trw_mcp.bootstrap._update_project import update_project

    repo = initialized_repo
    _real_git(repo)
    s = repo / ".claude" / "settings.json"
    s.write_text(s.read_text().replace("{", '{\n  "userKey": "my uncommitted edit",', 1))
    # make TRW rewrite settings.json this run: drop one TRW hook registration
    import json

    data = json.loads(s.read_text())
    data.get("hooks", {}).pop("SessionStart", None)
    s.write_text(json.dumps(data, indent=2) + "\n")
    user_bytes = s.read_bytes()

    monkeypatch.setattr(rp, "os", _OsNoLink(errno.ENOTSUP))
    result = update_project(repo, ide="claude-code")
    monkeypatch.undo()
    print(f"\na4e2e: errors={[e[:200] for e in result['errors']]} settings_exists={s.exists()}")
    assert _sha(user_bytes) in _shas(repo), "user's uncommitted settings.json left only in $TMPDIR"


# ---- (b) the same-dir rename sibling -----------------------------------------------------------


def test_b1_rename_sibling_preexisting_user_file(initialized_repo, tmp_path_factory) -> None:
    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    _sib(dest, 0).write_bytes(VICTIM)
    _restore(repo, snap)
    assert _sib(dest, 0).read_bytes() == VICTIM and dest.read_bytes() == U


def test_b2_rename_sibling_planted_between_lexists_and_rename(initialized_repo, monkeypatch, tmp_path_factory) -> None:
    """_free_sibling checks lexists, then os.rename(dest, aside) silently replaces a file created there."""
    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    probe = _race_capture(monkeypatch, dest, "rename", "before", lambda: _sib(dest, 0).write_bytes(VICTIM))
    _restore(repo, snap)
    assert probe.fired
    assert _sha(VICTIM) in _shas(repo), "rename onto the sibling name destroyed a file created there"


def _crash_after(real, name, crash):
    def wrapper(*args, **kwargs):
        out = real(*args, **kwargs)
        if args[0] == name and "src_dir_fd" in kwargs:
            crash()
        return out

    return wrapper


@pytest.mark.parametrize("where", ["after_rename_own", "after_rename_foreign"])
def test_b3_crash_mid_clear(initialized_repo, monkeypatch, tmp_path_factory, where) -> None:
    """KeyboardInterrupt right after the rename aside: what is left, and does anything name it?"""
    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)

    def crash():
        raise KeyboardInterrupt

    def foreign_then_capture() -> None:
        if where == "after_rename_foreign":
            atomic_replace(dest, W)  # the writer replaced TRW's write just before the capture

    _race_capture(monkeypatch, dest, "rename", "before", foreign_then_capture)
    monkeypatch.setattr(os, "rename", _crash_after(os.rename, dest.name, crash))
    with pytest.raises(KeyboardInterrupt):
        _restore(repo, snap)
    monkeypatch.undo()
    left = sorted(p.name for p in dest.parent.iterdir() if ".trw-" in p.name)
    print(f"\nb3 {where}: dest_exists={dest.exists()} siblings={left}")
    data = W if where == "after_rename_foreign" else T
    assert _sha(data) in _shas(repo)


# ---- (c) a writer at each step of remove_proven_or_keep ---------------------------------------


@pytest.mark.parametrize(
    "step",
    ["after_lstat", "after_current_hash", "after_rename", "after_rehash_fd_write", "mismatch_then_name_taken"],
)
def test_c_writer_at_each_step(initialized_repo, monkeypatch, tmp_path_factory, step) -> None:

    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    keep: dict[str, object] = {}
    from trw_mcp._checkout_write import recording_writes, write_checkout_file
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    notes: list[str] = []
    with recording_writes():
        write_checkout_file(repo, dest, T)
        if step == "after_lstat":
            p = race_after(
                monkeypatch, target=dest, op="lstat", nth=1, when="after", interloper=lambda: atomic_replace(dest, W)
            )
        elif step == "after_current_hash":
            p = _race_capture(monkeypatch, dest, "rename", "before", lambda: atomic_replace(dest, W))
        elif step == "after_rename":
            p = _race_capture(monkeypatch, dest, "rename", "after", lambda: dest.write_bytes(W))
        elif step == "after_rehash_fd_write":
            keep["fd"] = open_fd_writer(dest)  # a process that opened TRW's file and writes in place later
            p = _race_capture(
                monkeypatch,
                dest,
                "rename",
                "after",  # r5c: nothing is unlinked
                lambda: keep["fd"].write(W),
            )
        else:  # rename moves a writer's in-place edit aside, then another writer takes the name before link-back

            def edit_then():
                with open(dest, "r+b") as fh:
                    fh.truncate(0)
                    fh.write(W)

            _race_capture(monkeypatch, dest, "rename", "before", edit_then)
            p = _race_capture(monkeypatch, dest, "link", "before", lambda: dest.write_bytes(b"third writer\n"))
        _restore_transaction_file(repo, snap, REL, notes)
    if "fd" in keep:
        keep["fd"].close()
    monkeypatch.undo()
    hidden = sorted(x.name for x in dest.parent.iterdir() if ".trw-" in x.name)
    print(
        f"\nc {step}: fired={p.fired} dest={dest.read_bytes() if dest.exists() else None} hidden={hidden} notes={notes}"
    )
    assert p.fired
    assert _sha(W) in _shas(repo), f"writer's bytes lost at step {step}"
    if step == "mismatch_then_name_taken":
        named = [Path(m) for m in re.findall(r"moved to (\S+) before", " ".join(notes))]
        assert not hidden and any(x.is_file() and x.read_bytes() == W for x in named), "W kept at an unnamed path"


# ---- (d) a writer writes bytes EQUAL to the snapshot ------------------------------------------


def test_d_writer_writes_snapshot_equal_bytes(initialized_repo, tmp_path_factory) -> None:
    repo = initialized_repo
    snap, dest = _setup(repo, tmp_path_factory)
    os.chmod(snap / REL, 0o644)
    from trw_mcp._checkout_write import recording_writes, write_checkout_file
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    other = repo / "elsewhere-hardlink.json"
    with recording_writes():
        write_checkout_file(repo, dest, T)
        atomic_replace(dest, U)  # writer writes exactly the snapshot's bytes, mode 0755, shared with another name
        os.chmod(dest, 0o755)
        os.link(dest, other)
        notes: list[str] = []
        _restore_transaction_file(repo, snap, REL, notes)
    print(
        f"\nd: dest={dest.read_bytes()!r} mode={oct(stat.S_IMODE(dest.stat().st_mode))} "
        f"other_mode={oct(stat.S_IMODE(other.stat().st_mode))} notes={notes}"
    )
    assert dest.read_bytes() == U and other.read_bytes() == U


@pytest.mark.parametrize("mode", [0o000, 0o300, 0o500])
@pytest.mark.parametrize("fail", [False, True])
def test_e_unreadable_user_dir_under_claude(initialized_repo, monkeypatch, tmp_path_factory, mode, fail) -> None:
    """W1's r5 finding (fixed in r5b): an unreadable user dir under .claude made _rollback raise."""
    if os.geteuid() == 0:
        pytest.skip("root ignores modes")
    from trw_mcp.bootstrap import _update_project as up

    repo = initialized_repo
    private = repo / ".claude" / "private"
    private.mkdir()
    (private / "secret.txt").write_bytes(VICTIM)
    os.chmod(private, mode)
    if fail:
        monkeypatch.setattr(
            up, "enforce_and_write_manifest", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full"))
        )
    try:
        result = up.update_project(repo, ide="claude-code")
    finally:
        os.chmod(private, 0o755)
    print(f"\ne mode={oct(mode)} fail={fail}: errors={result['errors']}")
    assert (private / "secret.txt").read_bytes() == VICTIM
    assert not any("Failed to restore" in e for e in result["errors"]), "rollback failed"


@pytest.mark.skipif(
    not os.environ.get("KI1_EXFAT"), reason="needs a real exFAT mount in KI1_EXFAT"
)  # skip-category: opt-in
def test_a4_REAL_exfat_dirty_restore(tmp_path_factory) -> None:
    """No patching: a real exFAT volume (macOS returns ENOTSUP for link())."""
    repo = Path(os.environ["KI1_EXFAT"]) / f"proj-{os.getpid()}"
    (repo / ".claude").mkdir(parents=True)
    snap, dest = _setup(repo, tmp_path_factory)
    exc = None
    try:
        _restore(repo, snap)
    except OSError as e:
        exc = e
    print(
        f"\nREAL exFAT: exc={exc!r} dest_exists={dest.exists()} files={sorted(p.name for p in dest.parent.iterdir())}"
    )
    assert _sha(U) in _shas(repo), "user's uncommitted file removed from the project on exFAT"


@pytest.mark.skipif(
    not os.environ.get("KI1_EXFAT"), reason="needs a real exFAT mount in KI1_EXFAT"
)  # skip-category: opt-in
def test_a4_REAL_exfat_update_project(initialized_repo) -> None:
    """No patching: the whole repo on exFAT, real git, an uncommitted user edit TRW rewrites."""
    import json

    from trw_mcp.bootstrap._update_project import update_project

    repo = Path(os.environ["KI1_EXFAT"]) / f"e2e-{os.getpid()}"
    shutil.copytree(initialized_repo, repo, symlinks=False)
    _real_git(repo)
    s = repo / ".claude" / "settings.json"
    data = json.loads(s.read_text())
    data["userKey"] = "my uncommitted edit"
    data.get("hooks", {}).pop("SessionStart", None)
    s.write_text(json.dumps(data, indent=2) + "\n")
    user_bytes = s.read_bytes()
    result = update_project(repo, ide="claude-code")
    print(f"\nREAL exFAT e2e: errors={[e[:220] for e in result['errors']]} settings_exists={s.exists()}")
    assert _sha(user_bytes) in _shas(repo), "user's uncommitted settings.json left only in $TMPDIR"


@pytest.mark.parametrize("mode", [0o000, 0o300, 0o500])
def test_e2_user_dir_becomes_unreadable_mid_run_then_rollback(initialized_repo, monkeypatch, mode) -> None:
    """The dir is fine at snapshot time; the user chmods it during the run; then a late failure rolls back."""
    if os.geteuid() == 0:
        pytest.skip("root ignores modes")
    from trw_mcp.bootstrap import _update_project as up

    repo = initialized_repo
    private = repo / ".claude" / "private"
    private.mkdir()
    (private / "secret.txt").write_bytes(VICTIM)

    def chmod_then_fail(*a, **k):
        os.chmod(private, mode)
        raise OSError(errno.ENOSPC, "full")

    monkeypatch.setattr(up, "enforce_and_write_manifest", chmod_then_fail)
    try:
        result = up.update_project(repo, ide="claude-code")
    finally:
        os.chmod(private, 0o755)
    print(f"\ne2 mode={oct(mode)}: errors={[e[:200] for e in result['errors']]}")
    assert (private / "secret.txt").read_bytes() == VICTIM
    assert not any("Failed to restore" in e for e in result["errors"]), "rollback failed"
