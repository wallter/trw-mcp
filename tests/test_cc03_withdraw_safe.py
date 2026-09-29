"""CC-03 hook withdrawal (``cc03_hook_enabled`` off) goes through ``remove_if_hash`` (HB-2).

An unedited bundled hook is moved into ``.trw/trash``; nothing is unlinked, so an open-fd write or an
edit racing the withdrawal keeps its bytes, and a symlink at the hook path is never followed.
"""

from __future__ import annotations

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

_HOOK = "pre-tool-distill-hint.sh"


def _installed(tmp_path: Path) -> tuple[Path, Path, bytes]:
    from trw_mcp.bootstrap._claude_code_distill_channels import _CC03_HOOKS, _get_hook_content

    assert _HOOK in _CC03_HOOKS
    content = _get_hook_content(_HOOK)
    assert content is not None
    root = tmp_path / "proj"
    hook = root / ".claude" / "hooks" / _HOOK
    hook.parent.mkdir(parents=True)
    hook.write_bytes(content.encode("utf-8"))
    return root, hook, content.encode("utf-8")


def _result() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "preserved": [], "errors": []}


def _trash(root: Path) -> list[bytes]:
    trash = root / ".trw" / "trash"
    return [p.read_bytes() for p in trash.glob("*/data")] if trash.is_dir() else []


