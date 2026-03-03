"""Tests for auto_upgrade module — PRD-INFRA-014 Phase 2C."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.auto_upgrade import (
    _compare_versions,
    _fetch_artifact_info,
    _is_compatible,
    check_for_update,
    download_release_artifact,
    get_installed_version,
    perform_upgrade,
)


@pytest.fixture(autouse=True)
def reset_cfg() -> None:
    _reset_config()
    yield  # type: ignore[misc]
    _reset_config()


# ---------------------------------------------------------------------------
# Helper: build a valid .tar.gz in memory with a data/ directory
# ---------------------------------------------------------------------------

def _make_tar_gz_bytes(members: dict[str, bytes] | None = None) -> bytes:
    """Build a tar.gz archive in memory.

    Args:
        members: mapping of archive-path -> file-content.
                 Default: {"data/hello.txt": b"world"}
    """
    if members is None:
        members = {"data/hello.txt": b"world"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _mock_urlopen_for_bytes(data: bytes) -> MagicMock:
    """Return a context-manager-compatible mock response that reads *data*."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ===================================================================
# get_installed_version
# ===================================================================


class TestGetInstalledVersion:
    def test_returns_string(self) -> None:
        version = get_installed_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_import_error_returns_fallback(self) -> None:
        """Lines 30-31: ImportError on `from trw_mcp import __version__`."""
        import sys
        import types

        # Temporarily replace trw_mcp in sys.modules with a module that
        # raises ImportError on attribute access of __version__.
        original = sys.modules["trw_mcp"]
        fake = types.ModuleType("trw_mcp")

        # __getattr__ on a module takes (name,) not (self, name)
        def _raise_import(name: str) -> None:
            raise ImportError(f"no attribute {name}")

        fake.__getattr__ = _raise_import  # type: ignore[attr-defined]
        sys.modules["trw_mcp"] = fake
        try:
            # The function does `from trw_mcp import __version__` which
            # triggers ImportError from our fake module.
            result = get_installed_version()
            assert result == "0.0.0"
        finally:
            sys.modules["trw_mcp"] = original

    def test_attribute_error_returns_fallback(self) -> None:
        """Lines 30-31: AttributeError when __version__ is missing."""
        import sys
        import types

        original = sys.modules["trw_mcp"]
        fake = types.ModuleType("trw_mcp")
        # Module exists but has no __version__
        if hasattr(fake, "__version__"):
            delattr(fake, "__version__")
        sys.modules["trw_mcp"] = fake
        try:
            result = get_installed_version()
            assert result == "0.0.0"
        finally:
            sys.modules["trw_mcp"] = original


# ===================================================================
# _compare_versions
# ===================================================================


class TestCompareVersions:
    def test_newer(self) -> None:
        assert _compare_versions("0.4.0", "0.5.0") is True

    def test_same(self) -> None:
        assert _compare_versions("0.4.0", "0.4.0") is False

    def test_older(self) -> None:
        assert _compare_versions("0.5.0", "0.4.0") is False

    def test_invalid_current(self) -> None:
        assert _compare_versions("abc", "0.4.0") is False

    def test_invalid_latest(self) -> None:
        assert _compare_versions("0.4.0", "xyz") is False

    def test_patch_newer(self) -> None:
        assert _compare_versions("0.4.0", "0.4.1") is True

    def test_major_newer(self) -> None:
        assert _compare_versions("1.0.0", "2.0.0") is True

    def test_extra_parts_ignored(self) -> None:
        """Only first 3 parts compared."""
        assert _compare_versions("1.0.0.0", "1.0.1.0") is True


# ===================================================================
# _is_compatible  (lines 292-297)
# ===================================================================


class TestIsCompatible:
    def test_equal_versions(self) -> None:
        assert _is_compatible("1.0.0", "1.0.0") is True

    def test_current_above_min(self) -> None:
        assert _is_compatible("2.0.0", "1.0.0") is True

    def test_current_below_min(self) -> None:
        assert _is_compatible("0.9.0", "1.0.0") is False

    def test_patch_level_compat(self) -> None:
        assert _is_compatible("1.0.1", "1.0.0") is True

    def test_patch_level_incompat(self) -> None:
        assert _is_compatible("1.0.0", "1.0.1") is False

    def test_invalid_current_returns_true(self) -> None:
        """Fail-open: unparseable versions assume compatible."""
        assert _is_compatible("abc", "1.0.0") is True

    def test_invalid_min_returns_true(self) -> None:
        assert _is_compatible("1.0.0", "abc") is True

    def test_both_invalid_returns_true(self) -> None:
        assert _is_compatible("abc", "xyz") is True


# ===================================================================
# check_for_update
# ===================================================================


