"""GUARDED-COPY-UPDATE-RACE (lead's KI1-RACE review, finding 5): _guarded_copy_update checked that a hook was not
user-modified and then overwrote it, so a writer landing between the check and the write was clobbered (HB-2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.integration

_WRITER = b"#!/bin/sh\n# a concurrent writer saved this between the check and the write\n"


def test_a_writer_landing_after_the_check_is_never_overwritten(initialized_repo: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from trw_mcp.bootstrap import _template_updater
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / ".claude" / "hooks" / "session-start.sh"
    real = _template_updater._is_user_modified
    fired = {"n": 0}

    def check_then_race(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        out = real(path, *args, **kwargs)
        if Path(path) == dest and not fired["n"]:
            fired["n"] += 1
            dest.write_bytes(_WRITER)  # lands right after the check said "not modified"
        return out

    monkeypatch.setattr(_template_updater, "_is_user_modified", check_then_race)
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}

    _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")

    assert fired["n"] == 1
    assert dest.read_bytes() == _WRITER, "the writer's bytes were replaced by the bundled hook"
    assert any("session-start.sh" in m for m in result.get("modified", [])), "the kept file is reported"


def test_an_unchanged_hook_is_still_refreshed_without_trash_noise(initialized_repo: Path) -> None:
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / ".claude" / "hooks" / "session-start.sh"
    old = b"#!/bin/sh\n# what an older TRW shipped\n"
    dest.write_bytes(old)
    import hashlib

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    _update_hooks(
        initialized_repo, _DATA_DIR, result, manifest_hashes={"session-start.sh": hashlib.sha256(old).hexdigest()}
    )

    assert dest.read_bytes() == (_DATA_DIR / "hooks" / "session-start.sh").read_bytes()
    trash = initialized_repo / ".trw" / "trash"
    assert not trash.exists() or not [p for p in trash.rglob("*") if p.is_file() and p.read_bytes() == old]


def test_a_writer_creating_an_absent_hook_after_the_check_is_never_overwritten(  # type: ignore[no-untyped-def]
    initialized_repo: Path, monkeypatch
) -> None:
    """Reviewer test_E: the name was empty at the check (a captured lib), so there was nothing to claim; a writer
    that creates it before the refresh must keep its file, and the refresh must say so."""
    from trw_mcp.bootstrap import _template_updater
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / ".claude" / "hooks" / "session-start.sh"
    dest.unlink()
    real = _template_updater._is_user_modified
    fired = {"n": 0}

    def check_then_race(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        out = real(path, *args, **kwargs)
        if Path(path) == dest and not fired["n"]:
            fired["n"] += 1
            dest.write_bytes(_WRITER)
        return out

    monkeypatch.setattr(_template_updater, "_is_user_modified", check_then_race)
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")

    assert fired["n"] == 1
    assert dest.read_bytes() == _WRITER, "the refresh replaced a file created after its check"
    assert any("session-start.sh" in m for m in result.get("modified", []))


def test_an_absent_hook_is_created_executable_and_recorded_as_this_runs_write(initialized_repo: Path) -> None:
    import hashlib
    import os
    import stat

    from trw_mcp import _checkout_write
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / ".claude" / "hooks" / "session-start.sh"
    dest.unlink()
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
    with _checkout_write.recording_writes():
        _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes={}, ide="claude-code")
        recorded = _checkout_write.written_this_run(dest)
    shipped = (_DATA_DIR / "hooks" / "session-start.sh").read_bytes()
    assert dest.read_bytes() == shipped and os.stat(dest).st_mode & stat.S_IXUSR
    assert recorded == hashlib.sha256(shipped).hexdigest(), "a rollback could not prove this file is its own"


def test_a_hard_link_swapped_in_before_the_times_are_set_keeps_its_mtime(  # type: ignore[no-untyped-def]
    initialized_repo: Path, monkeypatch
) -> None:
    """Lead r8 G-L1: the copy's times are set through its own fd, never by name after close; a name swapped for
    a hard link to another file in between never has that file's mtime changed."""
    import os

    from trw_mcp.bootstrap import _restore_proof
    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / ".claude" / "hooks" / "session-start.sh"
    dest.unlink()
    other = initialized_repo.parent / f"other-{os.getpid()}"
    other.write_bytes(b"someone else's file\n")
    os.utime(other, (1_000_000_000, 1_000_000_000))
    real_close = _restore_proof.os.fstat  # swap right after the create, before any time is set
    fired = {"n": 0}

    def fstat_then_swap(fd):  # type: ignore[no-untyped-def]
        out = real_close(fd)
        if not fired["n"] and dest.exists() and not dest.is_symlink() and dest.stat().st_ino == out.st_ino:
            fired["n"] += 1
            dest.unlink()
            os.link(other, dest)
        return out

    monkeypatch.setattr(_restore_proof.os, "fstat", fstat_then_swap)
    try:
        _update_hooks(
            initialized_repo, _DATA_DIR, {"errors": [], "modified": []}, manifest_hashes={}, ide="claude-code"
        )
        monkeypatch.undo()
        assert fired["n"] == 1
        assert os.stat(other).st_mtime == 1_000_000_000, "the other file's mtime was changed through the swapped name"
    finally:
        other.unlink(missing_ok=True)


def test_a_new_hook_follows_the_umask_like_the_legacy_write_path(initialized_repo: Path) -> None:
    """Lead r8 G-L2: under umask 077 a new hook gets no group/other read or write bits (exec bits are added on
    purpose, as _update_or_report does)."""
    import os
    import stat

    from trw_mcp.bootstrap._template_updater import _update_hooks
    from trw_mcp.bootstrap._utils import _DATA_DIR

    dest = initialized_repo / ".claude" / "hooks" / "session-start.sh"
    dest.unlink()
    old = os.umask(0o077)
    try:
        _update_hooks(
            initialized_repo, _DATA_DIR, {"errors": [], "modified": []}, manifest_hashes={}, ide="claude-code"
        )
    finally:
        os.umask(old)
    mode = stat.S_IMODE(dest.stat().st_mode)
    assert mode & 0o066 == 0, oct(mode)
    assert mode & stat.S_IXUSR
