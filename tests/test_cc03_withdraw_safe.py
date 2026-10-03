"""CC-03 hook withdrawal (``cc03_hook_enabled`` off) deletes in place, never into ``.trw/trash`` (HB-2).

An unedited bundled hook is deleted; one git holds clean is deleted and named with its restore command; an
edited one is kept and named with the command that removes it. A symlink at the hook path is never followed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

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


def _no_trash(root: Path) -> bool:
    return not (root / ".trw" / "trash").exists()


def test_unedited_hook_is_deleted_in_place_and_reported(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, _body = _installed(tmp_path)
    result = _result()
    _withdraw_hook(root, _HOOK, result)
    assert not hook.exists()
    assert _no_trash(root)
    assert result["removed"] == [f".claude/hooks/{_HOOK}"]
    assert result["retired"] == [f".claude/hooks/{_HOOK}"]


def test_a_git_clean_edited_hook_is_deleted_and_names_the_restore(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, _body = _installed(tmp_path)
    hook.write_bytes(b"#!/bin/sh\necho committed edit\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    result = _result()
    _withdraw_hook(root, _HOOK, result)
    assert not hook.exists()
    assert _no_trash(root)
    rel = f".claude/hooks/{_HOOK}"
    assert (
        f"{rel}: removed; your version differs from TRW's but is committed in git (restore: git restore -- {rel})"
        in result["warnings"]
    )


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


def test_edited_hook_is_kept_and_named_with_the_removal_command(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, _body = _installed(tmp_path)
    hook.write_bytes(b"#!/bin/sh\necho mine\n")
    result = _result()
    _withdraw_hook(root, _HOOK, result)
    assert hook.read_bytes() == b"#!/bin/sh\necho mine\n"
    assert result["preserved"] == [f".claude/hooks/{_HOOK}"]
    assert any(w.endswith(f"rm .claude/hooks/{_HOOK}") for w in result["warnings"])
    assert _no_trash(root)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_unreadable_hook_is_kept_with_a_warning(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _withdraw_hook

    root, hook, _body = _installed(tmp_path)
    hook.chmod(0)
    try:
        result = _result()
        _withdraw_hook(root, _HOOK, result)
    finally:
        hook.chmod(0o644)
    assert hook.exists()
    assert any("unreadable" in w and w.endswith(f"rm .claude/hooks/{_HOOK}") for w in result["warnings"])


def _gated_off_project(tmp_path: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    config = tmp_path / ".trw" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "\ncc03_hook_enabled: false\n", encoding="utf-8")
    return tmp_path


@pytest.mark.usefixtures("no_memory_daemon")
def test_update_project_reports_the_retired_hook(tmp_path: Path) -> None:
    """Red before the merge fix: update_project dropped the channel's ``retired`` key."""
    from trw_mcp.bootstrap import update_project
    from trw_mcp.bootstrap._claude_code_distill_channels import _get_hook_content

    root = _gated_off_project(tmp_path)
    hook = root / ".claude" / "hooks" / _HOOK
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(_get_hook_content(_HOOK) or "", encoding="utf-8")
    result = update_project(root, ide="claude-code")
    assert f".claude/hooks/{_HOOK}" in result.get("retired", [])
    assert not hook.exists()


@pytest.mark.usefixtures("no_memory_daemon")
def test_withdrawal_sticks_in_a_real_git_repo_across_updates(tmp_path: Path) -> None:
    """Red on ac908be0: the uncommitted-changes guard restored the untracked hooks after every withdrawal."""
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
    for _ in range(2):
        update_project(root, ide="claude-code")
        assert _no_trash(root)
        assert not (hooks / _HOOK).exists()
        assert not (hooks / "lib-distill-hint.sh").exists()
