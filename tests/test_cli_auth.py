"""Tests for trw_mcp.cli.auth — device auth client, org selector, config helpers.

Covers FR07 (device auth), FR08 (org selector), and config operations
(logout, status, save).
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.cli.auth import (
    _format_countdown,
    _post_json,
    _save_api_key,
    _save_config_field,
    device_auth_login,
    device_auth_logout,
    device_auth_status,
    run_auth_login,
    select_organization,
)


# ── Helpers ───────────────────────────────────────────────────────────


class _DeviceAuthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler simulating the device auth endpoints."""

    # Class-level state for test control
    token_response: dict[str, Any] = {}
    token_error: str = ""
    token_http_code: int = 200
    poll_count: int = 0
    max_pending: int = 1  # how many "authorization_pending" before success

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler convention
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len)) if content_len else {}

        if self.path == "/v1/auth/device/code":
            self._handle_device_code(body)
        elif self.path == "/v1/auth/device/token":
            self._handle_device_token(body)
        else:
            self.send_error(404)

    def _handle_device_code(self, body: dict[str, Any]) -> None:
        resp = {
            "device_code": "test-device-code",
            "user_code": "WDJB-MJHT",
            "verification_uri": "https://trwframework.com/device",
            "verification_uri_complete": "https://trwframework.com/device?code=WDJB-MJHT",
            "interval": 1,
            "expires_in": 10,
        }
        self._send_json(200, resp)

    def _handle_device_token(self, body: dict[str, Any]) -> None:
        cls = type(self)
        cls.poll_count += 1

        if cls.token_error:
            self._send_json(
                cls.token_http_code or 400,
                {"error": cls.token_error},
            )
            return

        if cls.poll_count <= cls.max_pending:
            self._send_json(400, {"error": "authorization_pending"})
            return

        resp = cls.token_response or {
            "api_key": "trw_dk_test123",
            "user_email": "user@example.com",
            "organizations": [
                {"id": "org-1", "name": "acme-corp", "slug": "acme-corp"},
            ],
        }
        self._send_json(200, resp)

    def _send_json(self, code: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — override
        pass  # Suppress HTTP server logs during tests


@pytest.fixture()
def _reset_handler():
    """Reset handler class state before each test."""
    _DeviceAuthHandler.token_response = {}
    _DeviceAuthHandler.token_error = ""
    _DeviceAuthHandler.token_http_code = 200
    _DeviceAuthHandler.poll_count = 0
    _DeviceAuthHandler.max_pending = 1
    yield


@pytest.fixture()
def mock_server(_reset_handler):
    """Start a local HTTP server for device auth tests."""
    server = HTTPServer(("127.0.0.1", 0), _DeviceAuthHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    """Create a minimal config file for testing."""
    cfg = tmp_path / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        'installation_id: "test-project"\n'
        'platform_api_key: "trw_dk_existing123"\n'
        "platform_telemetry_enabled: true\n",
        encoding="utf-8",
    )
    return cfg


# ── Tests: _format_countdown ─────────────────────────────────────────


class TestFormatCountdown:
    def test_minutes_and_seconds(self) -> None:
        assert _format_countdown(863) == "14:23"

    def test_zero(self) -> None:
        assert _format_countdown(0) == "0:00"

    def test_negative_clamps(self) -> None:
        assert _format_countdown(-5) == "0:00"

    def test_exact_minute(self) -> None:
        assert _format_countdown(60) == "1:00"

    def test_seconds_only(self) -> None:
        assert _format_countdown(45) == "0:45"


# ── Tests: _post_json ────────────────────────────────────────────────


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


# ── Tests: device_auth_login ─────────────────────────────────────────


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
        # First poll returns slow_down, then pending, then success
        original_post_json = _post_json
        call_count = 0

        def _mock_post(url: str, payload: dict[str, object], timeout: int = 10) -> dict[str, object]:
            nonlocal call_count
            if "/device/token" in url:
                call_count += 1
                if call_count == 1:
                    from urllib.error import HTTPError
                    import io

                    body = json.dumps({"error": "slow_down"}).encode()
                    raise HTTPError(url, 400, "Bad Request", {}, io.BytesIO(body))
            return original_post_json(url, payload, timeout)

        _DeviceAuthHandler.max_pending = 0  # Return success immediately after slow_down
        _DeviceAuthHandler.token_error = ""

        with (
            patch("trw_mcp.cli.auth._post_json", side_effect=_mock_post),
            patch("trw_mcp.cli.auth.webbrowser"),
        ):
            result = device_auth_login(mock_server, interactive=False)

        # Should still succeed after slow_down
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


# ── Tests: select_organization ────────────────────────────────────────


class TestSelectOrganization:
    def test_empty_list(self) -> None:
        result = select_organization([], interactive=True)
        assert result is None

    def test_single_org_auto_selects(self) -> None:
        org = {"id": "org-1", "name": "acme-corp", "slug": "acme-corp"}
        result = select_organization([org], interactive=True)
        assert result == org

    def test_multiple_non_interactive(self) -> None:
        """Non-interactive mode picks first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        result = select_organization(orgs, interactive=False)
        assert result == orgs[0]

    def test_multiple_interactive_default(self) -> None:
        """Interactive mode with empty input picks first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        mock_tty = MagicMock()
        mock_tty.readline.return_value = "\n"

        with patch("trw_mcp.cli.auth._open_tty", return_value=mock_tty):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[0]

    def test_multiple_interactive_choice(self) -> None:
        """Interactive mode with specific choice."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        mock_tty = MagicMock()
        mock_tty.readline.return_value = "2\n"

        with patch("trw_mcp.cli.auth._open_tty", return_value=mock_tty):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[1]

    def test_multiple_interactive_invalid_choice(self) -> None:
        """Invalid choice defaults to first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]
        mock_tty = MagicMock()
        mock_tty.readline.return_value = "99\n"

        with patch("trw_mcp.cli.auth._open_tty", return_value=mock_tty):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[0]

    def test_no_tty_defaults_to_first(self) -> None:
        """No TTY available defaults to first org."""
        orgs = [
            {"id": "org-1", "name": "first"},
            {"id": "org-2", "name": "second"},
        ]

        with patch("trw_mcp.cli.auth._open_tty", return_value=None):
            result = select_organization(orgs, interactive=True)

        assert result == orgs[0]


