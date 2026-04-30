"""Tests for auto_upgrade artifact download behavior."""

from __future__ import annotations

import hashlib
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._auto_upgrade_test_support import (
    _make_tar_gz_bytes,
    _mock_urlopen_for_bytes,
    reset_cfg,  # noqa: F401
)
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.auto_upgrade import download_release_artifact


class TestDownloadReleaseArtifact:
    def test_no_checksum_returns_none(self, tmp_path: Path) -> None:
        """PRD-QUAL-042-FR08: mandatory checksum — no checksum returns None."""
        archive_bytes = _make_tar_gz_bytes({"data/hello.txt": b"world"})
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_success_with_valid_checksum(self, tmp_path: Path) -> None:
        """Checksum verification passes."""
        archive_bytes = _make_tar_gz_bytes({"data/file.txt": b"content"})
        expected_checksum = hashlib.sha256(archive_bytes).hexdigest()
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
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
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
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
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_absolute_path_blocked(self, tmp_path: Path) -> None:
        """Archive member starting with '/' is rejected."""
        archive_bytes = _make_tar_gz_bytes({"/etc/passwd": b"root:x:0:0"})
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_no_data_dir_returns_none(self, tmp_path: Path) -> None:
        """Archive without data/ directory returns None."""
        archive_bytes = _make_tar_gz_bytes({"other/file.txt": b"stuff"})
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is None

    def test_network_error_returns_none(self) -> None:
        """URLError during download returns None (fail-open)."""
        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            result = download_release_artifact("https://example.com/release.tar.gz")
        assert result is None

    def test_auth_header_sent(self, tmp_path: Path) -> None:
        """platform_api_key produces an Authorization header."""
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        _reset_config(TRWConfig(platform_api_key="secret-key"))
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 30) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(archive_bytes)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact("https://example.com/release.tar.gz")

        assert captured[0].get_header("Authorization") == "Bearer secret-key"

    def test_exception_returns_none(self) -> None:
        """Generic exception returns None (fail-open, outer except)."""
        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", side_effect=RuntimeError("boom")):
            result = download_release_artifact("https://example.com/release.tar.gz")
        assert result is None


class TestDownloadReleaseArtifactEdge:
    """Edge cases for download_release_artifact."""

    def test_path_traversal_after_checksum_passes(self, tmp_path: Path) -> None:
        """Path traversal detected after checksum verification passes."""
        archive_bytes = _make_tar_gz_bytes({"data/../etc/shadow": b"evil"})
        expected_checksum = hashlib.sha256(archive_bytes).hexdigest()
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
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
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
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
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
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
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 30) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(archive_bytes)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact("https://example.com/release.tar.gz")

        assert captured[0].get_header("Authorization") is None
