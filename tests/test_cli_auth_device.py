"""Tests for CLI auth device flow behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._test_cli_auth_support import _DeviceAuthHandler
from trw_mcp.cli.auth import _post_json, device_auth_login, run_auth_login

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

    def test_disallowed_destination_raises_typed_error(self) -> None:
        """PRD-SEC-021 FR05: refused before any request, via the shared policy error."""
        from trw_mcp._outbound_http import DisallowedDestinationError

        with pytest.raises(DisallowedDestinationError):
            _post_json("http://not-loopback.example/x", {"x": 1})


class TestRunAuthLoginDisallowedDestination:
    """PRD-SEC-021 item (3): the policy refusal must not escape as a traceback.

    Exercises the SERVED CLI entrypoint (``run_auth_login``, called by the
    ``trw-mcp auth login`` subcommand), not just the internal helper.
    """

    def test_clean_error_and_nonzero_exit(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = run_auth_login("http://not-loopback.example/api", tmp_path / "config.yaml")

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out
        assert "Refusing to contact" in captured.err


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
        # Terminal error: polling stopped after the first token request.
        assert _DeviceAuthHandler.poll_count == 1

    def test_expired_token(self, mock_server: str) -> None:
        """expired_token error stops polling and returns None."""
        _DeviceAuthHandler.token_error = "expired_token"
        _DeviceAuthHandler.token_http_code = 400

        with patch("trw_mcp.cli.auth.webbrowser"):
            result = device_auth_login(mock_server, interactive=True)

        assert result is None
        assert _DeviceAuthHandler.poll_count == 1

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

        sleeps: list[float] = []
        with (
            patch("trw_mcp.cli.auth._post_json", side_effect=_mock_post),
            patch("trw_mcp.cli.auth.webbrowser"),
            patch("trw_mcp.cli.auth.time.sleep", side_effect=sleeps.append),
        ):
            result = device_auth_login(mock_server, interactive=False)

        assert result is not None
        assert result["api_key"] == "trw_dk_test123"
        # Server interval is 1s; after one slow_down the next wait is 5s longer.
        assert sleeps == [1, 6]

    def test_network_error_returns_none(self) -> None:
        """Network failure on initial code request returns None."""
        result = device_auth_login("http://127.0.0.1:1", interactive=False)
        assert result is None

    def test_trailing_slash_stripped(self, mock_server: str) -> None:
        """Trailing slash on api_url is stripped."""
        _DeviceAuthHandler.max_pending = 0

        result = device_auth_login(mock_server + "/", interactive=False)
        assert result is not None