class TestCheckForUpdate:
    def test_offline_no_platform_url(self) -> None:
        _reset_config(TRWConfig(platform_url=""))
        result = check_for_update()
        assert result["available"] is False
        assert isinstance(result["current"], str)
        assert result["advisory"] is None

    def test_success(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        mock_resp = _mock_urlopen_for_bytes(json.dumps({"version": "99.0.0"}).encode())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_for_update()
        assert result["available"] is True
        assert result["latest"] == "99.0.0"
        assert result["advisory"] is not None
        assert "99.0.0" in str(result["advisory"])

    def test_network_error(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("unreachable")):
            result = check_for_update()
        assert result["available"] is False
        assert result["advisory"] is None

    def test_non_200_response(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_for_update()
        assert result["available"] is False

    def test_bad_json(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        mock_resp = _mock_urlopen_for_bytes(b"not-json")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_for_update()
        assert result["available"] is False

    def test_same_version(self) -> None:
        current = get_installed_version()
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        mock_resp = _mock_urlopen_for_bytes(json.dumps({"version": current}).encode())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_for_update()
        assert result["available"] is False
        assert result["advisory"] is None

    def test_channel_in_url(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="lts"))
        captured: list[str] = []
        orig_request = __import__("urllib.request", fromlist=["Request"]).Request

        def fake_request(url: str, **kwargs: object) -> object:
            captured.append(url)
            return orig_request(url, **kwargs)

        with patch("urllib.request.Request", side_effect=fake_request):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no")):
                check_for_update()
        assert len(captured) == 1
        assert "channel=lts" in captured[0]

    def test_sends_auth_header(self) -> None:
        _reset_config(TRWConfig(
            platform_url="https://example.com",
            platform_api_key="test-key",
            update_channel="latest",
        ))
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 3) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(json.dumps({"version": "99.0.0"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = check_for_update()
        assert result["available"] is True
        assert captured[0].get_header("Authorization") == "Bearer test-key"

    def test_no_auth_without_key(self) -> None:
        _reset_config(TRWConfig(
            platform_url="https://example.com",
            platform_api_key="",
            update_channel="latest",
        ))
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 3) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(json.dumps({"version": "99.0.0"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            check_for_update()
        assert captured[0].get_header("Authorization") is None

    def test_fallback_urls_tries_next(self) -> None:
        """When first URL fails, tries the next one."""
        _reset_config(TRWConfig(
            platform_url="",
            platform_urls=["https://fail.example.com", "https://ok.example.com"],
            update_channel="latest",
        ))
        call_count = 0

        def fake_urlopen(req: object, timeout: int = 3) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise urllib.error.URLError("first fails")
            return _mock_urlopen_for_bytes(json.dumps({"version": "99.0.0"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = check_for_update()
        assert result["available"] is True
        assert call_count == 2


# ===================================================================
# _fetch_artifact_info  (lines 268-287)
# ===================================================================


class TestFetchArtifactInfo:
    def test_no_urls_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url=""))
        result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_success_returns_dict(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        payload = {"artifact_url": "https://dl.example.com/v1.tar.gz", "artifact_checksum": "abc123"}
        mock_resp = _mock_urlopen_for_bytes(json.dumps(payload).encode())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_artifact_info("1.0.0")
        assert result is not None
        assert result["artifact_url"] == "https://dl.example.com/v1.tar.gz"

    def test_network_error_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_bad_json_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        mock_resp = _mock_urlopen_for_bytes(b"not-json")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_non_200_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_auth_header_sent(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", platform_api_key="my-key"))
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 5) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(json.dumps({"artifact_url": "x"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _fetch_artifact_info("1.0.0")
        assert captured[0].get_header("Authorization") == "Bearer my-key"

    def test_no_auth_header_without_key(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", platform_api_key=""))
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 5) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(json.dumps({"artifact_url": "x"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _fetch_artifact_info("1.0.0")
        assert captured[0].get_header("Authorization") is None

    def test_fallback_to_second_url(self) -> None:
        _reset_config(TRWConfig(
            platform_url="",
            platform_urls=["https://bad.example.com", "https://good.example.com"],
        ))
        call_count = 0

        def fake_urlopen(req: object, timeout: int = 5) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise urllib.error.URLError("fail")
            return _mock_urlopen_for_bytes(json.dumps({"artifact_url": "ok"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = _fetch_artifact_info("1.0.0")
        assert result is not None
        assert result["artifact_url"] == "ok"
        assert call_count == 2

    def test_all_urls_fail_returns_none(self) -> None:
        _reset_config(TRWConfig(
            platform_url="",
            platform_urls=["https://a.example.com", "https://b.example.com"],
        ))
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_http_error_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError(
            "url", 403, "Forbidden", {}, None  # type: ignore[arg-type]
        )):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_oserror_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        with patch("urllib.request.urlopen", side_effect=OSError("connection reset")):
            result = _fetch_artifact_info("1.0.0")
        assert result is None


# ===================================================================
# download_release_artifact  (lines 113-163)
# ===================================================================


class TestDownloadReleaseArtifact:
    def test_success_no_checksum(self, tmp_path: Path) -> None:
        """Happy path: download, extract, return data/ dir."""
        archive_bytes = _make_tar_gz_bytes({"data/hello.txt": b"world"})
        mock_resp = _mock_urlopen_for_bytes(archive_bytes)

        _reset_config(TRWConfig(platform_api_key=""))
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact("https://example.com/release.tar.gz")

        assert result is not None
        assert result.name == "data"
        assert result.is_dir()

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


# ===================================================================
# perform_upgrade  (lines 176-259)
# ===================================================================


class TestPerformUpgrade:
    """Tests for the perform_upgrade function covering all branches."""

    def _setup_config(self, tmp_path: Path, api_key: str = "") -> None:
        """Reset config with a platform URL pointing to a temp project."""
        _reset_config(TRWConfig(
            platform_url="https://example.com",
            platform_api_key=api_key,
            update_channel="latest",
        ))

    def test_lock_contention_returns_not_applied(self, tmp_path: Path) -> None:
        """When flock raises OSError, returns 'Another upgrade is in progress'."""

        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        with patch("os.getcwd", return_value=str(tmp_path)):
            with patch.object(Path, "cwd", return_value=tmp_path):
                # Make flock raise to simulate contention
                with patch("fcntl.flock", side_effect=OSError("locked")):
                    result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Another upgrade" in str(result["details"])

    def test_artifact_info_none_returns_not_applied(self, tmp_path: Path) -> None:
        """When _fetch_artifact_info returns None."""

        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=None):
                result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Could not fetch artifact info" in str(result["details"])

    def test_incompatible_version_returns_not_applied(self, tmp_path: Path) -> None:
        """When current version is below min_compatible_version."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "99.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "below min compatible" in str(result["details"])

    def test_download_failure_returns_not_applied(self, tmp_path: Path) -> None:
        """When download_release_artifact returns None."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=None):
                    result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Download or verification failed" in str(result["details"])

    def test_update_project_errors_returns_not_applied(self, tmp_path: Path) -> None:
        """When update_project returns errors."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch("trw_mcp.bootstrap.update_project", return_value={
                        "errors": ["permission denied"],
                        "updated": [],
                        "created": [],
                    }):
                        result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Update errors" in str(result["details"])
        assert "permission denied" in str(result["details"])

    def test_success_returns_applied(self, tmp_path: Path) -> None:
        """Happy path: download, verify, apply — returns applied=True."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch("trw_mcp.bootstrap.update_project", return_value={
                        "errors": [],
                        "updated": ["a.txt", "b.txt"],
                        "created": ["c.txt"],
                    }):
                        result = perform_upgrade(update_info)

        assert result["applied"] is True
        assert result["version"] == "2.0.0"
        assert "3 files" in str(result["details"])

    def test_unexpected_exception_returns_not_applied(self, tmp_path: Path) -> None:
        """Outer except: any unexpected error returns applied=False."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        with patch.object(Path, "cwd", return_value=tmp_path):
            # Make mkdir raise to trigger the outer except
            with patch.object(Path, "mkdir", side_effect=RuntimeError("unexpected")):
                result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Unexpected error" in str(result["details"])

    def test_lock_cleanup_on_success(self, tmp_path: Path) -> None:
        """Lock file is cleaned up after successful upgrade."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch("trw_mcp.bootstrap.update_project", return_value={
                        "errors": [],
                        "updated": ["a.txt"],
                        "created": [],
                    }):
                        result = perform_upgrade(update_info)

        assert result["applied"] is True
        lock_path = tmp_path / ".trw" / "update.lock"
        assert not lock_path.exists()

    def test_artifact_checksum_forwarded(self, tmp_path: Path) -> None:
        """When artifact_info has a checksum, it's forwarded to download_release_artifact."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": "abc123def456",
            "min_compatible_version": "0.0.0",
        }

        captured_args: list[tuple[object, ...]] = []
        captured_kwargs: list[dict[str, object]] = []

        def fake_download(*args: object, **kwargs: object) -> None:
            captured_args.append(args)
            captured_kwargs.append(kwargs)
            return None

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", side_effect=fake_download):
                    perform_upgrade(update_info)

        assert len(captured_args) == 1
        assert captured_args[0][0] == "https://dl.example.com/v2.tar.gz"
        assert captured_kwargs[0]["expected_checksum"] == "abc123def456"

    def test_lock_unlink_oserror_suppressed(self, tmp_path: Path) -> None:
        """Lines 254-255: OSError during lock_path.unlink is silently suppressed."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        original_unlink = Path.unlink

        def unlink_that_fails(self_path: Path, *args: object, **kwargs: object) -> None:
            if self_path.name == "update.lock":
                raise OSError("permission denied")
            return original_unlink(self_path, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch("trw_mcp.bootstrap.update_project", return_value={
                        "errors": [],
                        "updated": ["a.txt"],
                        "created": [],
                    }):
                        with patch.object(Path, "unlink", unlink_that_fails):
                            result = perform_upgrade(update_info)

        # Should still succeed — the OSError in unlink is suppressed
        assert result["applied"] is True

    def test_no_checksum_passed_as_none(self, tmp_path: Path) -> None:
        """When artifact_checksum is None, expected_checksum is None."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        captured_kwargs: list[dict[str, object]] = []

        def fake_download(*args: object, **kwargs: object) -> None:
            captured_kwargs.append(kwargs)
            return None

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", side_effect=fake_download):
                    perform_upgrade(update_info)

        assert captured_kwargs[0]["expected_checksum"] is None
