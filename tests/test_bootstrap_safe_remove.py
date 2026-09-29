"""Direct tests for ``bootstrap/_safe_remove.py``, the delete primitive every uninstall path uses.

Every refusal case ends by proving the bytes outside the root still exist
(tests/AGENTS.md "Changes that delete ... user files", item 9), not only that a
reason string came back.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _safe_remove
from trw_mcp.bootstrap._safe_remove import path_refusal, safe_remove


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path]:
    """A project root and a sibling ``outside`` dir holding user bytes."""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "user.txt").write_text("user bytes", encoding="utf-8")
    return root, outside


def _outside_intact(outside: Path) -> None:
    assert (outside / "user.txt").read_text(encoding="utf-8") == "user bytes"


def test_plain_file_and_dir_under_root_are_removed(layout: tuple[Path, Path]) -> None:
    root, outside = layout
    f = root / ".trw" / "a.txt"
    f.parent.mkdir()
    f.write_text("trw", encoding="utf-8")
    assert path_refusal(f, root) is None
    assert safe_remove(f, root) is None
    assert not f.exists()
    assert safe_remove(root / ".trw", root) is None
    assert not (root / ".trw").exists()
    _outside_intact(outside)


@pytest.mark.parametrize("target_kind", ["dir", "file"])
def test_symlinked_path_is_refused_and_its_target_survives(layout: tuple[Path, Path], target_kind: str) -> None:
    root, outside = layout
    link = root / ".trw"
    link.symlink_to(outside if target_kind == "dir" else outside / "user.txt")
    assert safe_remove(link, root) == "refused: path is a symlink"
    assert link.is_symlink()
    _outside_intact(outside)


def test_symlinked_parent_component_is_refused(layout: tuple[Path, Path]) -> None:
    root, outside = layout
    (root / ".claude").symlink_to(outside, target_is_directory=True)
    reason = safe_remove(root / ".claude" / "user.txt", root)
    assert reason is not None
    assert reason.startswith("refused: parent") and "is a symlink" in reason
    _outside_intact(outside)


@pytest.mark.parametrize(
    ("make_path", "expected"),
    [
        (lambda root, outside: outside / "user.txt", "path is not under root"),
        (lambda root, outside: root / ".." / "outside" / "user.txt", "refused: path resolves outside root"),
    ],
    ids=["outside_root", "dotdot_escape"],
)
def test_paths_that_leave_the_root_are_refused(
    layout: tuple[Path, Path], make_path: Callable[[Path, Path], Path], expected: str
) -> None:
    root, outside = layout
    assert safe_remove(make_path(root, outside), root) == expected
    _outside_intact(outside)


def test_symlink_nested_inside_a_removed_dir_is_unlinked_not_followed(layout: tuple[Path, Path]) -> None:
    root, outside = layout
    managed = root / ".trw"
    managed.mkdir()
    (managed / "own.txt").write_text("trw", encoding="utf-8")
    (managed / "escape").symlink_to(outside, target_is_directory=True)
    assert safe_remove(managed, root) is None
    assert not managed.exists()
    _outside_intact(outside)


def test_dir_swapped_for_a_symlink_after_the_check_is_not_followed(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check-then-act race: the path becomes a symlink to user data between refusal check and rmtree."""
    root, outside = layout
    target = root / ".trw"
    target.mkdir()
    real_refusal = _safe_remove.path_refusal
    swapped: list[bool] = []

    def check_then_swap(path: Path, check_root: Path) -> str | None:
        verdict = real_refusal(path, check_root)
        target.rmdir()
        target.symlink_to(outside, target_is_directory=True)
        swapped.append(True)
        return verdict

    monkeypatch.setattr(_safe_remove, "path_refusal", check_then_swap)
    reason = safe_remove(target, root)
    assert swapped == [True]
    assert reason == "refused: path is a symlink"
    assert target.is_symlink()
    _outside_intact(outside)


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions; root bypasses them")
def test_unreadable_parent_reports_an_error_and_keeps_the_file(layout: tuple[Path, Path]) -> None:
    root, _outside = layout
    parent = root / ".trw"
    parent.mkdir()
    f = parent / "a.txt"
    f.write_text("trw", encoding="utf-8")
    parent.chmod(0)
    try:
        reason = safe_remove(f, root)
    finally:
        parent.chmod(0o700)
    assert reason is not None and reason.startswith("refused: could not inspect path (PermissionError)")
    assert f.read_text(encoding="utf-8") == "trw"


