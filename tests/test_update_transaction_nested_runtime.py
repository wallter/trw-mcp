"""Rollback preserves unmanaged runtime directories at every nesting depth."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from trw_mcp.bootstrap._update_transaction import _restore_transaction_snapshot, _snapshot_transaction_paths


@pytest.mark.parametrize("runtime_name", ["node_modules", "worktrees", "nested-repo"])
@pytest.mark.parametrize("present_at_snapshot", [True, False])
def test_rollback_preserves_deep_runtime(tmp_path: Path, runtime_name: str, present_at_snapshot: bool) -> None:
    root = tmp_path / "project"
    root.mkdir()
    managed = root / ".claude" / "custom" / "deep" / "settings.json"
    runtime = managed.parent / runtime_name

    def populate() -> None:
        runtime.mkdir(parents=True)
        (runtime / "valuable.txt").write_text("user-owned", encoding="utf-8")
        if runtime_name == "nested-repo":
            (runtime / ".git").write_text("gitdir: elsewhere", encoding="utf-8")

    if present_at_snapshot:
        populate()
        managed.write_text("before", encoding="utf-8")
    snapshot = _snapshot_transaction_paths(root)
    try:
        if not present_at_snapshot:
            populate()
        managed.write_text("failed-update", encoding="utf-8")
        _restore_transaction_snapshot(root, snapshot)
        assert (runtime / "valuable.txt").read_text(encoding="utf-8") == "user-owned"
        if present_at_snapshot:
            assert managed.read_text(encoding="utf-8") == "before"
        else:
            assert not managed.exists()
    finally:
        shutil.rmtree(snapshot)