def test_unedited_hook_moves_to_trash_and_is_reported(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, body = _installed(tmp_path)
    result = _result()
    _withdraw_hook(root, _HOOK, result)
    assert not hook.exists()
    assert _trash(root) == [body]
    assert result["removed"] == [f".claude/hooks/{_HOOK}"]
    assert result["trashed"] == [f".claude/hooks/{_HOOK}"]


def test_open_fd_write_after_withdrawal_keeps_its_bytes(tmp_path: Path) -> None:
    """Red on the old code: ``dest.unlink()`` detached the inode, so the late write was lost."""
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, _body = _installed(tmp_path)
    with open_fd_writer(hook) as writer:
        _withdraw_hook(root, _HOOK, _result())
        writer.write(b"#!/bin/sh\necho my late edit\n")
    assert _trash(root) == [b"#!/bin/sh\necho my late edit\n"]


def test_edit_between_check_and_act_is_put_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Red on the old code: it compared the text, then unlinked whatever was at the name."""
    from trw_mcp.bootstrap import _claude_code_distill_channels as channels

    root, hook, _body = _installed(tmp_path)
    real = channels.remove_if_hash

    def edit_then_remove(path: Path, root_: Path, expected: str, **kw: object):  # type: ignore[no-untyped-def]
        path.write_bytes(b"#!/bin/sh\necho edited at the act\n")
        return real(path, root_, expected, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(channels, "remove_if_hash", edit_then_remove)
    result = _result()
    channels._withdraw_hook(root, _HOOK, result)
    assert hook.read_bytes() == b"#!/bin/sh\necho edited at the act\n"
    assert "removed" not in result
    assert result["preserved"] == [f".claude/hooks/{_HOOK}"]


def test_symlinked_hook_is_refused_and_its_target_untouched(tmp_path: Path) -> None:
    """Red on the old code: is_file/read_text followed the link, and unlink removed it."""
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, body = _installed(tmp_path)
    outside = tmp_path / "outside.sh"
    outside.write_bytes(body)  # the bundled bytes: a content match must not reach it
    hook.unlink()
    hook.symlink_to(outside)
    result = _result()
    _withdraw_hook(root, _HOOK, result)
    assert hook.is_symlink()
    assert outside.read_bytes() == body
    assert "removed" not in result
    assert any(_HOOK in w for w in result["warnings"])


@pytest.mark.parametrize("kind", ["symlink", "dir", "replace"])
def test_swap_after_the_check_never_loses_user_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from trw_mcp.bootstrap import _claude_code_distill_channels as channels

    root, hook, body = _installed(tmp_path)
    outside = tmp_path / "outside.sh"
    outside.write_bytes(body)
    real = channels.remove_if_hash
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

    monkeypatch.setattr(channels, "remove_if_hash", swap_then_remove)
    result = _result()
    channels._withdraw_hook(root, _HOOK, result)
    assert "removed" not in result
    assert outside.read_bytes() == body
    assert_user_bytes_preserved(before[0], tmp_path)
    assert os.path.lexists(hook)


def test_edited_hook_is_preserved_without_a_trash_entry(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, _body = _installed(tmp_path)
    hook.write_bytes(b"#!/bin/sh\necho mine\n")
    result = _result()
    _withdraw_hook(root, _HOOK, result)
    assert hook.read_bytes() == b"#!/bin/sh\necho mine\n"
    assert result["preserved"] == [f".claude/hooks/{_HOOK}"]
    assert _trash(root) == []


def test_unreadable_hook_is_left_with_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, _body = _installed(tmp_path)
    real = Path.read_bytes

    def denied(self: Path) -> bytes:
        if self == hook:
            raise PermissionError(13, "denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    result = _result()
    _withdraw_hook(root, _HOOK, result)
    assert hook.exists()
    assert result["warnings"] == [f".claude/hooks/{_HOOK}: left untouched (could not read it: [Errno 13] denied)"]


def _gated_off_project(tmp_path: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    config = tmp_path / ".trw" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "\ncc03_hook_enabled: false\n", encoding="utf-8")
    return tmp_path


@pytest.mark.usefixtures("no_memory_daemon")
def test_update_project_reports_the_trashed_hook(tmp_path: Path) -> None:
    """Red before the merge fix: update_project dropped the channel's ``trashed`` key."""
    from trw_mcp.bootstrap import update_project
    from trw_mcp.bootstrap._claude_code_distill_channels import _get_hook_content

    root = _gated_off_project(tmp_path)
    hook = root / ".claude" / "hooks" / _HOOK
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(_get_hook_content(_HOOK) or "", encoding="utf-8")
    result = update_project(root, ide="claude-code")
    assert f".claude/hooks/{_HOOK}" in result.get("trashed", [])
    assert not hook.exists()


@pytest.mark.usefixtures("no_memory_daemon")
def test_update_project_reports_a_hook_left_in_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Red before the merge fix: update_project dropped the channel's ``warnings`` key."""
    from trw_mcp.bootstrap import _claude_code_distill_channels as channels
    from trw_mcp.bootstrap import update_project
    from trw_mcp.bootstrap._safe_remove import Removal

    root = _gated_off_project(tmp_path)
    hook = root / ".claude" / "hooks" / _HOOK
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(channels._get_hook_content(_HOOK) or "", encoding="utf-8")
    copy = root / ".trw" / "trash" / "x" / "data"
    monkeypatch.setattr(
        channels,
        "remove_if_hash",
        lambda p, r, e, **k: Removal(k.get("key"), p, "retained", None, copy, "could not put the file back"),
    )
    result = update_project(root, ide="claude-code")
    expected = f".claude/hooks/{_HOOK}: moved to .trw/trash and not put back (could not put the file back); "
    assert f"{expected}your copy is in {copy}" in result.get("warnings", [])


@pytest.mark.usefixtures("no_memory_daemon")
def test_withdrawal_sticks_in_a_real_git_repo_across_updates(tmp_path: Path) -> None:
    """Red on ac908be0: the uncommitted-changes guard restored the untracked hooks after every withdrawal."""
    import subprocess

    from trw_mcp.bootstrap import init_project, update_project

    root = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        check=True,
    )
    assert not init_project(root, ide="claude-code")["errors"]
    config = root / ".trw" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "\ncc03_hook_enabled: true\n", encoding="utf-8")
    update_project(root, ide="claude-code")
    hooks = root / ".claude" / "hooks"
    assert (hooks / _HOOK).is_file()
    config.write_text(
        config.read_text(encoding="utf-8").replace("cc03_hook_enabled: true", "cc03_hook_enabled: false"),
        encoding="utf-8",
    )
    counts = []
    for _ in range(2):
        update_project(root, ide="claude-code")
        counts.append(len(_trash(root)))
        assert not (hooks / _HOOK).exists()
        assert not (hooks / "lib-distill-hint.sh").exists()
    assert counts == [2, 2]
