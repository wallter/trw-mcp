"""E2E-INC-051: ``memory migrate --to user`` on a checkout that is already migrated is a no-op success, not an error."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.server._subcommands_memory import _run_memory_migrate
from trw_mcp.state import _store_migration
from trw_mcp.state._store_migration import AlreadyMigratedError, MigrationRefusedError


def _args(tmp_path: Path, *, apply: bool) -> argparse.Namespace:
    return argparse.Namespace(target_dir=str(tmp_path), rollback=None, apply=apply, to="user")


@pytest.mark.parametrize("apply", [False, True])
def test_an_already_migrated_checkout_exits_zero_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], apply: bool
) -> None:
    def already(_trw_dir: Path) -> None:
        raise AlreadyMigratedError(f"{tmp_path} is already migrated to project:x; nothing to do")

    monkeypatch.setattr(_store_migration, "apply_migration", already)
    monkeypatch.setattr(_store_migration, "preview_migration", already)

    _run_memory_migrate(_args(tmp_path, apply=apply))  # returns: no SystemExit, so the exit code is 0

    captured = capsys.readouterr()
    assert "already migrated" in captured.out and captured.err == ""


def test_a_real_refusal_still_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_trw_dir: Path) -> None:
        raise MigrationRefusedError("the memory daemon is not attached")

    monkeypatch.setattr(_store_migration, "apply_migration", refuse)

    with pytest.raises(SystemExit) as done:
        _run_memory_migrate(_args(tmp_path, apply=True))

    assert done.value.code == 1


def test_the_preflight_raises_the_already_migrated_subclass_for_a_pinned_empty_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    monkeypatch.setattr(_store_migration, "_pin", lambda _d: "project:pinned")
    monkeypatch.setattr(_store_migration, "holds_rows", lambda _s: False)

    with pytest.raises(AlreadyMigratedError, match="already migrated to project:pinned"):
        _store_migration._preflight(trw_dir)
