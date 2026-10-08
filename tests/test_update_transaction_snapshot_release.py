"""Dry-run transactions release their real liveness descriptor before removal."""

import os
import sys
from pathlib import Path

import pytest
from trw_memory._tree_removal import remove_tree

from trw_mcp.bootstrap import _snapshot_fd, _update_transaction

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("raises", [False, True])
def test_scratch_releases_lock_before_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raises: bool) -> None:
    if sys.platform == "win32":
        pytest.skip("requires flock")
    target = tmp_path / "project"
    target.mkdir()
    snapshots: list[Path] = []
    descriptors: list[int] = []
    remove = remove_tree

    def apply(scratch: Path) -> None:
        snapshots.append(scratch)
        descriptors.append(_snapshot_fd._HELD[str(scratch)])
        if raises:
            raise ValueError("apply failed")

    def checked_remove(path: Path, *, purpose: str) -> None:
        assert str(path) not in _snapshot_fd._HELD
        with pytest.raises(OSError):
            os.fstat(descriptors[-1])
        remove(path, purpose=purpose)

    monkeypatch.setattr(_update_transaction, "remove_tree", checked_remove)
    try:
        for _ in range(3):
            if raises:
                with pytest.raises(ValueError, match="apply failed"):
                    _update_transaction.run_in_scratch(target, {"errors": []}, apply)
            else:
                _update_transaction.run_in_scratch(target, {"errors": []}, apply)
        assert all(not snapshot.exists() for snapshot in snapshots)
    finally:
        for snapshot in snapshots:
            _snapshot_fd.release_snapshot(snapshot)
            if snapshot.exists():
                remove(snapshot, purpose="test cleanup")
