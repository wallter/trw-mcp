"""Tests for auto_upgrade update checks and artifact lookup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from tests._auto_upgrade_test_support import (
    _mock_httpx_client,
    _mock_httpx_response,
    reset_cfg,  # noqa: F401
)
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.auto_upgrade import (
    _fetch_artifact_info,
    check_for_update,
    get_installed_version,
)


def _client_raising(exc: Exception) -> MagicMock:
    """Build a mock httpx.Client whose get() raises *exc*."""
    client = MagicMock()
    client.get.side_effect = exc
    client.post.side_effect = exc
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client


class TestCheckForUpdate:
    def test_offline_no_platform_url(self) -> None:
        _reset_config(TRWConfig(platform_url=""))
        result = check_for_update()
        assert result["available"] is False
        assert isinstance(result["current"], str)
        assert result["advisory"] is None

    def test_success(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        client = _mock_httpx_client(_mock_httpx_response(json_data={"version": "99.0.0"}))
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is True
        assert result["latest"] == "99.0.0"
        assert result["advisory"] is not None
        assert "99.0.0" in str(result["advisory"])

    def test_network_error(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        with patch("httpx.Client", return_value=_client_raising(httpx.RequestError("unreachable"))):
            result = check_for_update()
        assert result["available"] is False
        assert result["advisory"] is None

    def test_non_200_response(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        client = _mock_httpx_client(_mock_httpx_response(status_code=404))
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is False

    def test_bad_json(self) -> None:
        import json as _json

        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        resp = _mock_httpx_response()
        resp.json.side_effect = _json.JSONDecodeError("not-json", "", 0)
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is False

    def test_same_version(self) -> None:
        current = get_installed_version()
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        client = _mock_httpx_client(_mock_httpx_response(json_data={"version": current}))
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is False
        assert result["advisory"] is None

    def test_channel_in_url(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="lts"))
        client = _client_raising(httpx.RequestError("no"))
        with patch("httpx.Client", return_value=client):
            check_for_update()
        assert client.get.call_count == 1
        call = client.get.call_args
        url = call.args[0]
        params = call.kwargs.get("params") or {}
        assert "/v1/releases/latest" in url
        assert params.get("channel") == "lts"

    def test_sends_auth_header(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="https://example.com",
                platform_api_key="test-key",
                update_channel="latest",
            )
        )
        client = _mock_httpx_client(_mock_httpx_response(json_data={"version": "99.0.0"}))
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is True
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer test-key"

    def test_no_auth_without_key(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="https://example.com",
                platform_api_key="",
                update_channel="latest",
            )
        )
        client = _mock_httpx_client(_mock_httpx_response(json_data={"version": "99.0.0"}))
        with patch("httpx.Client", return_value=client):
            check_for_update()
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers

    def test_fallback_urls_tries_next(self) -> None:
        """When first URL fails, tries the next one."""
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://fail.example.com", "https://ok.example.com"],
                update_channel="latest",
            )
        )

        # Each `with httpx.Client(...) as client` makes a fresh Client instance,
        # so produce a new mock per call: first raises, second returns 200.
        ok_resp = _mock_httpx_response(json_data={"version": "99.0.0"})

        clients = [
            _client_raising(httpx.RequestError("first fails")),
            _mock_httpx_client(ok_resp),
        ]

        with patch("httpx.Client", side_effect=clients):
            result = check_for_update()

        assert result["available"] is True
        # Two Client constructions: one per URL
        assert clients[0].get.call_count == 1
        assert clients[1].get.call_count == 1

    def test_all_urls_fail_returns_fallback(self) -> None:
        """When all platform_urls fail, returns not-available with current as latest."""
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://a.example.com", "https://b.example.com"],
                update_channel="latest",
            )
        )
        with patch("httpx.Client", return_value=_client_raising(httpx.RequestError("down"))):
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
        client = _mock_httpx_client(_mock_httpx_response(json_data=payload))
        with patch("httpx.Client", return_value=client):
            result = _fetch_artifact_info("1.0.0")
        assert result is not None
        assert isinstance(result, dict)
        assert result["artifact_url"] == "https://dl.example.com/v1.tar.gz"
        assert result["artifact_checksum"] == "abc123"

    def test_network_error_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        with patch("httpx.Client", return_value=_client_raising(httpx.RequestError("fail"))):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_bad_json_returns_none(self) -> None:
        import json as _json

        _reset_config(TRWConfig(platform_url="https://example.com"))
        resp = _mock_httpx_response()
        resp.json.side_effect = _json.JSONDecodeError("not-json", "", 0)
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_non_200_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        client = _mock_httpx_client(_mock_httpx_response(status_code=500))
        with patch("httpx.Client", return_value=client):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_auth_header_sent(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", platform_api_key="my-key"))
        client = _mock_httpx_client(_mock_httpx_response(json_data={"artifact_url": "x"}))
        with patch("httpx.Client", return_value=client):
            _fetch_artifact_info("1.0.0")
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer my-key"

    def test_no_auth_header_without_key(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com", platform_api_key=""))
        client = _mock_httpx_client(_mock_httpx_response(json_data={"artifact_url": "x"}))
        with patch("httpx.Client", return_value=client):
            _fetch_artifact_info("1.0.0")
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers

    def test_fallback_to_second_url(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://bad.example.com", "https://good.example.com"],
            )
        )
        clients = [
            _client_raising(httpx.RequestError("fail")),
            _mock_httpx_client(_mock_httpx_response(json_data={"artifact_url": "ok"})),
        ]
        with patch("httpx.Client", side_effect=clients):
            result = _fetch_artifact_info("1.0.0")
        assert result is not None
        assert isinstance(result, dict)
        assert result["artifact_url"] == "ok"
        assert clients[0].get.call_count == 1
        assert clients[1].get.call_count == 1

    def test_all_urls_fail_returns_none(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="",
                platform_urls=["https://a.example.com", "https://b.example.com"],
            )
        )
        with patch("httpx.Client", return_value=_client_raising(httpx.RequestError("fail"))):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_http_error_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        # 403 status — non-2xx returns None via the status-code branch
        client = _mock_httpx_client(_mock_httpx_response(status_code=403))
        with patch("httpx.Client", return_value=client):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_oserror_returns_none(self) -> None:
        _reset_config(TRWConfig(platform_url="https://example.com"))
        with patch("httpx.Client", return_value=_client_raising(OSError("connection reset"))):
            result = _fetch_artifact_info("1.0.0")
        assert result is None


class TestCheckForUpdateEdge:
    """Edge cases for check_for_update."""

    def test_response_missing_version_key(self) -> None:
        """When JSON has no 'version' key, uses current as latest → not available."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        client = _mock_httpx_client(_mock_httpx_response(json_data={"notes": "no version field"}))
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is False
        assert result["latest"] == result["current"]

    def test_http_error_is_caught(self) -> None:
        """httpx.HTTPError (not just RequestError) is caught — fail-open."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        # 500 status — non-2xx returns available=False via the status-code branch
        client = _mock_httpx_client(_mock_httpx_response(status_code=500))
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is False

    def test_oserror_is_caught(self) -> None:
        """OSError during connection is caught — fail-open."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        with patch("httpx.Client", return_value=_client_raising(OSError("connection refused"))):
            result = check_for_update()
        assert result["available"] is False

    def test_key_error_in_response_is_caught(self) -> None:
        """KeyError during response parsing is caught — fail-open."""
        _reset_config(TRWConfig(platform_url="https://example.com", update_channel="latest"))
        resp = _mock_httpx_response(json_data={"version": "99.0.0"})
        resp.json.side_effect = KeyError("bad key")
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            result = check_for_update()
        assert result["available"] is False

    def test_trailing_slash_stripped_from_url(self) -> None:
        """Platform URL with trailing slash does not double-slash in the request."""
        _reset_config(TRWConfig(platform_url="https://example.com/", update_channel="latest"))
        client = _client_raising(httpx.RequestError("no"))
        with patch("httpx.Client", return_value=client):
            check_for_update()
        url = client.get.call_args.args[0]
        assert "//" not in url.split("://", 1)[1]


