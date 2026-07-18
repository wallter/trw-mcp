"""Rollback must not rewind mutable TRW runtime state."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from trw_mcp.bootstrap._update_transaction import (
    _MANAGED_TRW_FILES,
    _restore_transaction_snapshot,
    _snapshot_transaction_paths,
)
from trw_mcp.canons.registry import load_registry
from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH


def test_transaction_covers_every_managed_framework_artifact() -> None:
    registry = load_registry()
    expected = {
        str(DEPLOYMENT_RELATIVE_PATH),
        *(canon.runtime_compact_core for canon in registry.compiled_canons),
        *(canon.runtime_reference for canon in registry.compiled_canons),
    }
    assert expected <= set(_MANAGED_TRW_FILES)


def test_transaction_restore_preserves_runtime_writes_after_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "project"
    config = target / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: before\n", encoding="utf-8")
    framework = target / ".trw" / "frameworks" / "FRAMEWORK.md"
    framework.parent.mkdir(parents=True)
    framework.write_text("before framework\n", encoding="utf-8")

    snapshot = _snapshot_transaction_paths(target)
    try:
        config.write_text("version: changed\n", encoding="utf-8")
        framework.write_text("changed framework\n", encoding="utf-8")
        custom_framework = framework.with_name("CUSTOM.md")
        custom_framework.write_text("concurrent custom framework\n", encoding="utf-8")
        learning = target / ".trw" / "learnings" / "entries" / "concurrent.yaml"
        learning.parent.mkdir(parents=True)
        learning.write_text("id: concurrent\n", encoding="utf-8")
        run = target / ".trw" / "runs" / "active" / "meta" / "run.yaml"
        run.parent.mkdir(parents=True)
        run.write_text("status: active\n", encoding="utf-8")
        wal = target / ".trw" / "memory" / "memory.db-wal"
        wal.parent.mkdir(parents=True)
        wal.write_bytes(b"live-wal")

        _restore_transaction_snapshot(target, snapshot)

        assert config.read_text(encoding="utf-8") == "version: before\n"
        assert framework.read_text(encoding="utf-8") == "before framework\n"
        assert custom_framework.read_text(encoding="utf-8") == "concurrent custom framework\n"
        assert learning.read_text(encoding="utf-8") == "id: concurrent\n"
        assert run.read_text(encoding="utf-8") == "status: active\n"
        assert wal.read_bytes() == b"live-wal"
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def test_transaction_snapshot_excludes_memory_database(tmp_path: Path) -> None:
    target = tmp_path / "project"
    database = target / ".trw" / "memory" / "memory.db"
    database.parent.mkdir(parents=True)
    database.write_bytes(b"database")

    snapshot = _snapshot_transaction_paths(target)
    try:
        assert not (snapshot / ".trw" / "memory").exists()
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


def test_transaction_restore_refuses_replaced_trw_symlink(tmp_path: Path) -> None:
    target = tmp_path / "project"
    config = target / ".trw" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("original\n", encoding="utf-8")
    snapshot = _snapshot_transaction_paths(target)

    external = tmp_path / "external"
    external.mkdir()
    external_config = external / "config.yaml"
    external_config.write_text("must survive\n", encoding="utf-8")
    shutil.rmtree(target / ".trw")
    (target / ".trw").symlink_to(external, target_is_directory=True)

    try:
        with pytest.raises(OSError, match="contains a symlink"):
            _restore_transaction_snapshot(target, snapshot)
        assert external_config.read_text(encoding="utf-8") == "must survive\n"
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


@pytest.mark.parametrize(
    ("managed_path", "is_directory"),
    [
        (Path(".claude"), True),
        (Path(".claude/skills"), True),
        (Path("AGENTS.md"), False),
        (Path(".trw"), True),
    ],
)
def test_transaction_snapshot_rejects_managed_symlink_escape(
    tmp_path: Path,
    managed_path: Path,
    is_directory: bool,
) -> None:
    target = tmp_path / "project"
    target.mkdir()
    external = tmp_path / ("external-dir" if is_directory else "external-file")
    if is_directory:
        external.mkdir()
        (external / "sentinel.txt").write_text("untouched\n", encoding="utf-8")
    else:
        external.write_text("untouched\n", encoding="utf-8")
    link = target / managed_path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(external, target_is_directory=is_directory)

    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if not path.is_symlink())
    with pytest.raises(OSError, match="symlink"):
        _snapshot_transaction_paths(target)
    after = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if not path.is_symlink())

    assert after == before
    sentinel = external / "sentinel.txt" if is_directory else external
    assert sentinel.read_text(encoding="utf-8") == "untouched\n"