# ── Tests: device_auth_logout ────────────────────────────────────────


class TestDeviceAuthLogout:
    def test_removes_key(self, config_file: Path) -> None:
        result = device_auth_logout(config_file)
        assert result is True
        content = config_file.read_text(encoding="utf-8")
        assert 'platform_api_key: ""' in content

    def test_no_key_present(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            'installation_id: "test"\nplatform_telemetry_enabled: true\n',
            encoding="utf-8",
        )
        result = device_auth_logout(cfg)
        assert result is False

    def test_no_config_file(self, tmp_path: Path) -> None:
        result = device_auth_logout(tmp_path / "nonexistent" / "config.yaml")
        assert result is False

    def test_empty_key_not_removed(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            'installation_id: "test"\nplatform_api_key: ""\n',
            encoding="utf-8",
        )
        result = device_auth_logout(cfg)
        assert result is False


# ── Tests: device_auth_status ────────────────────────────────────────


class TestDeviceAuthStatus:
    def test_authenticated(self, config_file: Path) -> None:
        status = device_auth_status(config_file, "https://api.example.com")
        assert status["authenticated"] is True
        assert "key_prefix" in status
        assert status["key_prefix"].startswith("trw_dk_exi")

    def test_not_authenticated(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            'installation_id: "test"\n',
            encoding="utf-8",
        )
        status = device_auth_status(cfg, "https://api.example.com")
        assert status["authenticated"] is False

    def test_missing_file(self, tmp_path: Path) -> None:
        status = device_auth_status(
            tmp_path / "nonexistent" / "config.yaml",
            "https://api.example.com",
        )
        assert status["authenticated"] is False

    def test_empty_key(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('platform_api_key: ""\n', encoding="utf-8")
        status = device_auth_status(cfg, "https://api.example.com")
        assert status["authenticated"] is False


# ── Tests: _save_api_key ─────────────────────────────────────────────


class TestSaveApiKey:
    def test_creates_new_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        _save_api_key(cfg, "trw_dk_newkey")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_newkey"' in content

    def test_updates_existing_key(self, config_file: Path) -> None:
        _save_api_key(config_file, "trw_dk_updated")
        content = config_file.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_updated"' in content
        assert "trw_dk_existing123" not in content

    def test_appends_when_no_key_line(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('installation_id: "test"\n', encoding="utf-8")
        _save_api_key(cfg, "trw_dk_appended")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_appended"' in content
        assert 'installation_id: "test"' in content

    def test_no_existing_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _save_api_key(cfg, "trw_dk_brand_new")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_brand_new"' in content


# ── Tests: _save_config_field (P1-2 fix) ──────────────────────────


class TestSaveConfigField:
    def test_creates_field_in_new_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        _save_config_field(cfg, "platform_org_name", "acme-corp")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_org_name: "acme-corp"' in content

    def test_updates_existing_field(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text('platform_org_name: "old-org"\n', encoding="utf-8")
        _save_config_field(cfg, "platform_org_name", "new-org")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_org_name: "new-org"' in content
        assert "old-org" not in content

    def test_appends_when_field_absent(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text('installation_id: "test"\n', encoding="utf-8")
        _save_config_field(cfg, "platform_user_email", "user@example.com")
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_user_email: "user@example.com"' in content
        assert 'installation_id: "test"' in content


# ── Tests: run_auth_login org/email persistence (P1-2 fix) ────────


class TestRunAuthLoginPersistence:
    def test_saves_org_name_and_email(self, tmp_path: Path) -> None:
        """Verify run_auth_login persists org_name and user_email to config."""
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text('platform_api_key: ""\n', encoding="utf-8")

        mock_result: dict[str, object] = {
            "api_key": "trw_dk_test123",
            "org_name": "acme-corp",
            "user_email": "dev@acme.com",
            "org_id": 42,
            "organizations": [{"id": 42, "name": "acme-corp", "slug": "acme-corp"}],
        }

        with patch("trw_mcp.cli.auth.device_auth_login", return_value=mock_result), \
             patch("trw_mcp.cli.auth.select_organization", return_value=mock_result["organizations"][0]):
            exit_code = run_auth_login("https://api.example.com", cfg)

        assert exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        assert 'platform_api_key: "trw_dk_test123"' in content
        assert 'platform_org_name: "acme-corp"' in content
        assert 'platform_user_email: "dev@acme.com"' in content

    def test_status_reads_org_and_email(self, tmp_path: Path) -> None:
        """Verify device_auth_status returns saved org_name and user_email."""
        cfg = tmp_path / ".trw" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(
            'platform_api_key: "trw_dk_test123"\n'
            'platform_org_name: "acme-corp"\n'
            'platform_user_email: "dev@acme.com"\n',
            encoding="utf-8",
        )
        status = device_auth_status(cfg, "https://api.example.com")
        assert status["authenticated"] is True
        assert status["org_name"] == "acme-corp"
        assert status["user_email"] == "dev@acme.com"
