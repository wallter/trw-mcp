"""Tests for auto_upgrade credential-egress host validation (sweep-4 security).

The platform bearer API key must NEVER be attached to a network request whose
target host is not the configured, trusted platform host. These tests assert
BEHAVIOR (was the Authorization header attached / suppressed) for:

  (a) version-check (check_for_update) to an http:// non-localhost host
  (b) artifact download whose artifact_url host != platform host (e.g. a
      poisoned URL OR a legitimate presigned-S3 URL — both must NOT receive
      the platform bearer)
  (c) the legitimate https same-host artifact download (bearer attached)
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import structlog

from tests._auto_upgrade_test_support import (
    _make_tar_gz_bytes,
    _mock_httpx_client,
    _mock_httpx_response,
    reset_cfg,  # noqa: F401
)
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state import auto_upgrade
from trw_mcp.state.auto_upgrade import (
    _bearer_allowed_for,
    check_for_update,
    download_release_artifact,
)


# ---------------------------------------------------------------------------
# _bearer_allowed_for — the host/scheme floor
# ---------------------------------------------------------------------------


class TestBearerAllowedFor:
    def test_https_same_host_allowed(self) -> None:
        assert _bearer_allowed_for(
            "https://api.trwframework.com/v1/releases/x",
            platform_host="api.trwframework.com",
        )

    def test_https_different_host_refused(self) -> None:
        # presigned S3 URL host differs from the platform host
        assert not _bearer_allowed_for(
            "https://trw-releases.s3.amazonaws.com/x?X-Amz-Signature=abc",
            platform_host="api.trwframework.com",
        )

    def test_http_non_localhost_refused(self) -> None:
        assert not _bearer_allowed_for(
            "http://attacker.host/v1/releases/latest",
            platform_host="attacker.host",
        )

    def test_http_localhost_allowed_for_dev(self) -> None:
        assert _bearer_allowed_for(
            "http://127.0.0.1:8100/v1/releases/latest",
            platform_host="127.0.0.1",
        )
        assert _bearer_allowed_for(
            "http://localhost:8100/v1/releases/latest",
            platform_host="localhost",
        )

    def test_no_platform_host_requires_host_match_disabled(self) -> None:
        # When platform_host is None we only enforce the scheme floor (used by
        # the version-check path where the target IS the platform host).
        assert _bearer_allowed_for("https://api.trwframework.com/x", platform_host=None)
        assert not _bearer_allowed_for("http://attacker.host/x", platform_host=None)

    def test_case_insensitive_host_match(self) -> None:
        assert _bearer_allowed_for(
            "https://API.TRWframework.com/x",
            platform_host="api.trwframework.com",
        )


# ---------------------------------------------------------------------------
# (a) version-check to an http:// non-localhost host → no Authorization
# ---------------------------------------------------------------------------


class TestCheckForUpdateEgress:
    def test_http_non_localhost_withholds_bearer(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="http://attacker.host",
                platform_api_key="secret-key",
            )
        )
        resp = _mock_httpx_response(json_data={"version": "9.9.9"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            with structlog.testing.capture_logs() as logs:
                check_for_update()

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers
        assert any(e.get("event") == "credential_withheld_untrusted_host" for e in logs)

    def test_https_host_attaches_bearer(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        resp = _mock_httpx_response(json_data={"version": "9.9.9"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            check_for_update()

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer secret-key"

    def test_http_localhost_attaches_bearer(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="http://127.0.0.1:8100",
                platform_api_key="secret-key",
            )
        )
        resp = _mock_httpx_response(json_data={"version": "9.9.9"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            check_for_update()

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer secret-key"


# ---------------------------------------------------------------------------
# (b) artifact_url host != platform host → bearer NOT attached
# (c) legit https same-host → bearer attached + works
# ---------------------------------------------------------------------------


class TestDownloadArtifactEgress:
    def _archive_client(self, archive_bytes: bytes):  # type: ignore[no-untyped-def]
        resp = _mock_httpx_response(content=archive_bytes)
        return _mock_httpx_client(resp)

    def test_presigned_s3_host_withholds_bearer(self, tmp_path: Path) -> None:
        """A presigned S3 URL is a DIFFERENT host carrying its own query-string
        auth — the platform bearer must NOT be attached."""
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                with structlog.testing.capture_logs() as logs:
                    result = download_release_artifact(
                        "https://trw-releases.s3.amazonaws.com/v1/release.tar.gz?X-Amz-Signature=abc",
                        expected_checksum=checksum,
                    )

        assert result is not None  # download still succeeds (S3 auth is in the URL)
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers
        assert any(e.get("event") == "credential_withheld_untrusted_host" for e in logs)

    def test_attacker_host_withholds_bearer(self, tmp_path: Path) -> None:
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact(
                    "https://attacker.host/release.tar.gz",
                    expected_checksum=checksum,
                )

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers

    def test_same_host_https_attaches_bearer(self, tmp_path: Path) -> None:
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact(
                    "https://api.trwframework.com/v1/release.tar.gz",
                    expected_checksum=checksum,
                )

        assert result is not None
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer secret-key"

    def test_no_platform_urls_configured_withholds_bearer(self, tmp_path: Path) -> None:
        """If we cannot determine the trusted platform host, never attach the
        bearer to an artifact download."""
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(TRWConfig(platform_url="", platform_api_key="secret-key"))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact(
                    "https://api.trwframework.com/v1/release.tar.gz",
                    expected_checksum=checksum,
                )

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers


def test_module_exposes_guard() -> None:
    """Wiring: the guard helper is importable from the module facade."""
    assert hasattr(auto_upgrade, "_bearer_allowed_for")
