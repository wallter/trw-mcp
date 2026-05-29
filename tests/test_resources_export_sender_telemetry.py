from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from tests._auto_upgrade_test_support import _mock_httpx_client, _mock_httpx_response
from tests._resources_export_sender_support import _make_sender, _write_events


class TestHttpPost:
    """Lines 127-143 — _http_post httpx branches."""

    def test_http_post_returns_true_on_2xx(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path)

        client = _mock_httpx_client(_mock_httpx_response(status_code=200))

        with patch("httpx.Client", return_value=client):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is True

    def test_http_post_returns_false_on_3xx(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path)

        client = _mock_httpx_client(_mock_httpx_response(status_code=301))

        with patch("httpx.Client", return_value=client):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_returns_false_on_url_error(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path)

        client = MagicMock()
        client.post.side_effect = httpx.RequestError("connection refused")
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch("httpx.Client", return_value=client):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_returns_false_on_http_error(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path)

        client = _mock_httpx_client(_mock_httpx_response(status_code=500))

        with patch("httpx.Client", return_value=client):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_returns_false_on_os_error(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path)

        client = MagicMock()
        client.post.side_effect = OSError("network unreachable")
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch("httpx.Client", return_value=client):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_sends_json_body(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path)

        client = _mock_httpx_client(_mock_httpx_response(status_code=200))

        with patch("httpx.Client", return_value=client):
            sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"tool": "trw_learn", "duration_ms": 42}],
            )

        assert client.post.call_count == 1
        body = client.post.call_args.kwargs.get("json")
        assert isinstance(body, dict)
        assert "events" in body
        assert body["events"][0]["tool"] == "trw_learn"

    def test_http_post_integrated_with_send(self, tmp_path: Path) -> None:
        sender, input_path = _make_sender(tmp_path)
        _write_events(input_path, [{"event_type": "tool_invocation"}])

        client = _mock_httpx_client(_mock_httpx_response(status_code=201))

        with patch("httpx.Client", return_value=client):
            result = sender.send()

        assert result["sent"] == 1
        assert result["failed"] == 0

    def test_http_post_url_construction(self, tmp_path: Path) -> None:
        sender, _ = _make_sender(tmp_path)

        client = _mock_httpx_client(_mock_httpx_response(status_code=200))

        with patch("httpx.Client", return_value=client):
            sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        call = client.post.call_args
        assert call.args[0] == "https://api.example.com/v1/telemetry"
        headers = call.kwargs.get("headers") or {}
        assert headers.get("Content-Type") == "application/json"