class TestFetchArtifactInfoEdge:
    """Edge cases for _fetch_artifact_info."""

    def test_version_embedded_in_url(self) -> None:
        """Verify the version string appears in the constructed URL."""
        _reset_config(TRWConfig(platform_url="https://example.com"))
        client = _client_raising(httpx.RequestError("no"))
        with patch("httpx.Client", return_value=client):
            _fetch_artifact_info("3.2.1")
        assert client.get.call_count == 1
        url = client.get.call_args.args[0]
        assert "/v1/releases/3.2.1/artifact" in url

    def test_json_decode_error_returns_none(self) -> None:
        """JSONDecodeError specifically is caught and returns None."""
        import json as _json

        _reset_config(TRWConfig(platform_url="https://example.com"))
        resp = _mock_httpx_response()
        resp.json.side_effect = _json.JSONDecodeError("bad", "", 0)
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            result = _fetch_artifact_info("1.0.0")
        assert result is None

    def test_trailing_slash_stripped(self) -> None:
        """Platform URL trailing slash does not cause double-slash."""
        _reset_config(TRWConfig(platform_url="https://example.com/"))
        client = _client_raising(httpx.RequestError("no"))
        with patch("httpx.Client", return_value=client):
            _fetch_artifact_info("1.0.0")
        url = client.get.call_args.args[0]
        assert "//" not in url.split("://", 1)[1]
