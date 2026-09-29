"""Presigned-PUT backup-archive uploader — PRD-CORE-311-FR03.

Off-machine leg of the memory-backup drill: uploads a local gzip archive
(``trw-memory``'s ``create_backup_archive``) to S3 through a short-lived
presigned URL the client never holds direct AWS credentials to request.
Mirrors ``SyncPusher``'s fail-open contract (``push.py:91-97`` / the
``never raises`` docstring on ``push_learnings``): every path returns a typed
:class:`BackupUploadResult`, never an exception, past this module's boundary.

Two independent consent gates apply, both checked BEFORE any HTTP client is
constructed:

1. ``backup_remote_enabled`` (new field, default ``False``,
   ``_fields_telemetry.py``) — the backup-specific consent flag. Unlike
   learning sync's anonymized summary+detail payload, a backup archive is the
   RAW local store, so this is a distinct, more conservative gate rather than
   a reuse of ``learning_sharing_enabled``.
2. ``platform_contact_enabled()`` (``state._platform_trust``) — the
   operator's global egress kill switch, re-checked immediately before the
   PUT (per-request recheck), matching ``push.py:187``'s
   ``if not platform_contact_enabled(root): break`` pattern for each batch.

The presign POST and the PUT both attach the platform bearer exclusively
through :func:`trw_mcp.state._platform_trust.platform_auth_headers` — the
ONE function permitted to build that header (module docstring of
``_platform_trust.py``).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from trw_mcp.state._platform_trust import platform_auth_headers, platform_contact_enabled, send_policy
from trw_mcp.sync.identity import resolve_sync_client_id

logger = structlog.get_logger(__name__)

#: Streaming chunk size for the PUT body — bounds memory use for large
#: archives (NFR: never read the whole file into memory).
_UPLOAD_CHUNK_SIZE = 1024 * 1024

UploadStatus = Literal[
    "ok",
    "disabled",
    "platform_contact_disabled",
    "presign_failed",
    "upload_failed",
]

ListStatus = Literal["ok", "platform_contact_disabled", "list_failed"]

DownloadStatus = Literal[
    "ok",
    "platform_contact_disabled",
    "presign_failed",
    "download_failed",
]


class BackupListEntry(BaseModel):
    """One remote backup object (PRD-CORE-311 FR06)."""

    key: str
    uploaded_at: str
    size_bytes: int


class BackupListResult(BaseModel):
    """Typed result of :meth:`BackupUploader.list_remote`. Never an exception.

    A missing or malformed ``objects`` field in the backend response is a
    typed ``list_failed`` — never silently reported as "no backups" (an
    empty ``objects: []`` list IS a legitimate "no backups" answer; a
    malformed/absent field is not the same thing and must not collapse into
    it).
    """

    status: ListStatus
    objects: list[BackupListEntry] = Field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class BackupDownloadResult(BaseModel):
    """Typed result of :meth:`BackupUploader.download`. Never an exception."""

    status: DownloadStatus
    key: str | None = None
    path: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class BackupUploadResult(BaseModel):
    """Typed result of a :meth:`BackupUploader.upload` call. Never an exception."""

    status: UploadStatus
    key: str | None = None
    uploaded_at: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True only when the archive was confirmed uploaded."""
        return self.status == "ok"


def _http_status_from_exception(exc: BaseException) -> int | None:
    """Extract an HTTP status code from httpx-style exceptions when present."""
    response = getattr(exc, "response", None)
    raw_status = getattr(response, "status_code", None)
    return int(raw_status) if isinstance(raw_status, int) else None


_QUERY_STRING_RE = re.compile(r"\?[^\s'\"]*")


def _safe_error_message(exc: BaseException) -> str:
    """A log-safe, truncated exception message with any URL query string stripped.

    httpx's ``HTTPStatusError`` embeds the full request URL in ``str(exc)`` —
    for the PUT leg that URL IS the presigned URL, whose query string carries
    the AWS signature. Every query string is stripped unconditionally rather
    than only when a signature parameter is detected, so this stays correct
    even if the presign response shape changes.
    """
    return _QUERY_STRING_RE.sub("", str(exc))[:200]


