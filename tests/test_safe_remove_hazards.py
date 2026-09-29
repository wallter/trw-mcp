"""Adoption proof for ``tests/_fs_hazards.py``: race ``safe_remove``'s act-time re-lstat.

TODO(follow-up): apply the same kit to ``remove_if_hash`` / ``Removal`` once they land on trunk.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests._fs_hazards import (
    assert_user_bytes_preserved,
    atomic_replace,
    race_after,
    snapshot_user_bytes,
    swap_to_dir,
    swap_to_symlink,
    unreadable_parent,
)
from trw_mcp.bootstrap._safe_remove import safe_remove

pytestmark = pytest.mark.unit


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target.txt"
    target.write_bytes(b"recorded content")
    (root / "user.txt").write_bytes(b"unrelated user bytes")
    return root, target


def _race_second_lstat(mp: pytest.MonkeyPatch, target: Path, interloper):  # type: ignore[no-untyped-def]
    # path_refusal lstats the final component once; the realpath inside resolve() is the 2nd and the act-time re-lstat the 3rd (probe.fired guards drift).
    return race_after(mp, target=target, op="lstat", interloper=interloper, nth=3)


def test_baseline_removes_unraced_file(tree: tuple[Path, Path]) -> None:
    root, target = tree
    assert safe_remove(target, root, expect="file") is None
    assert not target.exists()


def test_swap_to_symlink_at_act_is_refused_and_bytes_survive(
    monkeypatch: pytest.MonkeyPatch, tree: tuple[Path, Path], tmp_path: Path
) -> None:
    root, target = tree
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_bytes(b"outside bytes")
    before = snapshot_user_bytes(root)
    probe = _race_second_lstat(monkeypatch, target, lambda: swap_to_symlink(target, outside))
    # The interloper runs after the act-time lstat returned "plain file": the stale answer lets the act proceed.
    result = safe_remove(target, root, expect="file")
    assert probe.fired
    assert (outside / "precious").read_bytes() == b"outside bytes"
    assert_user_bytes_preserved(before, root)
    assert result is None or result.startswith(("refused", "error"))


def test_swap_to_symlink_before_act_lstat_is_refused(
    monkeypatch: pytest.MonkeyPatch, tree: tuple[Path, Path], tmp_path: Path
) -> None:
    root, target = tree
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_bytes(b"outside bytes")
    before = snapshot_user_bytes(root)
    probe = race_after(
        monkeypatch,
        target=target,
        op="lstat",
        nth=3,
        when="before",
        interloper=lambda: swap_to_symlink(target, outside),
    )
    assert safe_remove(target, root, expect="file") == "refused: path is a symlink"
    assert probe.fired
    assert os.path.islink(target) and (outside / "precious").exists()
    assert_user_bytes_preserved(before, root)


def test_swap_file_to_dir_before_act_is_refused_without_recursive_delete(
    monkeypatch: pytest.MonkeyPatch, tree: tuple[Path, Path]
) -> None:
    root, target = tree
    before = snapshot_user_bytes(root)
    probe = race_after(
        monkeypatch, target=target, op="lstat", nth=3, when="before", interloper=lambda: swap_to_dir(target)
    )
    assert safe_remove(target, root, expect="file") == "refused: expected a file, found a directory"
    assert probe.fired and target.is_dir()
    assert_user_bytes_preserved(before, root)


def test_swapped_in_dir_with_user_content_survives_refusal(
    monkeypatch: pytest.MonkeyPatch, tree: tuple[Path, Path]
) -> None:
    root, target = tree

    def swap_and_fill() -> None:
        swap_to_dir(target)
        (target / "inner.txt").write_bytes(b"user wrote this into the new dir")

    probe = race_after(monkeypatch, target=target, op="lstat", nth=3, when="before", interloper=swap_and_fill)
    before_inner = b"user wrote this into the new dir"
    assert safe_remove(target, root, expect="file") == "refused: expected a file, found a directory"
    assert probe.fired
    assert (target / "inner.txt").read_bytes() == before_inner


def test_swap_dir_to_file_before_act_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "root"
    d = root / "d"
    d.mkdir(parents=True)
    (d / "old").write_bytes(b"old")

    def to_file() -> None:
        os.replace(d, root / "d.swapped")
        d.write_bytes(b"user file now")

    probe = race_after(monkeypatch, target=d, op="lstat", nth=3, when="before", interloper=to_file)
    before = snapshot_user_bytes(root)
    assert safe_remove(d, root, expect="dir") == "refused: expected a directory, found a file"
    assert probe.fired
    assert_user_bytes_preserved(before, root)


def test_atomic_replace_between_check_and_act_is_documented_gap(
    monkeypatch: pytest.MonkeyPatch, tree: tuple[Path, Path]
) -> None:
    """Documented limit: safe_remove proves path identity, never content ownership.

    An atomic save of the named file between its last lstat and the unlink is removed by design;
    callers with user-editable bytes must use remove_if_hash. Pin the behaviour so a change is deliberate.
    """
    root, target = tree
    edited = b"user edit saved during the race"
    probe = race_after(monkeypatch, target=target, op="lstat", nth=3, interloper=lambda: atomic_replace(target, edited))
    assert safe_remove(target, root, expect="file") is None
    assert probe.fired
    assert not target.exists()


@pytest.mark.xfail(  # skip-category: tracked-defect
    strict=True,
    reason=(
        "documented limit: safe_remove removes by name, so an atomic save between its lstat and unlink is"
        " deleted; user-editable files need capture-then-verify (learning L-uUnd; HOOK-WITHDRAW parked)"
    ),
)
def test_atomic_replace_between_check_and_act_preserves_user_bytes(
    monkeypatch: pytest.MonkeyPatch, tree: tuple[Path, Path]
) -> None:
    root, target = tree
    edited = b"user edit saved during the race"
    race_after(monkeypatch, target=target, op="lstat", nth=3, interloper=lambda: atomic_replace(target, edited))
    safe_remove(target, root, expect="file")
    after = snapshot_user_bytes(root)
    import hashlib

    assert hashlib.sha256(edited).hexdigest() in after


def test_unreadable_parent_is_preserve_not_absent(tree: tuple[Path, Path]) -> None:
    root, target = tree
    sub = root / "sub"
    sub.mkdir()
    inner = sub / "f"
    inner.write_bytes(b"keep me")
    with unreadable_parent(inner):
        result = safe_remove(inner, root, expect="file")
        assert result is not None and result.startswith(("refused", "error"))
    assert inner.read_bytes() == b"keep me"
