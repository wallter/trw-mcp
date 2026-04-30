"""Shared support for split CLI auth tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest


class _DeviceAuthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler simulating the device auth endpoints."""

    token_response: dict[str, Any] = {}
    token_error: str = ""
    token_http_code: int = 200
    poll_count: int = 0
    max_pending: int = 1

    def do_POST(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_len)) if content_len else {}

        if self.path == "/v1/auth/device/code":
            self._handle_device_code(body)
        elif self.path == "/v1/auth/device/token":
            self._handle_device_token(body)
        else:
            self.send_error(404)

    def _handle_device_code(self, body: dict[str, Any]) -> None:
        del body
        self._send_json(
            200,
            {
                "device_code": "test-device-code",
                "user_code": "WDJB-MJHT",
                "verification_uri": "https://trwframework.com/device",
                "verification_uri_complete": "https://trwframework.com/device?code=WDJB-MJHT",
                "interval": 1,
                "expires_in": 10,
            },
        )

    def _handle_device_token(self, body: dict[str, Any]) -> None:
        del body
        cls = type(self)
        cls.poll_count += 1

        if cls.token_error:
            self._send_json(cls.token_http_code or 400, {"error": cls.token_error})
            return

        if cls.poll_count <= cls.max_pending:
            self._send_json(400, {"error": "authorization_pending"})
            return

        self._send_json(
            200,
            cls.token_response
            or {
                "api_key": "trw_dk_test123",
                "user_email": "user@example.com",
                "organizations": [
                    {"id": "org-1", "name": "acme-corp", "slug": "acme-corp"},
                ],
            },
        )

    def _send_json(self, code: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


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
        'installation_id: "test-project"\nplatform_api_key: "trw_dk_existing123"\nplatform_telemetry_enabled: true\n',
        encoding="utf-8",
    )
    return cfg
