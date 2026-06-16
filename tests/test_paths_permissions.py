"""PRD-QUAL-110-FR02: consistent directory + secret permissions.

``.trw/`` directories that hold state/secrets are created mode ``0700`` and
secret-bearing files (e.g. ``memory.db``) mode ``0600``, mirroring the existing
``pins.json`` 0600 hardening (``_pin_store.py:390``). On non-POSIX platforms the
chmod degrades to a WARNING and proceeds.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from trw_mcp.state import _paths_permissions

_POSIX_ONLY = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX mode bits")


@_POSIX_ONLY
def test_harden_dir_mode_is_0700(tmp_path: Path) -> None:
    d = tmp_path / "memory"
    _paths_permissions.harden_dir_mode(d, create=True)
    mode = stat.S_IMODE(os.stat(d).st_mode)
    assert mode == 0o700


@_POSIX_ONLY
def test_harden_secret_file_mode_is_0600(tmp_path: Path) -> None:
    f = tmp_path / "memory.db"
    f.write_bytes(b"")
    _paths_permissions.harden_secret_file_mode(f)
    mode = stat.S_IMODE(os.stat(f).st_mode)
    assert mode == 0o600


def test_harden_dir_creates_when_requested(tmp_path: Path) -> None:
    d = tmp_path / "nested" / "memory"
    assert not d.exists()
    _paths_permissions.harden_dir_mode(d, create=True)
    assert d.is_dir()


def test_harden_secret_file_missing_is_noop(tmp_path: Path) -> None:
    """A missing secret file is a no-op (best-effort, never raises)."""
    f = tmp_path / "does-not-exist.db"
    # Must not raise.
    _paths_permissions.harden_secret_file_mode(f)
    assert not f.exists()


def test_harden_dir_chmod_failure_warns_and_proceeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chmod failure (non-POSIX) logs a WARNING and proceeds, never raises."""
    d = tmp_path / "memory"
    d.mkdir()

    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("not supported")

    monkeypatch.setattr(_paths_permissions.os, "chmod", _boom)
    with capture_logs() as logs:
        _paths_permissions.harden_dir_mode(d, create=False)  # must not raise
    events = {e.get("event") for e in logs}
    assert "path_chmod_failed" in events


@_POSIX_ONLY
def test_harden_trw_tree_creates_root_and_subdirs_0700(tmp_path: Path) -> None:
    """harden_trw_tree(create_subdirs=True) makes .trw root + state subdirs 0700."""
    trw_dir = tmp_path / ".trw"
    _paths_permissions.harden_trw_tree(trw_dir, create_subdirs=True)

    assert stat.S_IMODE(os.stat(trw_dir).st_mode) == 0o700
    for name in ("runs", "learnings", "logs", "runtime"):
        sub = trw_dir / name
        assert sub.is_dir(), f"{name} not created"
        assert stat.S_IMODE(os.stat(sub).st_mode) == 0o700, f"{name} not 0700"


@_POSIX_ONLY
def test_harden_trw_tree_tightens_existing_loose_subdirs(tmp_path: Path) -> None:
    """An existing 0755 .trw subdir is tightened to 0700 (no create_subdirs)."""
    trw_dir = tmp_path / ".trw"
    runs = trw_dir / "runs"
    runs.mkdir(parents=True)
    os.chmod(trw_dir, 0o755)
    os.chmod(runs, 0o755)

    _paths_permissions.harden_trw_tree(trw_dir)

    assert stat.S_IMODE(os.stat(trw_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(runs).st_mode) == 0o700


def test_harden_trw_tree_missing_root_noop(tmp_path: Path) -> None:
    """Missing .trw root with create_subdirs=False is a no-op (no creation)."""
    trw_dir = tmp_path / ".trw"
    _paths_permissions.harden_trw_tree(trw_dir)
    assert not trw_dir.exists()


@_POSIX_ONLY
def test_scaffold_run_directory_hardens_trw_tree_0700(tmp_path: Path) -> None:
    """Run-init via orchestration_service leaves a fresh .trw tree at 0700."""
    from trw_mcp.services import orchestration_service

    trw_dir = tmp_path / ".trw"
    orchestration_service.scaffold_run_directory("demo-task", trw_dir=trw_dir)

    assert stat.S_IMODE(os.stat(trw_dir).st_mode) == 0o700
    for name in ("runs", "learnings", "logs"):
        sub = trw_dir / name
        assert sub.is_dir()
        assert stat.S_IMODE(os.stat(sub).st_mode) == 0o700, f"{name} not 0700"


@_POSIX_ONLY
def test_surface_log_dir_hardened_0700(tmp_path: Path) -> None:
    """surface_tracking log write creates .trw + .trw/logs at 0700."""
    from trw_mcp.state import surface_tracking

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    surface_tracking.log_surface_event(
        trw_dir,
        learning_id="L-x",
        surface_type="nudge",
    )
    logs_dir = trw_dir / "logs"
    assert logs_dir.is_dir()
    assert stat.S_IMODE(os.stat(logs_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(trw_dir).st_mode) == 0o700


@_POSIX_ONLY
def test_memory_dir_and_db_hardened_on_backend_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_backend() creates .trw/memory dir 0700 and memory.db 0600."""
    from trw_mcp.state import _memory_connection

    _memory_connection.reset_backend()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    backend = _memory_connection.get_backend(trw_dir=trw_dir)
    try:
        memory_dir = trw_dir / "memory"
        db_path = memory_dir / "memory.db"
        assert memory_dir.is_dir()
        assert stat.S_IMODE(os.stat(memory_dir).st_mode) == 0o700
        assert db_path.exists()
        assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600
    finally:
        _memory_connection.reset_backend()
