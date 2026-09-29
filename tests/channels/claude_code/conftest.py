"""Leak guard for the CC-03/CC-04 distill-hint hook tests (learning L-CUAX).

``pre-tool-distill-hint.sh`` spawns a Python subprocess whose
``compute_before_edit_hint`` resolves its project root via
``resolve_repo_root``/``resolve_project_root`` fallbacks that fall back to
``git rev-parse --show-toplevel`` / ``Path.cwd()`` when no explicit
``repo_root`` is passed -- both keyed on the SUBPROCESS's own cwd. A test that
does not pin that cwd to its own throwaway project (see
``_distill_hint_support.run_distill_hint_hook``) has that subprocess resolve
the ENCLOSING checkout instead, which can read or write this checkout's own
``.trw/context/cc03-hints`` (CC-04 correlation records) or
``.trw/distill/map-cache`` (the trw-distill sidecar cache) rather than staying
inside ``tmp_path``.

This fixture snapshots (names + mtimes only, read-only) both directories
rooted at the enclosing checkout before and after every test in this
directory and fails loudly if either changed -- whether or not the test
itself believed its hook invocation was isolated.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

#: Resolved once per session: the checkout actually running this test suite,
#: never the tmp_path a test spawns a hook subprocess against.
_ENCLOSING_ROOT: Path | None = None

_GUARDED_RELATIVE_DIRS: tuple[Path, ...] = (
    Path(".trw") / "context" / "cc03-hints",
    Path(".trw") / "distill" / "map-cache",
)


def _enclosing_repo_root() -> Path | None:
    global _ENCLOSING_ROOT
    if _ENCLOSING_ROOT is not None:
        return _ENCLOSING_ROOT
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # trw-fail-silent-allow: no git, nothing to guard
        return None
    if completed.returncode != 0:
        return None
    stripped = completed.stdout.strip()
    if not stripped:
        return None
    _ENCLOSING_ROOT = Path(stripped)
    return _ENCLOSING_ROOT


def _snapshot(root: Path) -> dict[str, tuple[str, float]]:
    """``{relative_path: (guarded_dir, mtime)}`` for every entry under each guarded dir."""
    listing: dict[str, tuple[str, float]] = {}
    for rel in _GUARDED_RELATIVE_DIRS:
        directory = root / rel
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            listing[str(entry.relative_to(root))] = (str(rel), entry.stat().st_mtime)
    return listing


@pytest.fixture(autouse=True)
def _no_leak_into_enclosing_checkout() -> Iterator[None]:
    root = _enclosing_repo_root()
    if root is None:
        # Not running inside a git checkout at all (e.g. an extracted sdist) —
        # nothing to protect, and no enclosing repo to walk up to either.
        yield
        return
    before = _snapshot(root)
    yield
    after = _snapshot(root)
    added = {k: v for k, v in after.items() if k not in before}
    changed = {k: v for k, v in after.items() if k in before and before[k] != v}
    removed = {k: v for k, v in before.items() if k not in after}
    assert not added and not changed and not removed, (
        "a CC-03/CC-04 hook test wrote into the ENCLOSING checkout's "
        f"{[str(p) for p in _GUARDED_RELATIVE_DIRS]} at {root} instead of staying "
        f"inside tmp_path (learning L-CUAX). added={added} changed={changed} removed={removed}. "
        "Route the hook subprocess through "
        "tests.channels.claude_code._distill_hint_support.run_distill_hint_hook, "
        "which pins cwd/HOME to the test's own tmp_path project."
    )
