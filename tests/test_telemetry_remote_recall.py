"""Tests for trw_mcp.telemetry.remote_recall — PRD-CORE-033."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from tests._auto_upgrade_test_support import _mock_httpx_client, _mock_httpx_response
from trw_mcp.telemetry.remote_recall import fetch_shared_learnings


def _client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.get.side_effect = exc
    client.post.side_effect = exc
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client


# ===========================================================================
# Offline / config guard
# ===========================================================================


class TestRemoteRecallOffline:
    def test_remote_recall_offline_no_url(self) -> None:
        """Returns empty list when platform_url is empty."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(platform_url="", platform_telemetry_enabled=True)
        with patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg):
            result = fetch_shared_learnings("some query")

        assert result == []

    def test_remote_recall_offline_telemetry_disabled(self) -> None:
        """Returns empty list when platform_telemetry_enabled=False."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=False,
        )
        with patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg):
            result = fetch_shared_learnings("some query")

        assert result == []


# ===========================================================================
# Successful recall
# ===========================================================================


class TestRemoteRecallSuccess:
    def test_remote_recall_success(self) -> None:
        """Returns results with [shared] label prefix."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        backend_response = {
            "results": [
                {"summary": "Use async for I/O bound tasks", "impact": 0.8},
                {"summary": "Avoid global state in modules", "impact": 0.75},
            ]
        }
        client = _mock_httpx_client(_mock_httpx_response(json_data=backend_response))

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", return_value=[0.1, 0.2, 0.3]),
            patch("httpx.Client", return_value=client),
        ):
            result = fetch_shared_learnings("async patterns")

        assert len(result) == 2
        assert result[0]["summary"] == "[shared] Use async for I/O bound tasks"
        assert result[1]["summary"] == "[shared] Avoid global state in modules"

    def test_remote_recall_label_prefix_on_each_result(self) -> None:
        """Every result in the list gets the [shared] label."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        backend_response = {
            "results": [
                {"summary": "Learning one"},
                {"summary": "Learning two"},
                {"summary": "Learning three"},
            ]
        }
        client = _mock_httpx_client(_mock_httpx_response(json_data=backend_response))

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", return_value=None),
            patch("httpx.Client", return_value=client),
        ):
            result = fetch_shared_learnings("query")

        for item in result:
            assert item["summary"].startswith("[shared] ")

    def test_remote_recall_empty_results_list(self) -> None:
        """Empty results array from backend returns empty list."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        backend_response: dict[str, object] = {"results": []}
        client = _mock_httpx_client(_mock_httpx_response(json_data=backend_response))

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", return_value=None),
            patch("httpx.Client", return_value=client),
        ):
            result = fetch_shared_learnings("query")

        assert result == []


# ===========================================================================
# Timeout / network failure
# ===========================================================================


class TestRemoteRecallTimeout:
    def test_remote_recall_timeout(self) -> None:
        """RequestError returns empty list (fail-open)."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", return_value=None),
            patch("httpx.Client", return_value=_client_raising(httpx.RequestError("timed out"))),
        ):
            result = fetch_shared_learnings("query")

        assert result == []

    def test_remote_recall_http_error(self) -> None:
        """Non-2xx status returns empty list (fail-open)."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        client = _mock_httpx_client(_mock_httpx_response(status_code=503))

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", return_value=None),
            patch("httpx.Client", return_value=client),
        ):
            result = fetch_shared_learnings("query")

        assert result == []

    def test_remote_recall_os_error(self) -> None:
        """OSError returns empty list (fail-open)."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", return_value=None),
            patch("httpx.Client", return_value=_client_raising(OSError("network unreachable"))),
        ):
            result = fetch_shared_learnings("query")

        assert result == []

    def test_remote_recall_json_decode_error(self) -> None:
        """Invalid JSON response returns empty list (fail-open)."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        resp = _mock_httpx_response()
        resp.json.side_effect = json.JSONDecodeError("bad", "", 0)
        client = _mock_httpx_client(resp)

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", return_value=None),
            patch("httpx.Client", return_value=client),
        ):
            result = fetch_shared_learnings("query")

        assert result == []


# ===========================================================================
# Empty query
# ===========================================================================


class TestRemoteRecallEmptyQuery:
    def test_remote_recall_empty_query_skips_embed(self) -> None:
        """Empty query string does not call embed (no embedding generated)."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        backend_response = {"results": [{"summary": "Some shared learning"}]}
        client = _mock_httpx_client(_mock_httpx_response(json_data=backend_response))

        embed_mock = MagicMock(return_value=None)

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", embed_mock),
            patch("httpx.Client", return_value=client),
        ):
            result = fetch_shared_learnings("")

        # embed should NOT be called for empty query
        embed_mock.assert_not_called()
        assert len(result) == 1
        assert result[0]["summary"] == "[shared] Some shared learning"

    def test_remote_recall_whitespace_query_skips_embed(self) -> None:
        """Whitespace-only query does not call embed."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        backend_response: dict[str, object] = {"results": []}
        client = _mock_httpx_client(_mock_httpx_response(json_data=backend_response))

        embed_mock = MagicMock(return_value=None)

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", embed_mock),
            patch("httpx.Client", return_value=client),
        ):
            result = fetch_shared_learnings("   ")

        embed_mock.assert_not_called()
        assert result == []

    def test_remote_recall_query_with_content_calls_embed(self) -> None:
        """Non-empty query calls embed to generate embedding."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )

        backend_response: dict[str, object] = {"results": []}
        client = _mock_httpx_client(_mock_httpx_response(json_data=backend_response))

        fake_embedding = [0.1] * 384
        embed_mock = MagicMock(return_value=fake_embedding)

        with (
            patch("trw_mcp.telemetry.remote_recall.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.remote_recall.embed", embed_mock),
            patch("httpx.Client", return_value=client),
        ):
            fetch_shared_learnings("testing patterns")

        embed_mock.assert_called_once_with("testing patterns")
