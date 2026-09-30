"""Tests for `trw-mcp backup restore` — PRD-CORE-311 FR06 (the PRD's own Definition of Done)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

# A real send needs a payload project: its policy is read from that project's .trw.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("governing_project")]


def _install_mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    calls: list[httpx.Request] = []
    original_async_client = httpx.AsyncClient

    def _capturing_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    transport = httpx.MockTransport(_capturing_handler)

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.daemon import DiscoveryAbsent

    monkeypatch.setattr("trw_memory.cli_client.read_live_discovery", lambda paths: DiscoveryAbsent(reason="no record"))


def _plant_learning(db_path: Path, content: str) -> str:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    backend = SQLiteBackend(db_path)
    entry = MemoryEntry(id="M-planted-001", content=content, namespace="default")
    backend.store(entry)
    backend.close()
    return entry.id


def _recall_content(db_path: Path, entry_id: str) -> str | None:
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    backend = SQLiteBackend(db_path)
    try:
        got = backend.get(entry_id, namespace="default")
    finally:
        backend.close()
    return got.content if got is not None else None


def test_restore_from_local_archive_offline_drill_into_fresh_client_recalls_planted_learning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """NFR01's offline drill leg: `--from <path>` restores with zero network calls."""
    from trw_memory.storage._backup_archive import create_backup_archive

    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    planted_content = "the restore drill must recall this exact sentence"
    entry_id = _plant_learning(db_path, planted_content)

    archive = create_backup_archive(tmp_path, db_path)

    # Simulate a lost store: the fresh client starts from nothing.
    db_path.unlink()

    calls = _install_mock_transport(monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("no network")))

    args = argparse.Namespace(
        backup_command="restore", yes=True, restore_from=str(archive.path), namespace="default", db=str(db_path)
    )
    _subcommands_backup.run_backup(args)

    assert len(calls) == 0, "the offline drill must make zero network calls"
    out = capsys.readouterr().out
    assert "Restored" in out

    assert _recall_content(db_path, entry_id) == planted_content


def test_restore_from_latest_into_fresh_client_recalls_planted_learning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR06's own DoD evidence: `--from latest` lists, downloads, and restores into a client that recalls by content."""
    from trw_memory.storage._backup_archive import create_backup_archive

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    source_db = tmp_path / "source" / "memory.db"
    source_db.parent.mkdir(parents=True)
    planted_content = "the DoD restore drill recalls this planted learning by content"
    entry_id = _plant_learning(source_db, planted_content)
    archive = create_backup_archive(tmp_path / "source", source_db)
    archive_bytes = archive.path.read_bytes()

    key = "backups/org1/c1/2026-09-26T000000Z.db.gz"
    presign_get_url = f"https://bucket.s3.amazonaws.com/{key}?X-Amz-Signature=getsig"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/list":
            return httpx.Response(
                200,
                json={
                    "objects": [
                        {
                            "key": key,
                            "uploaded_at": datetime.now(timezone.utc).isoformat(),
                            "size_bytes": len(archive_bytes),
                        }
                    ]
                },
            )
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(200, json={"url": presign_get_url, "key": key, "expires_in": 900})
        assert str(request.url) == presign_get_url
        return httpx.Response(200, content=archive_bytes)

    calls = _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    # A FRESH client's target store: nothing exists here yet.
    fresh_db = tmp_path / "fresh" / "memory.db"

    fake_config = TRWConfig(backend_url="https://api.trwframework.com", platform_api_key="k")
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: fake_config)

    args = argparse.Namespace(
        backup_command="restore", yes=True, restore_from="latest", namespace="default", db=str(fresh_db)
    )
    _subcommands_backup.run_backup(args)

    out = capsys.readouterr().out
    assert "Restored" in out
    assert len(calls) == 3  # list, presign, GET

    assert _recall_content(fresh_db, entry_id) == planted_content


def test_restore_from_latest_no_remote_backups_exits_nonzero_with_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """US-001 negative acceptance: no remote backup exists -> non-zero exit, clear message, no traceback."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/backup/list"
        return httpx.Response(200, json={"objects": []})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    fake_config = TRWConfig(backend_url="https://api.trwframework.com", platform_api_key="k")
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: fake_config)

    args = argparse.Namespace(
        backup_command="restore", yes=True, restore_from="latest", namespace="default", db=str(tmp_path / "memory.db")
    )
    with pytest.raises(SystemExit) as exc_info:
        _subcommands_backup.run_backup(args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "no remote backups found" in err.lower()


def test_restore_from_latest_malformed_objects_field_is_typed_failure_not_no_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Coordinator correction: a missing/malformed `objects` field is a typed failure, never 'no backups'."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/backup/list"
        return httpx.Response(200, json={"unexpected": "shape"})

    _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    fake_config = TRWConfig(backend_url="https://api.trwframework.com", platform_api_key="k")
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: fake_config)

    args = argparse.Namespace(
        backup_command="restore", yes=True, restore_from="latest", namespace="default", db=str(tmp_path / "memory.db")
    )
    with pytest.raises(SystemExit) as exc_info:
        _subcommands_backup.run_backup(args)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err.lower()
    assert "could not list remote backups" in err
    assert "no remote backups found" not in err


def test_restore_sha256_mismatch_refuses_local_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sha256 mismatch refuses (via restore_from_archive) and never touches the target store."""
    from trw_memory.storage._backup_archive import create_backup_archive

    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    entry_id = _plant_learning(db_path, "must survive a rejected restore")
    archive = create_backup_archive(tmp_path, db_path)

    # Corrupt the sidecar so the digest never matches the archive's real content.
    sidecar = archive.path.with_name(archive.path.name + ".sha256")
    corrupted_name = sidecar.read_text(encoding="utf-8").split(maxsplit=1)[1]
    sidecar.write_text(f"{'0' * 64}  {corrupted_name}\n", encoding="utf-8")

    args = argparse.Namespace(
        backup_command="restore", yes=True, restore_from=str(archive.path), namespace="default", db=str(db_path)
    )
    with pytest.raises(SystemExit) as exc_info:
        _subcommands_backup.run_backup(args)

    assert exc_info.value.code == 1
    assert "sha256 mismatch" in capsys.readouterr().err.lower()
    assert _recall_content(db_path, entry_id) == "must survive a rejected restore"


def test_restore_refuses_beside_running_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Restore refuses beside a daemon (restore_from_archive delegates to restore_from_snapshot's guard)."""
    from trw_memory.exceptions import StoreBusyError
    from trw_memory.storage._backup_archive import create_backup_archive

    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    _plant_learning(db_path, "protected by the daemon guard")
    archive = create_backup_archive(tmp_path, db_path)

    import trw_memory.storage._snapshot as snapshot_mod

    def _busy(*args: object, **kwargs: object) -> object:
        raise StoreBusyError("store is busy")

    monkeypatch.setattr(snapshot_mod, "store_access", _busy)

    args = argparse.Namespace(
        backup_command="restore", yes=True, restore_from=str(archive.path), namespace="default", db=str(db_path)
    )
    with pytest.raises(SystemExit) as exc_info:
        _subcommands_backup.run_backup(args)

    assert exc_info.value.code == 1
    assert "busy" in capsys.readouterr().err.lower()
