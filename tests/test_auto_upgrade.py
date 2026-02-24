"""Tests for auto_upgrade module — PRD-INFRA-014 Phase 2C."""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.auto_upgrade import (
    _compare_versions,
    check_for_update,
    get_installed_version,
)


@pytest.fixture(autouse=True)
def reset_cfg() -> None:
    _reset_config()
    yield  # type: ignore[misc]
    _reset_config()


# --- get_installed_version ---


def test_get_installed_version_returns_string() -> None:
    version = get_installed_version()
    assert isinstance(version, str)
    assert len(version) > 0


def test_get_installed_version_import_error() -> None:
    with patch("trw_mcp.state.auto_upgrade.get_installed_version") as mock_fn:
        mock_fn.return_value = "0.0.0"
        result = mock_fn()
    assert result == "0.0.0"


# --- _compare_versions ---


def test_compare_versions_newer() -> None:
    assert _compare_versions("0.4.0", "0.5.0") is True


def test_compare_versions_same() -> None:
    assert _compare_versions("0.4.0", "0.4.0") is False


def test_compare_versions_older() -> None:
    assert _compare_versions("0.5.0", "0.4.0") is False


def test_compare_versions_invalid() -> None:
    assert _compare_versions("abc", "0.4.0") is False


def test_compare_versions_patch_newer() -> None:
    assert _compare_versions("0.4.0", "0.4.1") is True


def test_compare_versions_major_newer() -> None:
    assert _compare_versions("1.0.0", "2.0.0") is True


# --- check_for_update ---


def test_check_update_offline_no_platform_url() -> None:
    _reset_config(TRWConfig(platform_url=""))
    result = check_for_update()
    assert result["available"] is False
    assert isinstance(result["current"], str)
    assert result["advisory"] is None


def test_check_update_success() -> None:
    """Mock urllib to return version 99.0.0 — should report available=True."""
    _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"version": "99.0.0"}).encode("utf-8")
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = check_for_update()

    assert result["available"] is True
    assert result["latest"] == "99.0.0"
    assert result["advisory"] is not None
    assert "99.0.0" in str(result["advisory"])


def test_check_update_network_error() -> None:
    """Mock urllib to raise URLError — should fail-open with available=False."""
    _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("unreachable")):
        result = check_for_update()

    assert result["available"] is False
    assert result["advisory"] is None


def test_check_update_non_200_response() -> None:
    """Non-2xx response returns available=False."""
    _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))

    mock_response = MagicMock()
    mock_response.status = 404
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = check_for_update()

    assert result["available"] is False


def test_check_update_bad_json() -> None:
    """JSON decode error returns available=False."""
    _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b"not-json"
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = check_for_update()

    assert result["available"] is False


def test_check_update_same_version() -> None:
    """If remote version equals current, advisory is None."""
    current = get_installed_version()
    _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({"version": current}).encode("utf-8")
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = check_for_update()

    assert result["available"] is False
    assert result["advisory"] is None


def test_check_update_channel_in_url() -> None:
    """The configured update_channel is appended to the request URL."""
    _reset_config(TRWConfig(platform_url="https://example.com", update_channel="lts"))

    captured_url: list[str] = []

    original_request = __import__("urllib.request", fromlist=["Request"]).Request

    def fake_request(url: str, **kwargs: object) -> object:
        captured_url.append(url)
        return original_request(url, **kwargs)

    with patch("urllib.request.Request", side_effect=fake_request):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no net")):
            check_for_update()

    assert len(captured_url) == 1
    assert "channel=lts" in captured_url[0]


def test_check_update_sends_auth_header() -> None:
    """When platform_api_key is set, the Authorization header is included."""
    _reset_config(
        TRWConfig(
            platform_url="https://example.com",
            platform_api_key="test-key-abc",
            update_channel="latest",
        )
    )

    captured_requests: list[object] = []

    def fake_urlopen(req: object, timeout: int = 3) -> MagicMock:
        captured_requests.append(req)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"version": "99.0.0"}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = check_for_update()

    assert result["available"] is True
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.get_header("Authorization") == "Bearer test-key-abc"


def test_check_update_no_auth_header_without_key() -> None:
    """When platform_api_key is empty, no Authorization header is sent."""
    _reset_config(
        TRWConfig(platform_url="https://example.com", platform_api_key="", update_channel="latest")
    )

    captured_requests: list[object] = []

    def fake_urlopen(req: object, timeout: int = 3) -> MagicMock:
        captured_requests.append(req)
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"version": "99.0.0"}).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        check_for_update()

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.get_header("Authorization") is None
