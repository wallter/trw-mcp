"""Integration test for ``.trw`` tree permission hardening on the install path.

PRD-QUAL-110-FR02 follow-up: ``init_project`` must leave the ``.trw`` root and
its state subdirectories at mode 0700, matching the README security claim that
"``.trw/`` dirs are 0700". Previously only ``.trw/memory`` was hardened (by the
memory-backend path), so a fresh install left ``learnings/``, ``logs/``,
``context/`` etc. group/other-readable.

POSIX-only: chmod mode bits are meaningless on Windows, so the assertions skip
on non-POSIX platforms (the hardening degrades to a logged WARNING there).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from trw_mcp.bootstrap._init_project import init_project

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX file-mode bits are meaningless on this platform"
)


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _mode(path: Path) -> int:
    """Return the permission bits (low 12) of *path*."""
    return stat.S_IMODE(path.stat().st_mode)


# Root + state subdirs that MUST be owner-only after init (the README claim).
_EXPECTED_0700_DIRS: tuple[str, ...] = (
    ".trw",
    ".trw/runs",
    ".trw/learnings",
    ".trw/learnings/entries",
    ".trw/logs",
    ".trw/context",
    ".trw/runtime",
    ".trw/reflections",
    ".trw/knowledge",
    ".trw/security",
    ".trw/memory",
    ".trw/frameworks",
    ".trw/templates",
    ".trw/scripts",
)


@pytest.mark.integration
class TestInitProjectPermissions:
    """init_project hardens the .trw tree to 0700 on the install path."""

    def test_trw_tree_is_0700_after_init(self, fake_git_repo: Path) -> None:
        result = init_project(fake_git_repo)
        assert not result["errors"], result["errors"]

        offenders: list[str] = []
        for rel in _EXPECTED_0700_DIRS:
            d = fake_git_repo / rel
            assert d.is_dir(), f"expected dir not created: {rel}"
            if _mode(d) != 0o700:
                offenders.append(f"{rel}={oct(_mode(d))}")
        assert not offenders, (
            "the following .trw dirs are not 0700 after init "
            f"(contradicts README security claim): {offenders}"
        )

    def test_trw_root_not_group_or_other_readable(self, fake_git_repo: Path) -> None:
        """The blast-radius assertion: learnings/logs/context not world/group readable."""
        init_project(fake_git_repo)
        for rel in (".trw", ".trw/learnings", ".trw/logs", ".trw/context"):
            mode = _mode(fake_git_repo / rel)
            group_other = mode & (stat.S_IRWXG | stat.S_IRWXO)
            assert group_other == 0, f"{rel} grants group/other access: {oct(mode)}"

    def test_no_trw_subdir_left_group_or_other_readable(self, fake_git_repo: Path) -> None:
        """EVERY directory under .trw is 0700 — including lazily-created ones.

        Sub-installers (e.g. distill channels, telemetry) create dirs AFTER the
        well-known scaffold set, so a fixed-list harden would miss them. The
        README claim "`.trw/` dirs are 0700" allows no exceptions, so the
        hardening walks the whole tree.
        """
        init_project(fake_git_repo)
        trw = fake_git_repo / ".trw"
        offenders = [
            f"{d.relative_to(fake_git_repo)}={oct(_mode(d))}"
            for d in trw.rglob("*")
            if d.is_dir() and not d.is_symlink() and _mode(d) != 0o700
        ]
        assert not offenders, f"non-0700 dirs under .trw: {offenders}"