def test_unresolvable_root_is_refused(layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    root, outside = layout
    real_resolve = Path.resolve

    def failing_resolve(self: Path, strict: bool = False) -> Path:
        if self == root:
            raise PermissionError("denied")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", failing_resolve)
    reason = safe_remove(root / "a.txt", root)
    assert reason is not None and reason.startswith("root could not be resolved")
    _outside_intact(outside)


def _tree_bytes(top: Path) -> dict[str, bytes | str]:
    """Whole-tree snapshot: every path under *top* with its bytes (or symlink target)."""
    snap: dict[str, bytes | str] = {}
    for p in sorted(top.rglob("*")):
        rel = str(p.relative_to(top))
        snap[rel] = os.readlink(p) if p.is_symlink() else (b"" if p.is_dir() else p.read_bytes())
    return snap


@pytest.fixture
def populated(layout: tuple[Path, Path]) -> tuple[Path, Path, Path]:
    root, outside = layout
    (root / ".trw").mkdir()
    (root / ".trw" / "a.txt").write_text("trw", encoding="utf-8")
    (root / "user.md").write_text("user root bytes", encoding="utf-8")
    return root, outside, root.parent


@pytest.mark.parametrize("kind", ["root", "root_dotdot", "symlink_to_root", "ancestor", "ancestor_symlink"])
def test_root_or_ancestor_target_is_refused_and_whole_tree_intact(
    populated: tuple[Path, Path, Path], kind: str
) -> None:
    root, _outside, top = populated
    if kind == "root":
        target = root
    elif kind == "root_dotdot":
        target = root / ".trw" / ".."
    elif kind == "symlink_to_root":
        target = root / "link"
        target.symlink_to(root, target_is_directory=True)
    elif kind == "ancestor":
        target = top
    else:
        target = root / "up"
        target.symlink_to(top, target_is_directory=True)
    before = _tree_bytes(top)
    reason = safe_remove(target, root)
    assert reason is not None and reason.startswith(("refused", "path is not under root"))
    assert _tree_bytes(top) == before


def test_dir_swapped_for_a_symlink_to_root_after_the_check_is_not_followed(
    populated: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _outside, top = populated
    target = root / "victim"
    target.mkdir()
    real_refusal = _safe_remove.path_refusal
    swapped: list[bool] = []

    def check_then_swap(path: Path, check_root: Path) -> str | None:
        verdict = real_refusal(path, check_root)
        target.rmdir()
        target.symlink_to(root, target_is_directory=True)
        swapped.append(True)
        return verdict

    monkeypatch.setattr(_safe_remove, "path_refusal", check_then_swap)
    reason = safe_remove(target, root)
    assert swapped == [True]
    assert reason == "refused: path is a symlink"
    assert target.is_symlink()
    assert (root / ".trw" / "a.txt").read_text(encoding="utf-8") == "trw"
    assert (root / "user.md").read_text(encoding="utf-8") == "user root bytes"
    assert (top / "outside" / "user.txt").exists()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions; root bypasses them")
def test_unreadable_parent_never_raises_and_whole_tree_survives(populated: tuple[Path, Path, Path]) -> None:
    root, _outside, top = populated
    parent = root / ".trw"
    before = _tree_bytes(top)
    parent.chmod(0)
    try:
        reason = safe_remove(parent / "a.txt", root)
    finally:
        parent.chmod(0o700)
    assert reason is not None and "PermissionError" in reason
    assert _tree_bytes(top) == before


def test_target_below_a_regular_file_is_absent_and_the_file_is_untouched(populated: tuple[Path, Path, Path]) -> None:
    root, _outside, top = populated
    before = _tree_bytes(top)
    assert safe_remove(root / "user.md" / "child", root) is None
    assert _tree_bytes(top) == before


def test_target_created_between_check_and_lstat_absent_path_is_never_unlinked(
    populated: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Racer creates the file AFTER the act's lstat said absent: unlink must not run."""
    root, _outside, top = populated
    target = root / "late.txt"
    real_lstat_mode = _safe_remove._lstat_mode
    calls: list[bool] = []

    def lstat_then_create(path: Path) -> int | None:
        mode = real_lstat_mode(path)
        if path == target and mode is None:
            calls.append(True)
            if len(calls) == 2:  # 1st call is path_refusal's symlink check, 2nd is the act's lstat
                target.write_text("racer bytes", encoding="utf-8")
        return mode

    monkeypatch.setattr(_safe_remove, "_lstat_mode", lstat_then_create)
    assert safe_remove(target, root) is None
    assert len(calls) == 2
    assert target.read_text(encoding="utf-8") == "racer bytes"
    assert (top / "outside" / "user.txt").exists()


def test_symlink_loop_under_root_is_refused_not_raised(populated: tuple[Path, Path, Path]) -> None:
    root, _outside, top = populated
    (root / "a").symlink_to(root / "b")
    (root / "b").symlink_to(root / "a")
    before = _tree_bytes(top)
    reason = safe_remove(root / "a" / "child", root)
    assert reason is not None and reason.startswith("refused")
    assert _tree_bytes(top) == before


def test_dir_swapped_for_a_symlink_inside_rmtree_is_not_followed(
    populated: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside, top = populated
    target = root / "victim"
    target.mkdir()
    real_rmtree = shutil.rmtree
    swapped: list[bool] = []

    def swap_then_rmtree(path: Path, *args: object, **kwargs: object) -> None:
        target.rmdir()
        target.symlink_to(outside, target_is_directory=True)
        swapped.append(True)
        real_rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil, "rmtree", swap_then_rmtree)
    reason = safe_remove(target, root)
    assert swapped == [True]
    assert reason is not None and reason.startswith("error removing")
    assert target.is_symlink()
    _outside_intact(outside)
    assert (root / "user.md").exists() and (top / "outside" / "user.txt").exists()


def test_file_swapped_for_a_symlink_before_unlink_removes_only_the_link(
    populated: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside, _top = populated
    target = root / "victim.txt"
    target.write_text("trw", encoding="utf-8")
    real_unlink = Path.unlink
    swapped: list[bool] = []

    def swap_then_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == target:
            real_unlink(target)
            target.symlink_to(outside / "user.txt")
            swapped.append(True)
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", swap_then_unlink)
    assert safe_remove(target, root) is None
    assert swapped == [True]
    assert not target.is_symlink() and not target.exists()
    _outside_intact(outside)
