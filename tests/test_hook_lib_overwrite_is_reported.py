"""An edited shared hook lib is never replaced without a report and a way back (HOOK-LIB-OVERWRITE-SILENT).

``lib-trw.sh`` and ``lib-intent-guard.sh`` are TRW-owned: the 15 and 2 hooks that source them move with them, so
``update-project`` replaces an edited copy (FB-INSTALL-01). The replacement must be loud: a warning naming the
file, and either a kept copy (uncommitted or hidden from git) or the git command that restores it (committed).
Real git repositories, because the report depends on what git knows about the file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project

pytestmark = pytest.mark.integration

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}
_EDIT = "# MY LOCAL EDIT"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, env=_ENV, check=True, capture_output=True)


@pytest.mark.parametrize("mode", ["uncommitted", "committed", "skip-worktree"])
@pytest.mark.parametrize("lib", ["lib-trw.sh", "lib-intent-guard.sh"])
def test_a_replaced_edited_lib_is_named_in_the_warnings_with_a_way_back(tmp_path: Path, mode: str, lib: str) -> None:
    _git(tmp_path, "init", "-q")
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    rel = f".claude/hooks/{lib}"
    path = tmp_path / rel
    path.write_text(path.read_text() + f"\n{_EDIT}\n", encoding="utf-8")
    if mode == "committed":
        _git(tmp_path, "commit", "-qam", "my edit")
    elif mode == "skip-worktree":
        _git(tmp_path, "update-index", "--skip-worktree", rel)

    result = update_project(tmp_path)

    assert not result["errors"], result["errors"]
    assert _EDIT not in path.read_text(encoding="utf-8"), "the hooks and their lib move together"
    named = [w for w in result["warnings"] if rel in w]
    assert named, f"{rel} was replaced and the update said nothing about it"
    if mode == "committed":
        assert any(f"git restore -- {rel}" in w for w in named), named
    else:
        kept = [p for p in (tmp_path / ".trw").rglob("*") if p.is_file() and _EDIT in p.read_text(errors="ignore")]
        assert kept, "no copy of the edited lib survives under .trw"
        assert any(str(tmp_path / ".trw") in w for w in named), named
