"""Tests for `trw-mcp backup create` — PRD-CORE-311 FR05."""

from __future__ import annotations

import argparse
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


def _make_db(path: Path) -> None:
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    backend = SQLiteBackend(path)
    backend.close()


def _no_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory.daemon import DiscoveryAbsent

    monkeypatch.setattr("trw_memory.cli_client.read_live_discovery", lambda paths: DiscoveryAbsent(reason="no record"))


def test_backup_create_chains_local_and_remote_legs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR05 acceptance 1: consent on -> stdout names the remote key; presign+PUT are stubbed."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    _make_db(db_path)
    _no_daemon(monkeypatch)

    expected_key = "backups/org1/c1/key.db.gz"
    put_url = f"https://bucket.s3.amazonaws.com/{expected_key}?X-Amz-Signature=sig"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(200, json={"url": put_url, "key": expected_key})
        assert request.method == "PUT"
        return httpx.Response(200)

    calls = _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    fake_config = TRWConfig(
        backend_url="https://api.trwframework.com", platform_api_key="secret", backup_remote_enabled=True
    )
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: fake_config)

    args = argparse.Namespace(backup_command="create", namespace="default", db=str(db_path))
    _subcommands_backup.run_backup(args)

    out = capsys.readouterr().out
    assert "Created backup archive:" in out
    assert f"Uploaded to remote backup: {expected_key}" in out
    assert len(calls) == 2


def test_backup_create_local_only_when_disabled_makes_no_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR05 acceptance 2 / negative test: backup_remote_enabled=False -> local-only, zero HTTP calls."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    _make_db(db_path)
    _no_daemon(monkeypatch)

    calls = _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={}))

    fake_config = TRWConfig(backup_remote_enabled=False)
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: fake_config)

    args = argparse.Namespace(backup_command="create", namespace="default", db=str(db_path))
    _subcommands_backup.run_backup(args)

    out = capsys.readouterr().out
    assert "Created backup archive:" in out
    assert "Local-only: backup_remote_enabled is False, no remote upload attempted." in out
    assert len(calls) == 0


def test_backup_create_refuses_beside_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR02's daemon refusal applies unchanged through the trw-mcp CLI leg."""
    from trw_memory.daemon import DaemonInfo

    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    _make_db(db_path)

    live_info = DaemonInfo(pid=1, url="http://127.0.0.1:1/mcp", started_at="2026-01-01T00:00:00Z", version="1.0.0")
    monkeypatch.setattr("trw_memory.cli_client.read_live_discovery", lambda paths: live_info)

    args = argparse.Namespace(backup_command="create", namespace="default", db=str(db_path))
    with pytest.raises(SystemExit) as exc_info:
        _subcommands_backup.run_backup(args)

    assert exc_info.value.code == 1
    assert "daemon" in capsys.readouterr().err.lower()


def test_backup_create_presign_failure_reports_typed_failure_and_keeps_local_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Negative/fallback: presign unreachable -> a typed failure is reported; the local archive still exists."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    _make_db(db_path)
    _no_daemon(monkeypatch)

    _install_mock_transport(monkeypatch, lambda req: httpx.Response(503, text="unreachable"))
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    fake_config = TRWConfig(
        backend_url="https://api.trwframework.com", platform_api_key="k", backup_remote_enabled=True
    )
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: fake_config)

    args = argparse.Namespace(backup_command="create", namespace="default", db=str(db_path))
    _subcommands_backup.run_backup(args)

    captured = capsys.readouterr()
    assert "Created backup archive:" in captured.out
    archive_line = next(line for line in captured.out.splitlines() if line.startswith("Created backup archive:"))
    archive_path = Path(archive_line.split(": ", 1)[1])
    assert archive_path.exists()
    assert "Remote upload failed" in captured.err


def test_the_uploaded_checksum_and_length_are_those_of_the_compressed_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sol r1 on core311-s4: the archive's sha256 covers the DECOMPRESSED snapshot, but S3 checks the object it
    receives. The presign body and the PUT headers must carry the digest and length of the .db.gz bytes sent."""
    import base64
    import hashlib
    import json

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _subcommands_backup

    db_path = tmp_path / "memory.db"
    _make_db(db_path)
    _no_daemon(monkeypatch)
    key = "backups/org1/c1/key.db.gz"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(
                200, json={"url": f"https://bucket.s3.amazonaws.com/{key}?X-Amz-Signature=s", "key": key}
            )
        return httpx.Response(200)

    calls = _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)
    config = TRWConfig(backend_url="https://api.trwframework.com", platform_api_key="k", backup_remote_enabled=True)
    monkeypatch.setattr(_subcommands_backup, "_load_config", lambda: config)

    _subcommands_backup.run_backup(argparse.Namespace(backup_command="create", namespace="default", db=str(db_path)))

    (archive,) = list(tmp_path.rglob("*.db.gz"))
    raw = archive.read_bytes()
    presign, put = calls
    body = json.loads(presign.content)
    assert body["sha256"] == hashlib.sha256(raw).hexdigest()
    assert body["size_bytes"] == len(raw)
    assert put.headers["x-amz-checksum-sha256"] == base64.b64encode(hashlib.sha256(raw).digest()).decode()
    assert put.headers["content-length"] == str(len(raw))
