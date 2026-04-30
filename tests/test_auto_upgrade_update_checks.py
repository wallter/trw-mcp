"""Tests for auto_upgrade update checks and artifact lookup."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from tests._auto_upgrade_test_support import _mock_urlopen_for_bytes, reset_cfg  # noqa: F401
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.auto_upgrade import (
    _fetch_artifact_info,
    check_for_update,
    get_installed_version,
)


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
        _reset_config(
            TRWConfig(
                platform_url="https://example.com",
                platform_api_key="test-key",
                update_channel="latest",
            )
        )
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 3) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(json.dumps({"version": "99.0.0"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = check_for_update()
        assert result["available"] is True
        assert captured[0].get_header("Authorization") == "Bearer test-key"

    def test_no_auth_without_key(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="https://example.com",
                platform_api_key="",
                update_channel="latest",
            )
        )
        captured: list[object] = []

        def fake_urlopen(req: object, timeout: int = 3) -> MagicMock:
            captured.append(req)
            return _mock_urlopen_for_bytes(json.dumps({"version": "99.0.0"}).encode())

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            check_for_update()
        assert captured[0].get_header("Authorization") is None

    def test_fallback_urls_tries_next(self) -> None:
        """When first URL fails, tries the next one."""
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://fail.example.com", "https://ok.example.com"],
                update_channel="latest",
            )
        )
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

    def test_all_urls_fail_returns_fallback(self) -> None:
        """When all platform_urls fail, returns not-available with current as latest."""
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://a.example.com", "https://b.example.com"],
                update_channel="latest",
            )
        )
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            result = check_for_update()
        assert result["available"] is False
        assert result["latest"] == result["current"]
        assert result["advisory"] is None


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
        assert isinstance(result, dict)
        assert result["artifact_url"] == "https://dl.example.com/v1.tar.gz"
        assert result["artifact_checksum"] == "abc123"

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
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://bad.example.com", "https://good.example.com"],
            )
        )
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
        assert isinstance(result, dict)
        assert result["artifact_url"] == "ok"
        assert call_count == 2

    def test_all_urls_fail_returns_none(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://a.example.com", "https://b.example.com"],
            )
        )
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_http_error_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url",
                403,
                "Forbidden",
                {},
                None,  # type: ignore[arg-type]
            ),
        ):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_oserror_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        with patch("urllib.request.urlopen", side_effect=OSError("connection reset")):
            result = _fetch_artifact_info("1.0.0")
        assert result is None


class TestCheckForUpdateEdge:
    """Edge cases for check_for_update."""

    def test_response_missing_version_key(self) -> None:
        """When JSON has no 'version' key, uses current as latest → not available."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        mock_resp = _mock_urlopen_for_bytes(json.dumps({"notes": "no version field"}).encode())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = check_for_update()
        assert result["available"] is False
        assert result["latest"] == result["current"]

    def test_http_error_is_caught(self) -> None:
        """HTTPError (not just URLError) is caught — fail-open."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url",
                500,
                "Internal Server Error",
                {},
                None,  # type: ignore[arg-type]
            ),
        ):
            result = check_for_update()
        assert result["available"] is False

    def test_oserror_is_caught(self) -> None:
        """OSError during connection is caught — fail-open."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            result = check_for_update()
        assert result["available"] is False

    def test_key_error_in_response_is_caught(self) -> None:
        """KeyError during response parsing is caught — fail-open."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        mock_resp = _mock_urlopen_for_bytes(json.dumps({"version": "99.0.0"}).encode())
        with patch("urllib.request.urlopen", return_value=mock_resp):
            with patch("json.loads", side_effect=KeyError("bad key")):
                result = check_for_update()
        assert result["available"] is False

    def test_trailing_slash_stripped_from_url(self) -> None:
        """Platform URL with trailing slash does not double-slash in the request."""
        _reset_config(TRWConfig(platform_url="https://example.com/", update_channel="latest"))
        captured: list[str] = []
        orig_request = __import__("urllib.request", fromlist=["Request"]).Request

        def fake_request(url: str, **kwargs: object) -> object:
            captured.append(url)
            return orig_request(url, **kwargs)

        with patch("urllib.request.Request", side_effect=fake_request):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no")):
                check_for_update()
        assert "//" not in captured[0].split("://")[1]


class TestFetchArtifactInfoEdge:
    """Edge cases for _fetch_artifact_info."""

    def test_version_embedded_in_url(self) -> None:
        """Verify the version string appears in the constructed URL."""
        _reset_config(TRWConfig(platform_url="https://example.com"))
        captured: list[str] = []
        orig_request = __import__("urllib.request", fromlist=["Request"]).Request

        def fake_request(url: str, **kwargs: object) -> object:
            captured.append(url)
            return orig_request(url, **kwargs)

        with patch("urllib.request.Request", side_effect=fake_request):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no")):
                _fetch_artifact_info("3.2.1")
        assert len(captured) == 1
        assert "/v1/releases/3.2.1/artifact" in captured[0]

    def test_json_decode_error_returns_none(self) -> None:
        """JSONDecodeError specifically is caught and returns None."""
        _reset_config(TRWConfig(platform_url="https://example.com"))
        mock_resp = _mock_urlopen_for_bytes(b"{invalid json")
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_trailing_slash_stripped(self) -> None:
        """Platform URL trailing slash does not cause double-slash."""
        _reset_config(TRWConfig(platform_url="https://example.com/"))
        captured: list[str] = []
        orig_request = __import__("urllib.request", fromlist=["Request"]).Request

        def fake_request(url: str, **kwargs: object) -> object:
            captured.append(url)
            return orig_request(url, **kwargs)

        with patch("urllib.request.Request", side_effect=fake_request):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no")):
                _fetch_artifact_info("1.0.0")
        assert "//" not in captured[0].split("://")[1]
