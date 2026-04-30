"""_post_learning unit tests for trw_mcp.telemetry.publisher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from tests._auto_upgrade_test_support import _mock_httpx_client, _mock_httpx_response
from trw_mcp.telemetry.publisher import _post_learning


class TestPostLearning:
    def test_post_learning_success(self) -> None:
        """_post_learning returns True on 2xx response."""
        client = _mock_httpx_client(_mock_httpx_response(status_code=200))

        with patch("httpx.Client", return_value=client):
            result = _post_learning("https://api.example.com", {"summary": "test"})

        assert result is True

    def test_post_learning_url_error(self) -> None:
        """_post_learning returns False on httpx.RequestError."""
        client = MagicMock()
        client.post.side_effect = httpx.RequestError("connection refused")
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        with patch("httpx.Client", return_value=client):
            result = _post_learning("https://api.example.com", {"summary": "test"})

        assert result is False

    def test_post_learning_url_construction(self) -> None:
        """URL is constructed as {platform_url}/v1/learnings."""
        client = _mock_httpx_client(_mock_httpx_response(status_code=201))

        with patch("httpx.Client", return_value=client):
            _post_learning("https://api.example.com/", {"summary": "test"})

        assert client.post.call_args.args[0] == "https://api.example.com/v1/learnings"

    def test_post_learning_4xx_returns_false(self) -> None:
        """_post_learning returns False on 4xx HTTP responses."""
        client = _mock_httpx_client(
            _mock_httpx_response(status_code=422, text="bad payload", reason_phrase="Unprocessable Entity")
        )

        with patch("httpx.Client", return_value=client):
            result = _post_learning("https://api.example.com", {"summary": "test"})

        assert result is False
