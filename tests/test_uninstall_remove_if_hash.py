"""Uninstall's hash-recorded file removal goes through ``remove_if_hash`` (HB-2).

Planning proves ownership from a hash taken earlier; the act must not delete bytes saved since. The
file is captured into ``.trw/trash``, re-hashed there, and linked back on a mismatch; nothing is
unlinked, so an edit, an open-fd write, or a swapped-in entry always keeps its bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import pytest

from tests._fs_hazards import (
    assert_user_bytes_preserved,
    atomic_replace,
    open_fd_writer,
    snapshot_user_bytes,
    swap_to_dir,
    swap_to_symlink,
)

_TRW = b"trw bytes\n"


def _victim(tmp_path: Path) -> tuple[Path, Path, object]:
    from trw_mcp.bootstrap._uninstall_manifest import KeyDisposition

    root = tmp_path / "proj"
    victim = root / ".claude" / "thing.md"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(_TRW)
    disposition = KeyDisposition("thing.md", victim, "remove", recorded_hash=hashlib.sha256(_TRW).hexdigest())
    return root, victim, disposition


def _trash(root: Path) -> list[bytes]:
    trash = root / ".trw" / "trash"
    return [p.read_bytes() for p in trash.glob("*/data")] if trash.is_dir() else []


def test_unedited_file_moves_to_trash_and_its_record_drops(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._uninstall_manifest import apply_removal

    root, victim, d = _victim(tmp_path)
    result: dict[str, list[str]] = {}
    removed, errors = apply_removal([d], result, root)  # type: ignore[list-item]
    assert (removed, errors) == ({"thing.md"}, 0)
    assert not victim.exists()
    assert _trash(root) == [_TRW]
    assert result["trashed"] == [str(victim)]


def test_edit_saved_after_planning_survives_and_keeps_the_record(tmp_path: Path) -> None:
    """Red on the old code: safe_remove deleted by name whatever bytes were there at the act."""
    from trw_mcp.bootstrap._uninstall_manifest import apply_removal

    root, victim, d = _victim(tmp_path)
    victim.write_bytes(b"my edit after planning\n")  # planning already matched the TRW hash
    result: dict[str, list[str]] = {}
    removed, errors = apply_removal([d], result, root)  # type: ignore[list-item]
    assert (removed, errors) == (set(), 0)
    assert victim.read_bytes() == b"my edit after planning\n"
    assert any("edited" in p for p in result["preserved"])
    assert "trashed" not in result


def test_open_fd_write_after_removal_keeps_its_bytes(tmp_path: Path) -> None:
    """Red on the old code: the unlink detached the inode, so the late write was lost."""
    from trw_mcp.bootstrap._uninstall_manifest import apply_removal

    root, victim, d = _victim(tmp_path)
    with open_fd_writer(victim) as writer:
        apply_removal([d], {}, root)  # type: ignore[list-item]
        writer.write(b"late write\n")
    assert _trash(root) == [b"late write\n"]


@pytest.mark.parametrize("kind", ["symlink", "dir", "replace"])
def test_swap_after_the_recheck_never_loses_user_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from trw_mcp.bootstrap import _uninstall_manifest

    root, victim, d = _victim(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_bytes(_TRW)  # same bytes as the record: a hash match must not reach it
    real = _uninstall_manifest.remove_if_hash
    before: list[dict[str, list[str]]] = [{}]

    def swap_then_remove(path: Path, root_: Path, expected: str, **kw: object):  # type: ignore[no-untyped-def]
        if kind == "symlink":
            swap_to_symlink(path, outside)
        elif kind == "dir":
            swap_to_dir(path)
            (path / "user.txt").write_bytes(b"user dir bytes")
        else:
            atomic_replace(path, b"racer bytes")
        before[0] = snapshot_user_bytes(tmp_path)
        return real(path, root_, expected, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(_uninstall_manifest, "remove_if_hash", swap_then_remove)
    result: dict[str, list[str]] = {}
    removed, _errors = _uninstall_manifest.apply_removal([d], result, root)  # type: ignore[list-item]
    assert removed == set()
    assert outside.read_bytes() == _TRW
    assert_user_bytes_preserved(before[0], tmp_path)
    assert os.path.lexists(victim)
    if kind == "replace":
        assert victim.read_bytes() == b"racer bytes"


def test_failed_read_of_the_capture_puts_the_file_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _safe_remove, _uninstall_manifest

    root, victim, d = _victim(tmp_path)
    real_open = _safe_remove.os.open

    def failing_open(path: object, flags: int, *a: object, **kw: object) -> int:
        if path == "data" and not flags & (os.O_WRONLY | os.O_RDWR):
            raise PermissionError(13, "injected read failure")
        return real_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(_safe_remove.os, "open", failing_open)
    result: dict[str, list[str]] = {}
    removed, _errors = _uninstall_manifest.apply_removal([d], result, root)  # type: ignore[list-item]
    assert removed == set()
    assert victim.read_bytes() == _TRW


@pytest.mark.usefixtures("no_memory_daemon")
def test_uninstall_summarises_removed_files_and_keeps_no_trash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Uninstall moves TRW's own unchanged captures to the system Trash (lead ruling 2026-09-29): one line."""
    from trw_mcp.bootstrap import init_project
    from trw_mcp.server import _subcommands_lifecycle as lifecycle

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    lifecycle._run_uninstall(
        argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True, delete_memory=False, keep_memory=True)
    )
    out = capsys.readouterr().out
    assert "  Moved to .trw/trash: " not in out
    summary = [line for line in out.splitlines() if " unchanged TRW file(s) to the system Trash: " in line]
    assert len(summary) == 1 and summary[0].startswith("  Moved ")
    assert list((tmp_path / ".trw" / "trash").glob("*/data")) == []


