"""An explicit uninstall moves TRW's own unchanged captures to the macOS system Trash (lead ruling, option e).

Nothing is unlinked: whole capture folders are renamed from ``.trw/trash`` into a fresh
``~/.Trash/trw-<project>-<utc>-<hex8>/`` folder, so a late write through a held fd survives there. A capture
that cannot move (another device, no system Trash, not macOS) stays in ``.trw/trash`` and is listed. A plain
uninstall of an untouched project therefore leaves no ``.trw`` behind, as it did before the trash existed.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the system Trash move is macOS-only")

_TRW = b"trw bytes\n"
_SHA = hashlib.sha256(_TRW).hexdigest()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "home"
    (fake / ".Trash").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake))
    return fake


def _capture(tmp_path: Path) -> tuple[Path, Path]:
    from trw_mcp.bootstrap._safe_remove import remove_if_hash

    root = tmp_path / "proj"
    victim = root / ".claude" / "thing.md"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(_TRW)
    outcome = remove_if_hash(victim, root, _SHA)
    assert outcome.status == "removed" and outcome.retained_at is not None
    return root, outcome.retained_at


def _os_trash(home: Path) -> list[Path]:
    return sorted(home.glob(".Trash/trw-*/*/data"))


def test_move_renames_the_capture_folder_into_the_system_trash(tmp_path: Path, home: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import move_captures_to_os_trash

    root, data = _capture(tmp_path)
    dest, kept = move_captures_to_os_trash(root, [data])
    assert kept == []
    assert dest is not None and dest.parent == home / ".Trash" and dest.name.startswith("trw-proj-")
    moved = dest / data.parent.name / "data"
    assert moved.read_bytes() == _TRW
    assert (dest / data.parent.name / "meta.json").is_file()
    assert list((root / ".trw" / "trash").iterdir()) == []


def test_a_late_fd_write_after_the_move_lands_in_the_system_trash(tmp_path: Path, home: Path) -> None:
    """Nothing is unlinked: the inode a writer holds keeps a name in ~/.Trash."""
    from tests._fs_hazards import open_fd_writer
    from trw_mcp.bootstrap._safe_remove import move_captures_to_os_trash, remove_if_hash

    root = tmp_path / "proj"
    victim = root / ".claude" / "thing.md"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(_TRW)
    with open_fd_writer(victim) as writer:
        outcome = remove_if_hash(victim, root, _SHA)
        assert outcome.retained_at is not None
        dest, _kept = move_captures_to_os_trash(root, [outcome.retained_at])
        writer.write(b"late write\n")
    assert dest is not None
    assert [p.read_bytes() for p in _os_trash(home)] == [b"late write\n"]


def test_exdev_keeps_the_capture_in_trw_trash(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _safe_remove

    root, data = _capture(tmp_path)
    real_rename = _safe_remove.os.rename

    def cross_device(src: object, dst: object, *a: object, **kw: object) -> None:
        if kw.get("dst_dir_fd") is not None and str(src) == data.parent.name:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_rename(src, dst, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(_safe_remove.os, "rename", cross_device)
    dest, kept = _safe_remove.move_captures_to_os_trash(root, [data])
    assert dest is None
    assert kept == [(data, "the system Trash is on another device")]
    assert data.read_bytes() == _TRW
    assert list((home / ".Trash").iterdir()) == []  # the empty folder we made is gone again


def test_no_system_trash_keeps_the_capture(tmp_path: Path, home: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import move_captures_to_os_trash

    (home / ".Trash").rmdir()
    root, data = _capture(tmp_path)
    dest, kept = move_captures_to_os_trash(root, [data])
    assert dest is None
    assert kept and kept[0][0] == data and kept[0][1].startswith("no usable system Trash")
    assert data.read_bytes() == _TRW


@pytest.mark.parametrize("where", ["outside_trash", "bad_folder_name", "not_data"])
def test_only_trw_capture_folders_move(tmp_path: Path, home: Path, where: str) -> None:
    from trw_mcp.bootstrap._safe_remove import move_captures_to_os_trash

    root, data = _capture(tmp_path)
    if where == "outside_trash":
        target = root / "elsewhere" / data.parent.name / "data"
    elif where == "bad_folder_name":
        target = root / ".trw" / "trash" / "not-a-capture" / "data"
    else:
        target = data.parent / "meta.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(_TRW)
    dest, kept = move_captures_to_os_trash(root, [target])
    assert dest is None
    assert kept == [(target, "not a TRW capture folder")]
    assert target.exists() and data.read_bytes() == _TRW


def test_a_symlinked_trw_trash_is_not_followed(tmp_path: Path, home: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import move_captures_to_os_trash

    root, data = _capture(tmp_path)
    real_trash = tmp_path / "real-trash"
    (root / ".trw" / "trash").rename(real_trash)
    (root / ".trw" / "trash").symlink_to(real_trash, target_is_directory=True)
    dest, kept = move_captures_to_os_trash(root, [data])
    assert dest is None and kept
    assert (real_trash / data.parent.name / "data").read_bytes() == _TRW


def _init(root: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    assert not init_project(root, ide="claude-code")["errors"]
    return root


def _uninstall(root: Path) -> None:
    from trw_mcp.server import _subcommands_lifecycle as lifecycle

    lifecycle._run_uninstall(
        argparse.Namespace(target_dir=str(root), dry_run=False, yes=True, delete_memory=False, keep_memory=False)
    )


@pytest.mark.usefixtures("no_memory_daemon")
def test_uninstall_of_an_untouched_project_leaves_no_trw(
    tmp_path: Path, home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Red on DISTILL-HOOK-SAFE: every unchanged TRW file stayed in .trw/trash, so .trw was kept."""
    root = _init(tmp_path / "proj")
    _uninstall(root)
    out = capsys.readouterr().out
    assert not (root / ".trw").exists(), out
    assert "Moved to .trw/trash" not in out
    moved = _os_trash(home)
    assert moved
    (line,) = [ln for ln in out.splitlines() if "to the system Trash: " in ln]
    assert line.startswith(f"  Moved {len(moved)} unchanged TRW file(s) to the system Trash: {home / '.Trash'}")


