"""E2E-BACKUP-DAEMON-LAYOUT (INC-050): backup finds the store the daemon serves; restore never replaces blind.

``backup create`` defaulted to the pre-daemon ``<proj>/.memory/default/memory.db`` and failed unless given an
undocumented ``--db``; ``backup restore`` replaced the store (every project's memory in the one shared file)
with no copy of what it overwrote and no confirmation (HB-2). Now the default is ``DaemonPaths``' store,
and a restore names the file it replaces, needs ``--yes`` (or an interactive yes), and archives the current
store first.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_cli_backup_restore import _no_daemon, _plant_learning, _recall_content

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("governing_project")]


@pytest.fixture
def daemon_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the daemon's resolver at a store under tmp_path (the one file every namespace lives in)."""
    store = tmp_path / "user" / "memory" / "memory.db"
    store.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "trw_memory.daemon.DaemonPaths.resolve", classmethod(lambda cls, create=True: SimpleNamespace(store=store))
    )
    _no_daemon(monkeypatch)
    return store


def _args(**kw: object) -> argparse.Namespace:
    return argparse.Namespace(**{"namespace": "default", "db": None, "yes": False, **kw})


def test_create_without_db_archives_the_store_the_daemon_serves(
    daemon_store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _subcommands_backup

    _plant_learning(daemon_store, "kept in the user store")

    _subcommands_backup.run_backup(_args(backup_command="create"))

    out = capsys.readouterr().out
    assert "Created backup archive:" in out
    assert str(daemon_store.parent) in out


def test_a_storage_path_alone_archives_the_served_store_and_warns(
    daemon_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """E2E-BACKUP-DAEMON-STORE-ONE-RESOLVER: the daemon ignores MEMORY_STORAGE_PATH for its store, so backup
    archives what it serves, names that file, and says the explicit setting was not honoured."""
    from trw_mcp.server import _subcommands_backup

    _plant_learning(daemon_store, "the served store")
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("MEMORY_STORAGE_PATH", str(elsewhere))

    _subcommands_backup.run_backup(_args(backup_command="create"))

    captured = capsys.readouterr()
    assert f"Archived store: {daemon_store.resolve()}" in captured.out
    assert "MEMORY_STORAGE_PATH" in captured.err and str(daemon_store.resolve()) in captured.err
    assert str((elsewhere / "default" / "memory.db").resolve()) in captured.err
    assert not (elsewhere / "default" / "memory.db").exists(), "the unserved path is never created or archived"


def test_the_daemon_and_backup_share_one_resolver(
    daemon_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The single-store setting wins for both; otherwise both name the user store."""
    import argparse as _argparse

    from trw_memory.daemon import served_store_path

    from trw_mcp.server._subcommands_backup import _resolve_base_and_db

    ns = _argparse.Namespace(db=None, namespace="default")
    assert served_store_path() == daemon_store
    assert _resolve_base_and_db(ns)[1] == daemon_store.resolve()
    single = tmp_path / "single.db"
    monkeypatch.setenv("MEMORY_SINGLE_STORE_PATH", str(single))
    assert served_store_path() == single
    assert _resolve_base_and_db(ns)[1] == single.resolve()


def _archive_of(store: Path, content: str) -> Path:
    from trw_memory.storage._backup_archive import create_backup_archive

    _plant_learning(store, content)
    return create_backup_archive(store.parent, store).path


def test_restore_without_yes_refuses_non_interactively_and_changes_nothing(
    daemon_store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _subcommands_backup

    archive = _archive_of(daemon_store, "old")
    entry_id = _plant_learning(daemon_store, "newer and precious")
    before = daemon_store.read_bytes()

    with pytest.raises(SystemExit) as exited:
        _subcommands_backup.run_backup(_args(backup_command="restore", restore_from=str(archive)))

    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert str(daemon_store) in err and "--yes" in err
    assert daemon_store.read_bytes() == before
    assert _recall_content(daemon_store, entry_id) == "newer and precious"


def test_restore_with_yes_snapshots_the_current_store_before_replacing_it(
    daemon_store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_memory.storage._backup_archive import restore_from_archive

    from trw_mcp.server import _subcommands_backup

    archive = _archive_of(daemon_store, "old")
    entry_id = _plant_learning(daemon_store, "newer and precious")

    _subcommands_backup.run_backup(_args(backup_command="restore", restore_from=str(archive), yes=True))

    out = capsys.readouterr().out
    assert _recall_content(daemon_store, entry_id) == "old"  # the restore happened
    snapshot_line = next(line for line in out.splitlines() if line.startswith("Saved the current store to "))
    snapshot = Path(snapshot_line.removeprefix("Saved the current store to ").split(" ", 1)[0])
    assert snapshot.is_file() and snapshot != archive
    recovered = daemon_store.parent / "recovered.db"
    restore_from_archive(snapshot, recovered)
    assert _recall_content(recovered, entry_id) == "newer and precious"  # nothing the restore replaced is lost


@pytest.mark.parametrize("verb", [["create"], ["restore", "--from", "x.db.gz"]])
def test_namespace_is_no_longer_accepted(verb: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """Lead ruling: a flag that selects nothing is a false affordance, so argparse rejects it."""
    from trw_mcp.server._cli_argparse_backup import add_backup_subcommands

    parser = argparse.ArgumentParser(prog="trw-mcp")
    add_backup_subcommands(parser.add_subparsers(dest="command"))
    with pytest.raises(SystemExit) as exited:
        parser.parse_args(["backup", *verb, "--namespace", "default"])

    assert exited.value.code == 2
    assert "unrecognized arguments: --namespace" in capsys.readouterr().err


def _archive_elsewhere(tmp_path: Path, content: str) -> Path:
    """A valid archive made from a separate store, so the target store can be corrupted freely."""
    from trw_memory.storage._backup_archive import create_backup_archive

    other = tmp_path / "source" / "memory.db"
    other.parent.mkdir()
    _plant_learning(other, content)
    return create_backup_archive(other.parent, other).path


def test_a_corrupt_store_is_byte_copied_then_restored(
    daemon_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Restore's main use: the store is corrupt, so VACUUM INTO fails; a raw copy is kept and the restore runs."""
    from trw_mcp.server import _subcommands_backup

    archive = _archive_elsewhere(tmp_path, "recovered content")
    garbage = b"this is not a sqlite database" * 64
    daemon_store.write_bytes(garbage)
    (daemon_store.parent / "memory.db-wal").write_bytes(b"wal bytes")

    _subcommands_backup.run_backup(_args(backup_command="restore", restore_from=str(archive), yes=True))

    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("Saved a raw copy of the current store to "))
    raw_dir = Path(line.removeprefix("Saved a raw copy of the current store to ").split(" ", 1)[0])
    assert (raw_dir / "memory.db").read_bytes() == garbage
    assert (raw_dir / "memory.db-wal").read_bytes() == b"wal bytes"
    assert _recall_content(daemon_store, "M-planted-001") == "recovered content"


def test_when_both_copies_fail_restore_refuses_and_leaves_the_store(
    daemon_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _subcommands_backup

    archive = _archive_elsewhere(tmp_path, "recovered content")
    daemon_store.write_bytes(b"corrupt")

    def no_copy(*_: object, **__: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr("trw_memory.safe_fs.write_beneath", no_copy)
    with pytest.raises(SystemExit) as exited:
        _subcommands_backup.run_backup(_args(backup_command="restore", restore_from=str(archive), yes=True))

    assert exited.value.code == 1
    assert daemon_store.read_bytes() == b"corrupt"
    assert "--no-snapshot" in capsys.readouterr().err


def test_no_snapshot_needs_yes(daemon_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server import _subcommands_backup

    archive = _archive_elsewhere(tmp_path, "x")
    daemon_store.write_bytes(b"corrupt")
    with pytest.raises(SystemExit) as exited:
        _subcommands_backup.run_backup(_args(backup_command="restore", restore_from=str(archive), no_snapshot=True))

    assert exited.value.code == 2
    assert daemon_store.read_bytes() == b"corrupt"


def test_no_snapshot_with_yes_restores_and_says_nothing_was_kept(
    daemon_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _subcommands_backup

    archive = _archive_elsewhere(tmp_path, "recovered content")
    daemon_store.write_bytes(b"corrupt")

    _subcommands_backup.run_backup(
        _args(backup_command="restore", restore_from=str(archive), yes=True, no_snapshot=True)
    )

    assert "NO copy of the current store was kept" in capsys.readouterr().err
    assert _recall_content(daemon_store, "M-planted-001") == "recovered content"
    assert not (daemon_store.parent / "memory" / "backups").exists()
