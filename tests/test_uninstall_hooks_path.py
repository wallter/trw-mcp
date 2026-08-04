"""Uninstall must follow ``core.hooksPath``, because install does.

``bootstrap/_git_hooks.py`` resolves ``core.hooksPath`` before writing the TRW
post-commit dispatch block, so a project that redirects its hooks — common in
monorepos and in teams that share a checked-in ``.githooks/`` — gets the block at
a path no project-relative uninstall manifest can name. The catalog entry for
``.git/hooks/post-commit`` documented that gap in a comment rather than closing
it, so ``trw-mcp uninstall`` reported success and left an ACTIVE hook that runs on
every commit.

Found by an independent adversarial review of the commit that claimed the
install/uninstall parity test holds for all install behaviour. It holds for the
default hooks directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def repo_with_custom_hooks_path(tmp_path: Path) -> Path:
    """A git repo whose hooks live in ``.githooks``, not ``.git/hooks``."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.hooksPath", ".githooks"], check=True)
    return tmp_path


def test_install_writes_the_hook_where_git_actually_looks(repo_with_custom_hooks_path: Path) -> None:
    """Non-vacuity control. If install stopped honouring the setting, the uninstall
    test below would pass for the wrong reason — there would be nothing to leave behind."""
    from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook

    install_git_post_commit_hook(repo_with_custom_hooks_path)

    assert (repo_with_custom_hooks_path / ".githooks" / "post-commit").is_file(), (
        "install no longer honours core.hooksPath; this test's premise is gone"
    )


def test_uninstall_strips_the_trw_block_from_the_configured_hooks_dir(
    repo_with_custom_hooks_path: Path,
) -> None:
    """The defect: RED before uninstall resolved core.hooksPath.

    Asserts on the TRW marker rather than on file absence, because the hook is
    chained — a user's own post-commit content must survive.
    """
    from trw_mcp.bootstrap._git_hooks import MARKER_START, install_git_post_commit_hook
    from trw_mcp.server._subcommands_lifecycle import _resolve_hooks_dir

    hook = repo_with_custom_hooks_path / ".githooks" / "post-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'a hook the user wrote'\n", encoding="utf-8")
    install_git_post_commit_hook(repo_with_custom_hooks_path)
    assert MARKER_START in hook.read_text(encoding="utf-8"), "setup failed: no TRW block to remove"

    resolved = _resolve_hooks_dir(repo_with_custom_hooks_path, repo_with_custom_hooks_path / ".git", probe=True)
    assert resolved.resolve() == (repo_with_custom_hooks_path / ".githooks").resolve(), (
        "uninstall's resolver disagrees with install's about where the hook lives"
    )

    from trw_mcp.server._subcommands_uninstall_config import _remove_managed_block_file

    _remove_managed_block_file(resolved / "post-commit", dry_run=False)

    remaining = hook.read_text(encoding="utf-8")
    assert MARKER_START not in remaining, "TRW's post-commit block survived uninstall"
    assert "a hook the user wrote" in remaining, "uninstall destroyed the user's own hook content"
