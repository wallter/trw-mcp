"""Retired-hook withdrawal deletes in place: TRW's bytes go, uncommitted user bytes are never touched.

A retired hook whose bytes match the manifest is deleted (no ``.trw/trash`` capture); one git holds clean is
deleted and named with its restore command; an edited one is kept and named with the command that removes it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

_BODY = b"#!/bin/sh\necho retired\n"
_REL = ".claude/hooks/retired.sh"


def _project(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    project = tmp_path / "proj"
    hook = project / ".claude" / "hooks" / "retired.sh"
    hook.parent.mkdir(parents=True)
    hook.write_bytes(_BODY)
    return project, hook, {"retired.sh": hashlib.sha256(_BODY).hexdigest()}


def _withdraw(project: Path, manifest: dict[str, str]) -> dict[str, list[str]]:
    from trw_mcp.bootstrap._template_updater import _withdraw_retired_hooks

    result: dict[str, list[str]] = {}
    _withdraw_retired_hooks(project, set(), manifest, result)
    return result


def test_unedited_retired_hook_is_deleted_in_place_and_reported_removed(tmp_path: Path) -> None:
    project, hook, manifest = _project(tmp_path)
    result = _withdraw(project, manifest)
    assert not hook.exists()
    assert result["removed"] == [_REL]
    assert result["retired"] == [_REL]
    assert not (project / ".trw" / "trash").exists()


def test_a_git_clean_edited_retired_hook_is_deleted_and_names_the_restore(tmp_path: Path) -> None:
    project, hook, manifest = _project(tmp_path)
    hook.write_bytes(b"#!/bin/sh\necho committed edit\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
    result = _withdraw(project, manifest)
    assert not hook.exists()
    assert (
        f"{_REL}: removed; your version differs from TRW's but is committed in git (restore: git restore -- {_REL})"
        in result["warnings"]
    )
    assert not (project / ".trw" / "trash").exists()


def test_an_uncommitted_edit_is_kept_untouched_and_named_with_the_removal_command(tmp_path: Path) -> None:
    project, hook, manifest = _project(tmp_path)
    hook.write_bytes(b"#!/bin/sh\necho mine\n")
    result = _withdraw(project, manifest)
    assert hook.read_bytes() == b"#!/bin/sh\necho mine\n"
    assert "removed" not in result
    assert any(
        w.startswith(f"{_REL}: no longer shipped by TRW; kept because it was edited") for w in result["warnings"]
    )
    assert any(w.endswith(f"rm {_REL}") for w in result["warnings"])
    assert not (project / ".trw" / "trash").exists()


def test_a_symlinked_retired_hook_is_left_alone_and_its_target_untouched(tmp_path: Path) -> None:
    project, hook, manifest = _project(tmp_path)
    outside = tmp_path / "outside.sh"
    outside.write_bytes(_BODY)  # same bytes as the recorded hook: a hash match must not reach it
    hook.unlink()
    hook.symlink_to(outside)
    result = _withdraw(project, manifest)
    assert hook.is_symlink()
    assert outside.read_bytes() == _BODY
    assert "removed" not in result
    assert any(_REL in w and "left untouched" in w for w in result["warnings"])


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_unreadable_retired_hook_is_kept_and_the_update_continues(tmp_path: Path) -> None:
    project, hook, manifest = _project(tmp_path)
    hook.chmod(0)
    try:
        result = _withdraw(project, manifest)
    finally:
        hook.chmod(0o644)
    assert hook.exists()
    assert any(_REL in w and "left untouched (unreadable" in w for w in result["warnings"])