def _size_and_sha256(path: Path) -> tuple[int, str]:
    """The byte length and hex SHA-256 of *path*, read in chunks (never the whole file at once)."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_UPLOAD_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


async def _aiter_file_chunks(path: Path, chunk_size: int = _UPLOAD_CHUNK_SIZE) -> AsyncIterator[bytes]:
    """Stream *path* in fixed-size chunks without reading it into memory at once.

    Reads happen off the event loop (``asyncio.to_thread``) so a large
    archive's disk I/O does not block other in-flight requests.
    """
    with path.open("rb") as fh:
        while True:
            chunk = await asyncio.to_thread(fh.read, chunk_size)
            if not chunk:
                break
            yield chunk


class BackupUploader:
    """Uploads a local backup archive to S3 via a backend-issued presigned PUT."""

    def __init__(
        self,
        backend_url: str,
        api_key: str,
        client_id: str | None = None,
        *,
        source_trw_dir: Path | None,
        backup_remote_enabled: bool = False,
        timeout: float = 10.0,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._api_key = api_key
        self._client_id = (client_id or "").strip() or resolve_sync_client_id()
        self._backup_remote_enabled = backup_remote_enabled
        self._timeout = timeout
        # The .trw of the store the archive was made from (or a download restores into); its policy governs.
        self._source_trw_dir = source_trw_dir

    async def upload(self, archive_path: Path) -> BackupUploadResult:
        """Upload *archive_path* as it is on disk. Never raises.

        The checksum and length sent to S3 are those of the exact bytes uploaded (the compressed archive),
        computed here: S3 verifies the uploaded object's checksum, and the archive's own ``sha256`` covers
        the decompressed snapshot (sol r1 on core311-s4), so it would never match.

        Fail-closed on ``backup_remote_enabled=False`` (zero HTTP requests);
        fail-closed on the global ``platform_contact_enabled()`` switch,
        checked once before the presign POST and again before the PUT.
        """
        import httpx

        policy = send_policy(self._source_trw_dir)  # the backed-up store's own consent and switch
        if not (self._backup_remote_enabled and policy.backup_remote):
            logger.debug(
                "backup_upload_skipped",
                reason="backup_remote_disabled",
                client_id=self._client_id,
            )
            return BackupUploadResult(status="disabled")

        if not platform_contact_enabled(self._source_trw_dir):
            logger.debug(
                "backup_upload_skipped",
                reason="platform_contact_disabled",
                client_id=self._client_id,
            )
            return BackupUploadResult(status="platform_contact_disabled")

        try:
            size_bytes, sha256 = await asyncio.to_thread(_size_and_sha256, archive_path)
        except OSError as exc:  # justified: boundary, a missing/unreadable archive returns a typed failure
            logger.warning(
                "backup_upload_error",
                stage="stat",
                client_id=self._client_id,
                error_type=type(exc).__name__,
            )
            return BackupUploadResult(status="upload_failed", error="archive_unreadable")

        presign_url = f"{self._backend_url}/v1/backup/presign"
        payload = {"client_id": self._client_id, "sha256": sha256, "size_bytes": size_bytes}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    presign_url,
                    json=payload,
                    # platform_auth_headers is the ONE function that may build
                    # this header — see _platform_trust module docstring.
                    headers=platform_auth_headers(presign_url, self._api_key, source_trw_dir=self._source_trw_dir),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # justified: boundary, presign failures return a typed result, never propagate
            logger.warning(
                "backup_upload_error",
                stage="presign",
                client_id=self._client_id,
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
                status_code=_http_status_from_exception(exc),
            )
            return BackupUploadResult(status="presign_failed", error=type(exc).__name__)

        put_url = data.get("url")
        key = data.get("key")
        if not isinstance(put_url, str) or not put_url:
            logger.warning(
                "backup_upload_error",
                stage="presign",
                client_id=self._client_id,
                reason="missing_presigned_url",
            )
            return BackupUploadResult(status="presign_failed", error="missing_presigned_url")

        # Per-request recheck (push.py's every-batch pattern, B71-106): the
        # switch may have flipped off between the presign and the PUT.
        if not platform_contact_enabled(self._source_trw_dir):
            logger.debug(
                "backup_upload_skipped",
                reason="platform_contact_disabled",
                client_id=self._client_id,
                stage="pre_put",
            )
            return BackupUploadResult(status="platform_contact_disabled")

        checksum_b64 = base64.b64encode(bytes.fromhex(sha256)).decode("ascii")
        # Content-Length is part of the presigned signature (the backend signs ContentLength), so S3 rejects a
        # body of any other size; send the exact length rather than let the client choose chunked encoding.
        put_headers = {"x-amz-checksum-sha256": checksum_b64, "Content-Length": str(size_bytes)}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.put(
                    put_url,
                    content=_aiter_file_chunks(archive_path),
                    headers=put_headers,
                )
                resp.raise_for_status()
        except Exception as exc:  # justified: boundary, PUT failures return a typed result, never propagate
            logger.warning(
                "backup_upload_error",
                stage="put",
                client_id=self._client_id,
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
                status_code=_http_status_from_exception(exc),
            )
            return BackupUploadResult(status="upload_failed", error=type(exc).__name__)

        uploaded_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "backup_upload_complete",
            client_id=self._client_id,
            key=key if isinstance(key, str) else None,
            size_bytes=size_bytes,
        )
        return BackupUploadResult(
            status="ok",
            key=key if isinstance(key, str) else None,
            uploaded_at=uploaded_at,
        )

    async def list_remote(self) -> BackupListResult:
        """List this client's remote backups, newest first. Never raises.

        Only the global ``platform_contact_enabled()`` kill switch gates a
        list call — unlike :meth:`upload`, listing does not send store
        content and does not require ``backup_remote_enabled`` (PRD-CORE-311
        FR06: the restore drill is a deliberate, manual, read-only-of-remote
        operation the operator triggers directly, distinct from the upload
        consent gate).
        """
        import httpx

        if not platform_contact_enabled(self._source_trw_dir):
            logger.debug("backup_list_skipped", reason="platform_contact_disabled", client_id=self._client_id)
            return BackupListResult(status="platform_contact_disabled")

        list_url = f"{self._backend_url}/v1/backup/list"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    list_url,
                    params={"client_id": self._client_id},
                    headers=platform_auth_headers(list_url, self._api_key, source_trw_dir=self._source_trw_dir),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # justified: boundary, list failures return a typed result, never propagate
            logger.warning(
                "backup_list_error",
                client_id=self._client_id,
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
                status_code=_http_status_from_exception(exc),
            )
            return BackupListResult(status="list_failed", error=type(exc).__name__)

        raw_objects = data.get("objects") if isinstance(data, dict) else None
        if not isinstance(raw_objects, list):
            # A missing/malformed `objects` field is NEVER "no backups" — it
            # is a typed failure the caller must not silently swallow into an
            # empty-list "nothing to restore" verdict.
            logger.warning("backup_list_error", client_id=self._client_id, reason="malformed_objects_field")
            return BackupListResult(status="list_failed", error="malformed_response")

        entries: list[BackupListEntry] = []
        for raw in raw_objects:
            if not isinstance(raw, dict):
                continue
            key, uploaded_at, size_bytes = raw.get("key"), raw.get("uploaded_at"), raw.get("size_bytes")
            if not isinstance(key, str) or not isinstance(uploaded_at, str) or not isinstance(size_bytes, int):
                logger.warning("backup_list_error", client_id=self._client_id, reason="malformed_object_entry")
                continue
            entries.append(BackupListEntry(key=key, uploaded_at=uploaded_at, size_bytes=size_bytes))
        return BackupListResult(status="ok", objects=entries)

    async def download(self, key: str, dest_path: Path) -> BackupDownloadResult:
        """Download remote backup *key* to *dest_path* via a presigned GET. Never raises.

        Reuses the same presign endpoint and auth-header boundary as
        :meth:`upload` (``method: "get"`` in the presign body); the GET
        itself streams to disk in fixed-size chunks, matching
        :func:`_aiter_file_chunks`'s never-read-the-whole-file-into-memory
        contract for the upload path.
        """
        import httpx

        if not platform_contact_enabled(self._source_trw_dir):
            logger.debug("backup_download_skipped", reason="platform_contact_disabled", key=key)
            return BackupDownloadResult(status="platform_contact_disabled", key=key)

        presign_url = f"{self._backend_url}/v1/backup/presign"
        payload = {"client_id": self._client_id, "method": "get", "key": key}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    presign_url,
                    json=payload,
                    headers=platform_auth_headers(presign_url, self._api_key, source_trw_dir=self._source_trw_dir),
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # justified: boundary, presign failures return a typed result, never propagate
            logger.warning(
                "backup_download_error",
                stage="presign",
                key=key,
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
                status_code=_http_status_from_exception(exc),
            )
            return BackupDownloadResult(status="presign_failed", key=key, error=type(exc).__name__)

        get_url = data.get("url")
        if not isinstance(get_url, str) or not get_url:
            logger.warning("backup_download_error", stage="presign", key=key, reason="missing_presigned_url")
            return BackupDownloadResult(status="presign_failed", key=key, error="missing_presigned_url")

        # Per-request recheck (the upload path's same pattern, B71-106): the
        # switch may have flipped off between the presign and the GET.
        if not platform_contact_enabled(self._source_trw_dir):
            logger.debug("backup_download_skipped", reason="platform_contact_disabled", key=key, stage="pre_get")
            return BackupDownloadResult(status="platform_contact_disabled", key=key)

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream("GET", get_url) as resp,
            ):
                resp.raise_for_status()
                with tmp_path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(_UPLOAD_CHUNK_SIZE):
                        fh.write(chunk)
            os.replace(tmp_path, dest_path)
        except Exception as exc:  # justified: boundary, download failures return a typed result, never propagate
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            logger.warning(
                "backup_download_error",
                stage="download",
                key=key,
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
                status_code=_http_status_from_exception(exc),
            )
            return BackupDownloadResult(status="download_failed", key=key, error=type(exc).__name__)

        return BackupDownloadResult(status="ok", key=key, path=dest_path)
