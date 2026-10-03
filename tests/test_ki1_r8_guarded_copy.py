"""GUARDED-COPY part of the r8 attack suite (g1-g3b). ki1-race-review r8: attacks on the user-dir snapshot location, samefile trust, no-follow retry, .bad naming,
and GUARDED-COPY add_mode/fchmod.

Every probe prints FIRED:<name> when its race/arm actually ran; a probe that never fires is UNTESTED.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
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


# ---- g: GUARDED-COPY add_mode / fchmod ---------------------------------------------------------------

HOOK = ".claude/hooks/session-start.sh"


def _hooks_with(repo, monkeypatch, patch):
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    patch()
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    _update_hooks(repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")
    monkeypatch.undo()
    return result


def test_r8_g1_swap_inside_create_before_fchmod(initialized_repo, monkeypatch, tmp_path_factory) -> None:
    from trw_mcp.bootstrap import _restore_proof

    outside = tmp_path_factory.mktemp("g1") / "secret"
    outside.write_bytes(OUTSIDE)
    os.chmod(outside, 0o600)
    dest = initialized_repo / HOOK
    dest.unlink()
    real_copy = _restore_proof.shutil.copyfileobj
    box = {"n": 0}

    def copy_then_swap(fsrc, fdst, *a, **k):
        out = real_copy(fsrc, fdst, *a, **k)
        if not box["n"] and dest.exists() and not dest.is_symlink():
            box["n"] += 1
            dest.unlink()
            dest.symlink_to(outside)
        return out

    res = _hooks_with(
        initialized_repo, monkeypatch, lambda: monkeypatch.setattr(_restore_proof.shutil, "copyfileobj", copy_then_swap)
    )
    mode = stat.S_IMODE(os.stat(outside).st_mode)
    print(
        f"\nFIRED:g1 n={box['n']} outside_mode={oct(mode)} outside_bytes_ok={outside.read_bytes() == OUTSIDE} errors={res['errors'][:2]}"
    )
    assert box["n"] == 1 and mode == 0o600 and outside.read_bytes() == OUTSIDE


def test_r8_g2_hardlink_swap_before_utime(initialized_repo, monkeypatch, tmp_path_factory) -> None:
    from trw_mcp.bootstrap import _restore_proof

    outside = initialized_repo.parent / f"g2-outside-{os.getpid()}"  # same volume, so a hard link works
    outside.write_bytes(OUTSIDE)
    os.utime(outside, (1_000_000_000, 1_000_000_000))
    dest = initialized_repo / HOOK
    dest.unlink()
    real_utime = _restore_proof.os.utime
    box = {"n": 0}

    def swap_then_utime(p, *a, **k):
        if not box["n"] and (isinstance(p, int) or Path(p) == dest):  # W1 r9: utime is now by fd
            box["n"] += 1
            dest.unlink()
            os.link(outside, dest)
        return real_utime(p, *a, **k)

    try:
        res = _hooks_with(
            initialized_repo, monkeypatch, lambda: monkeypatch.setattr(_restore_proof.os, "utime", swap_then_utime)
        )
        mt = os.stat(outside).st_mtime
        mode = stat.S_IMODE(os.stat(outside).st_mode)
        print(
            f"\nFIRED:g2 n={box['n']} outside_mtime_changed={mt != 1_000_000_000} outside_mode={oct(mode)} bytes_ok={outside.read_bytes() == OUTSIDE} errors={res['errors'][:2]}"
        )
        assert box["n"] == 1 and outside.read_bytes() == OUTSIDE and not (mode & 0o111)
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.parametrize("um", [0o022, 0o077])
def test_r8_g3_new_hook_mode_vs_umask(initialized_repo, um) -> None:
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / HOOK
    dest.unlink()
    old = os.umask(um)
    try:
        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")
    finally:
        os.umask(old)
    mode = stat.S_IMODE(os.stat(dest).st_mode)
    src_mode = (
        stat.S_IMODE(os.stat(_DATA_DIR / "hooks" / Path(HOOK).name).st_mode)
        if (_DATA_DIR / "hooks" / Path(HOOK).name).exists()
        else None
    )
    print(f"\nFIRED:g3 umask={oct(um)} new_hook_mode={oct(mode)} src_mode={src_mode and oct(src_mode)}")
    assert mode & 0o100


@pytest.mark.parametrize("um", [0o022, 0o077])
def test_r8_g3b_legacy_update_or_report_mode_vs_umask(tmp_path, um) -> None:
    """Reference: the pre-GUARDED-COPY path (_update_or_report) for a new hook, same umask."""
    from trw_mcp.bootstrap._template_updater import _update_or_report
    from trw_mcp.bootstrap._utils import _DATA_DIR

    src = next(p for p in (_DATA_DIR / "hooks").iterdir() if p.suffix == ".sh")
    dest = tmp_path / "hooks" / src.name
    dest.parent.mkdir()
    old = os.umask(um)
    try:
        _update_or_report(src, dest, {"errors": []}, make_executable=True)
    finally:
        os.umask(old)
    mode = stat.S_IMODE(os.stat(dest).st_mode)
    print(f"\nFIRED:g3b umask={oct(um)} legacy_new_hook_mode={oct(mode)}")
    assert dest.read_bytes() == src.read_bytes()
    assert mode & 0o100, f"a new hook is owner-executable under umask {um:#o}"
