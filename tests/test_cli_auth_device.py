"""Tests for CLI auth device flow behavior."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tests._test_cli_auth_support import _DeviceAuthHandler
from trw_mcp.cli.auth import _post_json, device_auth_login
from ._test_cli_auth_support import _reset_handler, mock_server  # noqa: F401

from ._test_cli_auth_support import _reset_handler, mock_server  # noqa: F401

from ._test_cli_auth_support import _reset_handler, mock_server  # noqa: F401


class TestPostJson:
    def test_success(self, mock_server: str) -> None:
        resp = _post_json(
            f"{mock_server}/v1/auth/device/code",
            {"client_id": "trw-cli"},
        )
        assert resp["device_code"] == "test-device-code"
        assert resp["user_code"] == "WDJB-MJHT"

    def test_network_error(self) -> None:
        from urllib.error import URLError

        with pytest.raises(URLError):
            _post_json("http://127.0.0.1:1/nonexistent", {"x": 1}, timeout=1)


class TestDeviceAuthLogin:
    def test_success_flow(self, mock_server: str) -> None:
        """Happy path: device code -> poll -> success."""
        _DeviceAuthHandler.max_pending = 1

        with patch("trw_mcp.cli.auth.webbrowser") as mock_wb:
            mock_wb.open.return_value = True
            result = device_auth_login(mock_server, interactive=True)

        assert result is not None
        assert result["api_key"] == "trw_dk_test123"
        assert result["user_email"] == "user@example.com"

    def test_non_interactive(self, mock_server: str) -> None:
        """Non-interactive mode returns result without printing."""
        _DeviceAuthHandler.max_pending = 0

        result = device_auth_login(mock_server, interactive=False)
        assert result is not None
        assert result["api_key"] == "trw_dk_test123"

    def test_access_denied(self, mock_server: str) -> None:
        """access_denied error stops polling and returns None."""
        _DeviceAuthHandler.token_error = "access_denied"
        _DeviceAuthHandler.token_http_code = 400

        with patch("trw_mcp.cli.auth.webbrowser"):
            result = device_auth_login(mock_server, interactive=True)

        assert result is None

    def test_expired_token(self, mock_server: str) -> None:
        """expired_token error stops polling and returns None."""
        _DeviceAuthHandler.token_error = "expired_token"
        _DeviceAuthHandler.token_http_code = 400

        with patch("trw_mcp.cli.auth.webbrowser"):
            result = device_auth_login(mock_server, interactive=True)

        assert result is None

    def test_slow_down_increases_interval(self, mock_server: str) -> None:
        """slow_down response permanently increases poll interval by 5s."""
        original_post_json = _post_json
        call_count = 0

        def _mock_post(url: str, payload: dict[str, object], timeout: int = 10) -> dict[str, object]:
            nonlocal call_count
            if "/device/token" in url:
                call_count += 1
                if call_count == 1:
                    import io
                    from urllib.error import HTTPError

                    body = json.dumps({"error": "slow_down"}).encode()
                    raise HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body))
            return original_post_json(url, payload, timeout)

        _DeviceAuthHandler.max_pending = 0
        _DeviceAuthHandler.token_error = ""

        with (
            patch("trw_mcp.cli.auth._post_json", side_effect=_mock_post),
            patch("trw_mcp.cli.auth.webbrowser"),
        ):
            result = device_auth_login(mock_server, interactive=False)

        assert result is not None

    def test_network_error_returns_none(self) -> None:
        """Network failure on initial code request returns None."""
        result = device_auth_login("http://127.0.0.1:1", interactive=False)
        assert result is None

    def test_trailing_slash_stripped(self, mock_server: str) -> None:
        """Trailing slash on api_url is stripped."""
        _DeviceAuthHandler.max_pending = 0

        result = device_auth_login(mock_server + "/", interactive=False)
        assert result is not None
