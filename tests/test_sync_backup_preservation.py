"""Restore-side and upload-side refusals of ``BackupUploader``: what it will NOT touch or send.

A failed or refused download must leave any file already at the destination alone and no partial
temp file behind; a switch flipped off mid-flow, an unreadable archive, or a presign answer without a
URL must produce zero data-bearing requests.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests._contact_support import payload_trw_dir
from tests.test_sync_backup_uploader import _install_mock_transport, _make_archive

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("governing_project")]

_KEY = "backups/org1/c1/2026-09-26T000000Z.db.gz"
_GET_URL = "https://bucket.s3.amazonaws.com/x?X-Amz-Signature=deadbeef"


def _uploader(**kw: object):  # type: ignore[no-untyped-def]
    from trw_mcp.sync.backup import BackupUploader

    return BackupUploader(
        backend_url="https://api.trwframework.com",
        api_key="k",
        client_id="c1",
        source_trw_dir=payload_trw_dir(),
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _contact_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: True)
    monkeypatch.setattr(
        "trw_mcp.sync.backup.platform_auth_headers",
        lambda url, api_key, source_trw_dir: {"Authorization": f"Bearer {api_key}"},
    )


async def test_download_refused_when_platform_contact_is_off_makes_no_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={}))
    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", lambda _root: False)
    dest = tmp_path / "restore" / "b.db.gz"

    result = await _uploader().download(_KEY, dest)

    assert result.status == "platform_contact_disabled"
    assert calls == []
    assert not dest.exists()


async def test_download_kill_switch_flipped_between_presign_and_get_skips_the_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = {"checks": 0}

    def contact(_root: object) -> bool:
        state["checks"] += 1
        return state["checks"] == 1  # on for the presign, off before the GET

    monkeypatch.setattr("trw_mcp.sync.backup.platform_contact_enabled", contact)
    calls = _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={"url": _GET_URL, "key": _KEY}))
    dest = tmp_path / "b.db.gz"
    dest.write_bytes(b"existing local backup")

    result = await _uploader().download(_KEY, dest)

    assert result.status == "platform_contact_disabled"
    assert [c.method for c in calls] == ["POST"]  # the presign only; no GET left the machine
    assert dest.read_bytes() == b"existing local backup"


@pytest.mark.parametrize(
    ("handler", "expected_status", "expected_error"),
    [
        (lambda req: httpx.Response(500, json={}), "presign_failed", "HTTPStatusError"),
        (lambda req: httpx.Response(200, json={"key": _KEY}), "presign_failed", "missing_presigned_url"),
        (lambda req: httpx.Response(200, json={"url": "", "key": _KEY}), "presign_failed", "missing_presigned_url"),
    ],
    ids=["presign-5xx", "no-url", "empty-url"],
)
async def test_download_presign_failure_writes_nothing_and_keeps_the_existing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: object, expected_status: str, expected_error: str
) -> None:
    calls = _install_mock_transport(monkeypatch, handler)  # type: ignore[arg-type]
    dest = tmp_path / "b.db.gz"
    dest.write_bytes(b"existing local backup")

    result = await _uploader().download(_KEY, dest)

    assert result.status == expected_status
    assert result.error == expected_error
    assert not result.ok
    assert dest.read_bytes() == b"existing local backup"
    assert not dest.with_suffix(".gz.tmp").exists()
    assert all(c.method == "POST" for c in calls)


async def test_failed_download_keeps_the_existing_destination_and_leaves_no_temp_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(200, json={"url": _GET_URL, "key": _KEY})
        return httpx.Response(503, content=b"partial")  # the GET leg fails

    _install_mock_transport(monkeypatch, handler)
    dest = tmp_path / "b.db.gz"
    dest.write_bytes(b"previous good restore")

    result = await _uploader().download(_KEY, dest)

    assert result.status == "download_failed"
    assert result.error == "HTTPStatusError"
    assert dest.read_bytes() == b"previous good restore"
    assert list(tmp_path.glob("*.tmp")) == []


async def test_download_dying_mid_stream_removes_the_partial_and_keeps_the_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def dies_after_first_chunk():  # type: ignore[no-untyped-def]
        yield b"partial-bytes"
        raise httpx.ReadError("connection reset")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(200, json={"url": _GET_URL, "key": _KEY})
        return httpx.Response(200, content=dies_after_first_chunk())

    _install_mock_transport(monkeypatch, handler)
    dest = tmp_path / "b.db.gz"
    dest.write_bytes(b"previous good restore")

    result = await _uploader().download(_KEY, dest)

    assert result.status == "download_failed"
    assert result.error == "ReadError"
    assert dest.read_bytes() == b"previous good restore"  # never replaced by the truncated download
    assert list(tmp_path.glob("*.tmp")) == []  # the partial file is gone


async def test_successful_download_replaces_the_destination_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Negative twin of the failure tests: the same setup with a 200 GET does replace the file."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/backup/presign":
            return httpx.Response(200, json={"url": _GET_URL, "key": _KEY})
        return httpx.Response(200, content=b"fresh bytes")

    _install_mock_transport(monkeypatch, handler)
    dest = tmp_path / "b.db.gz"
    dest.write_bytes(b"previous good restore")

    result = await _uploader().download(_KEY, dest)

    assert result.ok and result.path == dest
    assert dest.read_bytes() == b"fresh bytes"
    assert list(tmp_path.glob("*.tmp")) == []


async def test_upload_of_an_unreadable_archive_sends_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={}))

    result = await _uploader(backup_remote_enabled=True).upload(tmp_path / "missing.db.gz")

    assert result.status == "upload_failed"
    assert result.error == "archive_unreadable"
    assert calls == []


async def test_upload_without_a_presigned_url_never_puts_the_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={"key": _KEY}))
    archive, _sha = _make_archive(tmp_path)

    result = await _uploader(backup_remote_enabled=True).upload(archive)

    assert result.status == "presign_failed"
    assert result.error == "missing_presigned_url"
    assert [c.method for c in calls] == ["POST"]


async def test_list_drops_malformed_entries_instead_of_offering_them_for_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = {"key": _KEY, "uploaded_at": "2026-09-26T00:00:00Z", "size_bytes": 10}
    objects = [
        good,
        "not-a-dict",
        {"key": 5, "uploaded_at": "x", "size_bytes": 1},
        {"key": "k", "uploaded_at": "x"},
    ]
    _install_mock_transport(monkeypatch, lambda req: httpx.Response(200, json={"objects": objects}))

    result = await _uploader().list_remote()

    assert result.ok
    assert [o.key for o in result.objects] == [_KEY]
