"""httpx mocks shared by the telemetry tests: a Response-shaped mock and a context-manager Client."""

from __future__ import annotations

from unittest.mock import MagicMock


def _mock_httpx_response(
    status_code: int = 200,
    json_data: dict[str, object] | list[object] | None = None,
    content: bytes | None = None,
    text: str = "",
    reason_phrase: str = "OK",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Return a MagicMock shaped like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.content = content if content is not None else b""
    resp.text = text
    resp.reason_phrase = reason_phrase
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    return resp


def _mock_httpx_client(response: MagicMock) -> MagicMock:
    """Return a context-manager-compatible mock httpx.Client whose get/post return *response*."""
    client = MagicMock()
    client.get.return_value = response
    client.post.return_value = response
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client
