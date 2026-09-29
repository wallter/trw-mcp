"""Tests for BackupUploader — PRD-CORE-311-FR03."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
import structlog

from tests._contact_support import payload_trw_dir

# A real send needs a payload project: its policy is read from that project's .trw.
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("governing_project")]


def _install_mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Patch httpx.AsyncClient to route every request through *handler*.

    Returns the list of captured requests (appended to as calls happen) so
    tests can assert call counts and inspect request bodies/headers/urls.
    """
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


def _make_archive(tmp_path: Path, content: bytes = b"gzip-archive-bytes") -> tuple[Path, str]:
    archive_path = tmp_path / "archive.db.gz"
    archive_path.write_bytes(content)
    sha256 = hashlib.sha256(content).hexdigest()
    return archive_path, sha256


async def test_upload_disabled_by_default_makes_no_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR03 acceptance 2: backup_remote_enabled=False (default) -> zero HTTP requests."""
    from trw_mcp.sync.backup import BackupUploader

    calls = _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={}))
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    archive_path, sha256 = _make_archive(tmp_path)
    uploader = BackupUploader(
        backend_url="https://api.trwframework.com", api_key="k", client_id="c1", source_trw_dir=payload_trw_dir()
    )
    result = await uploader.upload(archive_path)

    assert result.status == "disabled"
    assert not result.ok
    assert len(calls) == 0


async def test_upload_platform_contact_disabled_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The global kill switch off blocks the request even when backup_remote_enabled=True."""
    from trw_mcp.sync.backup import BackupUploader

    calls = _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={}))
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: False)

    archive_path, sha256 = _make_archive(tmp_path)
    uploader = BackupUploader(
        backend_url="https://api.trwframework.com",
        api_key="k",
        client_id="c1",
        backup_remote_enabled=True,
        source_trw_dir=payload_trw_dir(),
    )
    result = await uploader.upload(archive_path)

    assert result.status == "platform_contact_disabled"
    assert not result.ok
    assert len(calls) == 0


async def test_upload_happy_path_presigns_and_puts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """FR03 acceptance 1: presigned PUT is requested, bytes uploaded, result names the key."""
    from trw_mcp.sync.backup import BackupUploader

    content = b"gzip-archive-bytes" * 100
    archive_path, sha256 = _make_archive(tmp_path, content)
    expected_key = "backups/org1/c1/2026-09-26T000000Z.db.gz"
    put_url = f"https://bucket.s3.amazonaws.com/{expected_key}?X-Amz-Signature=deadbeef"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/presign":
            body = json.loads(request.content)
            assert body == {"client_id": "c1", "sha256": sha256, "size_bytes": len(content)}
            return httpx.Response(200, json={"url": put_url, "key": expected_key})
        assert str(request.url) == put_url
        assert request.method == "PUT"
        checksum_b64 = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
        assert request.headers.get("x-amz-checksum-sha256") == checksum_b64
        assert request.content == content
        return httpx.Response(200)

    calls = _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)
    monkeypatch.setattr(
        "trw_mcp.sync.backup.platform_auth_headers",
        lambda url, api_key, source_trw_dir: {"Authorization": f"Bearer {api_key}"},
    )

    uploader = BackupUploader(
        backend_url="https://api.trwframework.com",
        api_key="secret-key",
        client_id="c1",
        backup_remote_enabled=True,
        source_trw_dir=payload_trw_dir(),
    )
    result = await uploader.upload(archive_path)

    assert result.status == "ok"
    assert result.ok
    assert result.key == expected_key
    assert result.uploaded_at is not None
    assert len(calls) == 2


async def test_upload_presign_4xx_returns_typed_failure_and_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A presign rejection returns a typed failure and logs backup_upload_error, never the API key."""
    from trw_mcp.sync.backup import BackupUploader

    archive_path, sha256 = _make_archive(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    calls = _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    uploader = BackupUploader(
        backend_url="https://api.trwframework.com",
        api_key="super-secret-key",
        client_id="c1",
        backup_remote_enabled=True,
        source_trw_dir=payload_trw_dir(),
    )
    with structlog.testing.capture_logs() as captured_logs:
        result = await uploader.upload(archive_path)

    assert result.status == "presign_failed"
    assert not result.ok
    assert len(calls) == 1

    error_events = [e for e in captured_logs if e.get("event") == "backup_upload_error"]
    assert len(error_events) == 1
    assert error_events[0]["stage"] == "presign"
    serialized_events = json.dumps(captured_logs)
    assert "super-secret-key" not in serialized_events
    assert "X-Amz-Signature" not in serialized_events
    assert "?" not in serialized_events or "X-Amz" not in serialized_events


async def test_upload_put_5xx_returns_typed_failure_and_logs_no_signature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A PUT failure returns a typed failure and never logs the presigned URL's query string."""
    from trw_mcp.sync.backup import BackupUploader

    content = b"payload-bytes"
    archive_path, sha256 = _make_archive(tmp_path, content)
    signed_put_url = "https://bucket.s3.amazonaws.com/backups/org1/c1/key.db.gz?X-Amz-Signature=topsecretsig"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(200, json={"url": signed_put_url, "key": "backups/org1/c1/key.db.gz"})
        return httpx.Response(500, text="internal error")

    calls = _install_mock_transport(monkeypatch, handler)
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)

    uploader = BackupUploader(
        backend_url="https://api.trwframework.com",
        api_key="k",
        client_id="c1",
        backup_remote_enabled=True,
        source_trw_dir=payload_trw_dir(),
    )
    with structlog.testing.capture_logs() as captured_logs:
        result = await uploader.upload(archive_path)

    assert result.status == "upload_failed"
    assert not result.ok
    assert len(calls) == 2

    error_events = [e for e in captured_logs if e.get("event") == "backup_upload_error"]
    assert len(error_events) == 1
    assert error_events[0]["stage"] == "put"
    serialized_events = json.dumps(captured_logs)
    assert "topsecretsig" not in serialized_events
    assert "X-Amz-Signature" not in serialized_events


async def test_upload_kill_switch_flipped_off_between_presign_and_put_skips_put(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Per-request recheck: platform_contact_enabled going False after presign blocks the PUT."""
    from trw_mcp.sync.backup import BackupUploader

    archive_path, sha256 = _make_archive(tmp_path)
    put_url = "https://bucket.s3.amazonaws.com/backups/org1/c1/key.db.gz?X-Amz-Signature=sig"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/backup/presign", "PUT must never be attempted once the switch is off"
        return httpx.Response(200, json={"url": put_url, "key": "backups/org1/c1/key.db.gz"})

    calls = _install_mock_transport(monkeypatch, handler)

    contact_states = iter([True, False])
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: next(contact_states, False))

    uploader = BackupUploader(
        backend_url="https://api.trwframework.com",
        api_key="k",
        client_id="c1",
        backup_remote_enabled=True,
        source_trw_dir=payload_trw_dir(),
    )
    result = await uploader.upload(archive_path)

    assert result.status == "platform_contact_disabled"
    assert not result.ok
    assert len(calls) == 1  # only the presign call; PUT never attempted
