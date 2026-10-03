"""GUARDED-COPY part of the r7 attack suite (c1-c5). ki1-race-review r7: attacks on r7's retry-record validation and on GUARDED-COPY's O_EXCL publish.

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


def _rec(repo: Path, snap: str, rels: list[str], tag: str) -> Path:
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


# ---- (c) GUARDED-COPY O_EXCL publish -------------------------------------------------------------

HOOK = ".claude/hooks/session-start.sh"


def _absent_race(repo, monkeypatch, plant):
    from trw_mcp.bootstrap import _template_updater
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = repo / HOOK
    dest.unlink()
    real = _template_updater._is_user_modified
    fired = {"n": 0}

    def check_then_race(path, *a, **k):
        out = real(path, *a, **k)
        if Path(path) == dest and not fired["n"]:
            fired["n"] += 1
            plant(dest)
        return out

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    # Undo only this patch: a bare monkeypatch.undo() also reverts the autouse HOME redirect (DAEMON-LEAK-INTERMITTENT).
    with monkeypatch.context() as mp:
        mp.setattr(_template_updater, "_is_user_modified", check_then_race)
        _update_hooks(repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")
    return dest, fired["n"], result


def test_c1_symlink_planted_before_create(initialized_repo, monkeypatch, tmp_path_factory) -> None:
    outside = tmp_path_factory.mktemp("outside") / "target.sh"
    outside.write_bytes(OUTSIDE)
    os.chmod(outside, 0o600)
    dest, n, result = _absent_race(initialized_repo, monkeypatch, lambda d: d.symlink_to(outside))
    print(
        f"\nFIRED:c1 n={n} link={dest.is_symlink()} mode={oct(os.stat(outside).st_mode)} result={ {k: v for k, v in result.items() if v} }"
    )
    assert n == 1 and dest.is_symlink() and outside.read_bytes() == OUTSIDE
    assert os.stat(outside).st_mode & 0o777 == 0o600, "outside target chmod-ed through the planted link"


def test_c1b_dangling_symlink_planted_before_create(initialized_repo, monkeypatch, tmp_path_factory) -> None:
    ghost = tmp_path_factory.mktemp("outside") / "ghost.sh"
    dest, n, result = _absent_race(initialized_repo, monkeypatch, lambda d: d.symlink_to(ghost))
    print(
        f"\nFIRED:c1b n={n} link={dest.is_symlink()} ghost_created={ghost.exists()} result={ {k: v for k, v in result.items() if v} }"
    )
    assert n == 1 and dest.is_symlink() and not ghost.exists()


def test_c2_directory_planted_before_create(initialized_repo, monkeypatch) -> None:
    def plant(d: Path) -> None:
        d.mkdir()
        (d / "inner").write_bytes(W)

    dest, n, result = _absent_race(initialized_repo, monkeypatch, plant)
    print(f"\nFIRED:c2 n={n} isdir={dest.is_dir()} result={ {k: v for k, v in result.items() if v} }")
    assert n == 1 and dest.is_dir() and (dest / "inner").read_bytes() == W


def test_c3_file_appears_after_check_before_create(initialized_repo, monkeypatch) -> None:
    from trw_mcp.bootstrap import _restore_proof
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / HOOK
    dest.unlink()
    real = _restore_proof.copy_back_exclusive
    fired = {"n": 0}

    def late(src, d, rel, notes, **kw):  # W1 r8: copy_back_exclusive takes add_mode
        if Path(d) == dest and not fired["n"]:
            fired["n"] += 1
            dest.write_bytes(W)
        return real(src, d, rel, notes, **kw)

    monkeypatch.setattr(_restore_proof, "copy_back_exclusive", late)
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")
    print(f"\nFIRED:c3 n={fired['n']} kept={dest.read_bytes() == W} modified={result['modified'][:1]}")
    assert fired["n"] == 1 and dest.read_bytes() == W
    assert any("session-start.sh" in m for m in result["modified"])


def test_c4_ledger_and_later_update(initialized_repo, monkeypatch) -> None:
    """TRW's create is in the run ledger; a racer's file is NOT; a second full update keeps W."""
    from trw_mcp._checkout_write import recording_writes, written_this_run
    from trw_mcp.bootstrap import _template_updater
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._update_project import update_project
    from trw_mcp.bootstrap._utils import _DATA_DIR

    repo = initialized_repo
    created = repo / HOOK
    created.unlink()
    raced = repo / ".claude/hooks/pre-compact.sh"
    raced.unlink()
    real = _template_updater._is_user_modified

    def race(path, *a, **k):
        out = real(path, *a, **k)
        if Path(path) == raced and not raced.exists():
            raced.write_bytes(W)
        return out

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    # Undo only this patch: a bare monkeypatch.undo() also reverted the autouse HOME redirect, so the update below
    # started a daemon in the worker's session home (DAEMON-LEAK-INTERMITTENT).
    with monkeypatch.context() as mp, recording_writes():
        mp.setattr(_template_updater, "_is_user_modified", race)
        _update_hooks(repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")
        own = written_this_run(created)
        theirs = written_this_run(raced)
    print(
        f"\nFIRED:c4 own={own and own[:12]} theirs={theirs} created_sha={_sha(created.read_bytes())[:12]} W_kept={raced.read_bytes() == W}"
    )
    assert own == _sha(created.read_bytes()) and theirs is None and raced.read_bytes() == W
    res2 = update_project(repo, ide="claude-code")
    print(f"FIRED:c4b second update: W_kept={raced.read_bytes() == W} errors={res2.get('errors', [])[:2]}")
    assert raced.read_bytes() == W, "a later update mistook the racer's file for TRW's"


def test_c5_swap_between_create_and_chmod(initialized_repo, monkeypatch, tmp_path_factory) -> None:
    """After the O_EXCL create, dest is swapped for a symlink before the by-name chmod."""
    from trw_mcp.bootstrap import _restore_proof
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    outside = tmp_path_factory.mktemp("outside") / "secret"
    outside.write_bytes(OUTSIDE)
    os.chmod(outside, 0o600)
    dest = initialized_repo / HOOK
    dest.unlink()
    real = _restore_proof.copy_back_exclusive
    fired = {"n": 0}

    def swap(src, d, rel, notes, **kw):
        ok = real(src, d, rel, notes, **kw)
        if Path(d) == dest and ok and not fired["n"]:
            fired["n"] += 1
            dest.unlink()
            dest.symlink_to(outside)
        return ok

    monkeypatch.setattr(_restore_proof, "copy_back_exclusive", swap)
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")
    mode = os.stat(outside).st_mode & 0o777
    print(f"\nFIRED:c5 n={fired['n']} outside_mode={oct(mode)}")
    assert fired["n"] == 1 and outside.read_bytes() == OUTSIDE
    assert mode == 0o600, "chmod followed a swapped-in symlink to a file outside the project"