@pytest.mark.usefixtures("no_memory_daemon")
def test_uninstall_keeps_an_edited_hook_and_nothing_else(tmp_path: Path, home: Path) -> None:
    root = _init(tmp_path / "proj")
    hooks = sorted(p for p in (root / ".claude" / "hooks").iterdir() if p.is_file() and p.suffix == ".sh")
    assert hooks
    edited = hooks[0]
    edited.write_bytes(edited.read_bytes() + b"# my edit\n")
    mine = edited.read_bytes()
    _uninstall(root)
    assert edited.read_bytes() == mine
    assert not (root / ".trw").exists()
    assert list((root / ".claude" / "hooks").iterdir()) == [edited]
    assert all(p.read_bytes() != mine for p in _os_trash(home))


@pytest.mark.usefixtures("no_memory_daemon")
def test_uninstall_exdev_falls_back_to_trw_trash(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.bootstrap import _safe_remove

    root = _init(tmp_path / "proj")
    real_rename = _safe_remove.os.rename

    def cross_device(src: object, dst: object, *a: object, **kw: object) -> None:
        if src == dst and kw.get("dst_dir_fd") is not None:  # the folder-into-system-Trash move
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        real_rename(src, dst, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(_safe_remove.os, "rename", cross_device)
    _uninstall(root)
    out = capsys.readouterr().out
    assert "to the system Trash" not in out
    listed = [ln for ln in out.splitlines() if ln.startswith("  Moved to .trw/trash: ")]
    assert listed and len(listed) == len(list((root / ".trw" / "trash").glob("*/data")))
    assert _os_trash(home) == []


@pytest.mark.usefixtures("no_memory_daemon")
def test_uninstall_withdraws_the_cc03_hook_registration(tmp_path: Path, home: Path) -> None:
    """Red since DISTILL-HOOK-SAFE: the CC-03 script was removed but settings.json still ran it."""
    import json

    from trw_mcp.bootstrap._claude_code_distill_channels import apply_cc03_hook_registration

    root = _init(tmp_path / "proj")
    config = root / ".trw" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "\ncc03_hook_enabled: true\n", encoding="utf-8")
    apply_cc03_hook_registration(root)
    settings = root / ".claude" / "settings.json"
    assert "pre-tool-distill-hint.sh" in settings.read_text(encoding="utf-8")
    _uninstall(root)
    if settings.exists():
        commands = [
            hook.get("command", "")
            for groups in json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}).values()
            for group in groups
            for hook in group.get("hooks", [])
        ]
        assert not any("pre-tool-distill-hint.sh" in c for c in commands), commands
