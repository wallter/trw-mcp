"""Retired-hook withdrawal goes through ``remove_if_hash``: user bytes are never unlinked.

A retired hook whose bytes match the manifest is moved into ``.trw/trash``, not deleted, so a
writer holding an open fd (or an edit that lands between the check and the act) keeps its bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests._fs_hazards import open_fd_writer, snapshot_user_bytes

_BODY = b"#!/bin/sh\necho retired\n"


def _project(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    project = tmp_path / "proj"
    hook = project / ".claude" / "hooks" / "retired.sh"
    hook.parent.mkdir(parents=True)
    hook.write_bytes(_BODY)
    return project, hook, {"retired.sh": hashlib.sha256(_BODY).hexdigest()}


def _in_trash(project: Path) -> list[bytes]:
    trash = project / ".trw" / "trash"
    return [p.read_bytes() for p in trash.glob("*/data")] if trash.is_dir() else []


def test_unedited_retired_hook_moves_to_trash_and_is_reported_removed(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._template_updater import _withdraw_retired_hooks

    project, hook, manifest = _project(tmp_path)
    result: dict[str, list[str]] = {}
    _withdraw_retired_hooks(project, set(), manifest, result)
    assert not hook.exists()
    assert result["removed"] == [".claude/hooks/retired.sh"]
    assert _in_trash(project) == [_BODY]


def test_open_fd_write_after_withdrawal_keeps_its_bytes(tmp_path: Path) -> None:
    """HB-2: a name-based unlink detaches the inode, so a later write through a held fd is lost."""
    from trw_mcp.bootstrap._template_updater import _withdraw_retired_hooks

    project, hook, manifest = _project(tmp_path)
    with open_fd_writer(hook) as writer:
        _withdraw_retired_hooks(project, set(), manifest, {})
        writer.write(b"#!/bin/sh\necho my late edit\n")
    assert _in_trash(project) == [b"#!/bin/sh\necho my late edit\n"]


def test_edit_between_check_and_act_is_put_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _template_updater

    project, hook, manifest = _project(tmp_path)
    real = _template_updater.remove_if_hash

    def edit_then_remove(path: Path, root: Path, expected: str, **kw: object):  # type: ignore[no-untyped-def]
        path.write_bytes(b"#!/bin/sh\necho edited at the act\n")
        return real(path, root, expected, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(_template_updater, "remove_if_hash", edit_then_remove)
    result: dict[str, list[str]] = {}
    _template_updater._withdraw_retired_hooks(project, set(), manifest, result)
    assert hook.read_bytes() == b"#!/bin/sh\necho edited at the act\n"
    assert "removed" not in result
    assert any("retired.sh" in w and "kept" in w for w in result["warnings"])


def test_edited_retired_hook_is_kept_without_a_trash_entry(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._template_updater import _withdraw_retired_hooks

    project, hook, manifest = _project(tmp_path)
    hook.write_bytes(b"#!/bin/sh\necho mine\n")
    before = snapshot_user_bytes(project)
    result: dict[str, list[str]] = {}
    _withdraw_retired_hooks(project, set(), manifest, result)
    assert hook.read_bytes() == b"#!/bin/sh\necho mine\n"
    assert snapshot_user_bytes(project) == before  # no capture for an edit seen at the pre-check
    assert any("edited" in w for w in result["warnings"])


@pytest.mark.parametrize("kind", ["symlink", "dir", "replace"])
def test_swap_after_the_pre_check_never_loses_user_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The name changes between the site's hash check and the act: nothing outside or racing is lost."""
    import os

    from tests._fs_hazards import assert_user_bytes_preserved, atomic_replace, swap_to_dir, swap_to_symlink
    from trw_mcp.bootstrap import _template_updater

    project, hook, manifest = _project(tmp_path)
    outside = tmp_path / "outside.sh"
    outside.write_bytes(_BODY)  # same bytes as the recorded hook: a hash match must not reach it
    real = _template_updater.remove_if_hash

    def swap_then_remove(path: Path, root: Path, expected: str, **kw: object):  # type: ignore[no-untyped-def]
        if kind == "symlink":
            swap_to_symlink(path, outside)
        elif kind == "dir":
            swap_to_dir(path)
            (path / "user.txt").write_bytes(b"user dir bytes")
        else:
            atomic_replace(path, b"racer bytes")
        before[0] = snapshot_user_bytes(tmp_path)
        return real(path, root, expected, **kw)  # type: ignore[arg-type]

    before: list[dict[str, list[str]]] = [{}]
    monkeypatch.setattr(_template_updater, "remove_if_hash", swap_then_remove)
    result: dict[str, list[str]] = {}
    _template_updater._withdraw_retired_hooks(project, set(), manifest, result)
    assert "removed" not in result
    assert outside.read_bytes() == _BODY
    assert_user_bytes_preserved(before[0], tmp_path)
    if kind == "replace":
        assert hook.read_bytes() == b"racer bytes"
    else:
        assert os.path.lexists(hook)


def test_failed_read_of_the_capture_puts_the_hook_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from trw_mcp.bootstrap import _safe_remove, _template_updater

    project, hook, manifest = _project(tmp_path)
    real_open = _safe_remove.os.open

    def failing_open(path: object, flags: int, *a: object, **kw: object) -> int:
        if path == "data" and not flags & (os.O_WRONLY | os.O_RDWR):
            raise PermissionError(13, "injected read failure")
        return real_open(path, flags, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(_safe_remove.os, "open", failing_open)
    result: dict[str, list[str]] = {}
    _template_updater._withdraw_retired_hooks(project, set(), manifest, result)
    assert hook.read_bytes() == _BODY
    assert "removed" not in result
    assert any("kept" in w for w in result["warnings"])


@pytest.mark.parametrize("retained_at", [None, "trash/x/data"], ids=["unknown", "known"])
def test_retained_message_names_where_the_bytes_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retained_at: str | None
) -> None:
    from trw_mcp.bootstrap import _template_updater
    from trw_mcp.bootstrap._safe_remove import Removal

    project, hook, manifest = _project(tmp_path)
    at = None if retained_at is None else tmp_path / retained_at

    def retained(path: Path, root: Path, expected: str, **kw: object) -> Removal:
        return Removal("k", path, "retained", None, at, "could not put the file back")

    monkeypatch.setattr(_template_updater, "remove_if_hash", retained)
    result: dict[str, list[str]] = {}
    _template_updater._withdraw_retired_hooks(project, set(), manifest, result)
    (warning,) = result["warnings"]
    assert "None" not in warning
    assert (str(at) if at else ".trw/trash (exact folder unknown)") in warning
    assert "nothing was overwritten" in warning


def test_unreadable_retired_hook_is_kept_and_the_update_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap._template_updater import _withdraw_retired_hooks

    project, hook, manifest = _project(tmp_path)
    real = Path.read_bytes

    def denied(self: Path) -> bytes:
        if self == hook:
            raise PermissionError(13, "denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    result: dict[str, list[str]] = {}
    _withdraw_retired_hooks(project, set(), manifest, result)
    assert hook.exists()
    assert result["warnings"] == [".claude/hooks/retired.sh: left untouched (could not read it: [Errno 13] denied)"]
