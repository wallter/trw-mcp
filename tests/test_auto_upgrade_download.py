"""Tests for auto_upgrade artifact download behavior."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from tests._auto_upgrade_test_support import (
    _make_tar_gz_bytes,
    _mock_httpx_client,
    _mock_httpx_response,
    reset_cfg,  # noqa: F401
)
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.auto_upgrade import download_release_artifact


def _client_for_archive(archive_bytes: bytes) -> MagicMock:
    """Build a mock httpx.Client whose get() returns a response with *archive_bytes*."""
    resp = _mock_httpx_response(content=archive_bytes)
    return _mock_httpx_client(resp)


class TestDownloadReleaseArtifact:
    def test_no_checksum_returns_none(self, tmp_path: Path) -> None:
        """PRD-QUAL-042-FR08: mandatory checksum — no checksum returns None."""
        archive_bytes = _make_tar_gz_bytes({"data/hello.txt": b"world"})
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_success_with_valid_checksum(self, tmp_path: Path) -> None:
        """Checksum verification passes."""
        archive_bytes = _make_tar_gz_bytes({"data/file.txt": b"content"})
        expected_checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact(
                    "https://example.com/release.tar.gz",
                    expected_checksum=expected_checksum,
                )

        assert result is not None
        assert isinstance(result, Path)
        assert result.is_dir()

    def test_checksum_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Checksum mismatch returns None."""
        archive_bytes = _make_tar_gz_bytes({"data/file.txt": b"content"})
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact(
                    "https://example.com/release.tar.gz",
                    expected_checksum="0000000000000000000000000000000000000000000000000000000000000000",
                )

        assert result is None

    def test_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Archive member with '..' is rejected."""
        archive_bytes = _make_tar_gz_bytes({"../etc/passwd": b"root:x:0:0"})
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_absolute_path_blocked(self, tmp_path: Path) -> None:
        """Archive member starting with '/' is rejected."""
        archive_bytes = _make_tar_gz_bytes({"/etc/passwd": b"root:x:0:0"})
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_no_data_dir_returns_none(self, tmp_path: Path) -> None:
        """Archive without data/ directory returns None."""
        archive_bytes = _make_tar_gz_bytes({"other/file.txt": b"stuff"})
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_network_error_returns_none(self) -> None:
        """RequestError during download returns None (fail-open)."""
        _reset_config(TRWConfig(platform_api_key=""))
        client = MagicMock()
        client.get.side_effect = httpx.RequestError("offline")
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        with patch("httpx.Client", return_value=client):
            result = download_release_artifact("https://example.com/release.tar.gz")
        assert result is None

    def test_auth_header_sent(self, tmp_path: Path) -> None:
        """platform_api_key produces an Authorization header ONLY when the
        artifact host matches the configured platform host over https
        (sweep-4 credential-egress guard)."""
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        _reset_config(
            TRWConfig(platform_url="https://example.com", platform_api_key="secret-key")
        )
        client = _client_for_archive(archive_bytes)

        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact("https://example.com/release.tar.gz")

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer secret-key"

    def test_exception_returns_none(self) -> None:
        """Generic exception returns None (fail-open, outer except)."""
        _reset_config(TRWConfig(platform_api_key=""))
        client = MagicMock()
        client.get.side_effect = RuntimeError("boom")
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        with patch("httpx.Client", return_value=client):
            result = download_release_artifact("https://example.com/release.tar.gz")
        assert result is None


class TestDownloadReleaseArtifactEdge:
    """Edge cases for download_release_artifact."""

    def test_path_traversal_after_checksum_passes(self, tmp_path: Path) -> None:
        """Path traversal detected after checksum verification passes."""
        archive_bytes = _make_tar_gz_bytes({"data/../etc/shadow": b"evil"})
        expected_checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact(
                    "https://example.com/release.tar.gz",
                    expected_checksum=expected_checksum,
                )

        assert result is None

    def test_absolute_path_after_checksum_passes(self, tmp_path: Path) -> None:
        """Absolute path detected after checksum verification passes."""
        archive_bytes = _make_tar_gz_bytes({"/etc/passwd": b"root"})
        expected_checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact(
                    "https://example.com/release.tar.gz",
                    expected_checksum=expected_checksum,
                )

        assert result is None

    def test_no_data_dir_after_checksum_passes(self, tmp_path: Path) -> None:
        """Archive passes checksum but has no data/ directory."""
        archive_bytes = _make_tar_gz_bytes({"other/readme.txt": b"hello"})
        expected_checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = _client_for_archive(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact(
                    "https://example.com/release.tar.gz",
                    expected_checksum=expected_checksum,
                )

        assert result is None

    def test_no_auth_header_when_key_empty(self, tmp_path: Path) -> None:
        """When platform_api_key is empty, no Authorization header is sent."""
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        _reset_config(TRWConfig(platform_api_key=""))
        client = _client_for_archive(archive_bytes)

        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact("https://example.com/release.tar.gz")

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers
