"""ki1-race-review minimal repros: each test FAILS on f647810526 by losing a writer's bytes."""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401
from ._fs_hazards import atomic_replace, race_after
from .test_hook_family_coherence import _refuse_trw_after_capturing_ig, _two_lib_family, _update

REL = ".claude/hooks/lib-intent-guard.sh"
W = b"#!/bin/sh\n# concurrent writer\n"


def _anywhere(root: Path, data: bytes) -> bool:
    return any(p.is_file() and not p.is_symlink() and p.read_bytes() == data for p in root.rglob("*"))


def _snap(repo: Path, factory: pytest.TempPathFactory) -> Path:
    snap = factory.mktemp("snap")
    (snap / REL).parent.mkdir(parents=True)
    shutil.copy2(repo / REL, snap / REL)
    return snap


def test_R1_rollback_after_settle_deletes_writer(initialized_repo: Path, monkeypatch) -> None:
    """Real update_project: writer races the link-back (proven path keeps W at the name), then the manifest
    write hits ENOSPC -> result.errors -> _restore_transaction_snapshot wipes .claude and W is gone."""
    from trw_mcp.bootstrap import _update_project as up

    repo, hooks = initialized_repo, initialized_repo / ".claude" / "hooks"
    for n in ("lib-trw.sh", "lib-intent-guard.sh"):
        (hooks / n).write_bytes((hooks / n).read_bytes() + b"# my tweak\n")
    _refuse_trw_after_capturing_ig(monkeypatch)
    probe = race_after(
        monkeypatch, target=repo / REL, op="link", when="before", interloper=lambda: (repo / REL).write_bytes(W)
    )

    def enospc(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(up, "enforce_and_write_manifest", enospc)
    result = up.update_project(repo, ide="claude-code")
    assert probe.fired and result["errors"]
    assert _anywhere(repo, W), "writer's only copy deleted by the rollback"


def test_R2_rollback_deletes_the_named_set_aside(initialized_repo: Path, monkeypatch) -> None:
    """Same, set-aside path: the warning names lib-intent-guard.sh.concurrent-<pid>-<ns>, the rollback deletes it."""
    from trw_mcp.bootstrap import _update_project as up

    repo, hooks = initialized_repo, initialized_repo / ".claude" / "hooks"
    for n in ("lib-trw.sh", "lib-intent-guard.sh"):
        (hooks / n).write_bytes((hooks / n).read_bytes() + b"# my tweak\n")
    _refuse_trw_after_capturing_ig(monkeypatch)
    trash = repo / ".trw" / "trash"

    def writer_and_fault() -> None:
        (repo / REL).write_bytes(W)
        trash.chmod(0)  # the r3 premise: an unusable trash

    probe = race_after(monkeypatch, target=repo / REL, op="link", when="before", interloper=writer_and_fault)
    monkeypatch.setattr(
        up, "enforce_and_write_manifest", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full"))
    )
    try:
        result = up.update_project(repo, ide="claude-code")
    finally:
        trash.chmod(0o755)
    assert probe.fired and result["errors"]
    # r4 design: no set-aside beside the name; the rollback's proof check refuses (unusable trash) or captures,
    # and either way the writer's bytes survive.
    assert _anywhere(repo, W), "the writer's bytes were deleted by the rollback"


def test_R3_writer_saves_again_before_dirty_restore(initialized_repo: Path, monkeypatch, tmp_path_factory) -> None:
    """Unproven capture (capture data unreadable) -> W moved to trash, name empty. The writer saves W2 any time
    before preserve_uncommitted_changes, which unlinks W2 and copies the snapshot over it."""
    from trw_mcp.bootstrap._version_manifest import preserve_uncommitted_changes

    repo = initialized_repo
    _refuse_trw_after_capturing_ig(monkeypatch)
    _before, manifest = _two_lib_family(repo)
    snap = _snap(repo, tmp_path_factory)
    trash = repo / ".trw" / "trash"

    def writer() -> None:
        (repo / REL).write_bytes(W)
        for p in trash.rglob("data"):
            p.chmod(0)

    probe = race_after(monkeypatch, target=repo / REL, op="link", when="before", interloper=writer)
    result = _update(repo, manifest)
    monkeypatch.undo()
    for p in trash.rglob("data"):
        p.chmod(0o644)
    w2 = b"#!/bin/sh\n# writer saved again\n"
    atomic_replace(repo / REL, w2)
    preserve_uncommitted_changes(repo, snap, {REL}, manifest, result)
    assert probe.fired
    assert _anywhere(repo, w2), "W2 unlinked by the dirty restore"


def test_R4_lexists_swallows_eacces(initialized_repo: Path, monkeypatch, tmp_path_factory) -> None:
    """_hook_family line 174: os.path.lexists is False on EACCES -> settle skipped -> restore unlinks W."""
    from trw_mcp.bootstrap import _hook_family as hf
    from trw_mcp.bootstrap._version_manifest import preserve_uncommitted_changes

    repo = initialized_repo
    _refuse_trw_after_capturing_ig(monkeypatch)
    _before, manifest = _two_lib_family(repo)
    snap = _snap(repo, tmp_path_factory)
    real = hf.os.path.lexists
    once = {"armed": False}

    def lexists(p):
        if once["armed"] and os.fspath(p) == str(repo / REL):
            once["armed"] = False
            return False  # what os.path.lexists returns when lstat raises EACCES
        return real(p)

    def writer() -> None:
        (repo / REL).write_bytes(W)
        once["armed"] = True

    monkeypatch.setattr(hf.os.path, "lexists", lexists)
    race_after(monkeypatch, target=repo / REL, op="link", when="before", interloper=writer)
    result = _update(repo, manifest)
    monkeypatch.undo()
    preserve_uncommitted_changes(repo, snap, {REL}, manifest, result)
    assert _anywhere(repo, W), "writer's only copy deleted"


def test_R5_set_aside_rename_replaces_existing_name(initialized_repo: Path, monkeypatch, tmp_path_factory) -> None:
    """_hook_family line 111-113: os.rename onto <name>.concurrent-<pid>-<ns> silently replaces a file there."""

    repo = initialized_repo
    _refuse_trw_after_capturing_ig(monkeypatch)
    _before, manifest = _two_lib_family(repo)
    # r4 design: no rename onto a set-aside name exists any more; a file at that name must still survive.
    aside = repo / ".claude" / "hooks" / f"lib-intent-guard.sh.concurrent-{os.getpid()}-42"
    victim = b"bytes already at the set-aside name\n"
    trash = repo / ".trw" / "trash"

    def writer() -> None:
        (repo / REL).write_bytes(W)
        aside.write_bytes(victim)
        trash.chmod(0)

    race_after(monkeypatch, target=repo / REL, op="link", when="before", interloper=writer)
    try:
        _update(repo, manifest)
    finally:
        trash.chmod(0o755)
    assert _anywhere(repo, victim), "rename replaced the existing file"
    assert _anywhere(repo, W), "the writer's bytes were lost"