def test_update_project_names_each_file_moved_to_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp import bootstrap
    from trw_mcp.server import _subcommands

    def fake_update(*_a: object, **_k: object) -> dict[str, list[str]]:
        return {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
            "trashed": [".claude/hooks/old.sh"],
        }

    monkeypatch.setattr(bootstrap, "update_project", fake_update)
    args = argparse.Namespace(target_dir=str(tmp_path), pip_install=False, dry_run=False, ide=None)
    with pytest.raises(SystemExit):
        _subcommands._run_update_project(args)
    assert "Moved to .trw/trash: .claude/hooks/old.sh (unchanged TRW file; see doctor)" in capsys.readouterr().out


def test_folder_moved_mid_removal_reports_where_the_copy_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _uninstall_manifest
    from trw_mcp.bootstrap._safe_remove import Removal

    root, victim, d = _victim(tmp_path)
    copy = root / ".trw" / "trash" / "x" / "data"
    reason = "bytes differ from the recorded hash; put back; the folder moved during removal"
    monkeypatch.setattr(
        _uninstall_manifest, "remove_if_hash", lambda p, r, e, **k: Removal("k", p, "kept", None, copy, reason)
    )
    result: dict[str, list[str]] = {}
    removed, errors = _uninstall_manifest.apply_removal([d], result, root)  # type: ignore[list-item]
    assert (removed, errors) == (set(), 1)
    assert "preserved" not in result
    assert result["errors"] == [f"{victim}: kept ({reason}); a copy is in {copy}"]


def test_capture_refused_is_an_error_that_keeps_the_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _uninstall_manifest
    from trw_mcp.bootstrap._safe_remove import Removal

    root, victim, d = _victim(tmp_path)
    monkeypatch.setattr(
        _uninstall_manifest,
        "remove_if_hash",
        lambda p, r, e, **k: Removal("k", p, "kept", None, None, "trash on another device"),
    )
    result: dict[str, list[str]] = {}
    removed, errors = _uninstall_manifest.apply_removal([d], result, root)  # type: ignore[list-item]
    assert (removed, errors) == (set(), 1)
    assert result["errors"] == [f"{victim}: kept (trash on another device)"]
    assert victim.read_bytes() == _TRW


def test_trw_gitignore_template_ignores_the_trash() -> None:
    from importlib import resources

    rules = resources.files("trw_mcp").joinpath("data/gitignore.txt").read_text(encoding="utf-8").splitlines()
    assert "trash/" in rules
