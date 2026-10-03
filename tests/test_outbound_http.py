"""Unit tests for the shared outbound-HTTP trust helper (PRD-SEC-021 FR04/FR05).

``trw_mcp._outbound_http`` is the ONE place the https-or-loopback destination policy and
the ``follow_redirects=False`` client construction live; ``tests/test_urlopen_census.py``
exercises it indirectly through the three migrated call sites, this file tests the helper
itself directly.
"""

from __future__ import annotations

import httpx
import pytest

from trw_mcp._outbound_http import outbound_http_client, require_https_or_loopback


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.test/v1/x",
        "http://127.0.0.1:8000/x",
        "http://[::1]:8000/x",
        "http://localhost:8000/x",
    ],
)
def test_require_https_or_loopback_permits(url: str) -> None:
    require_https_or_loopback(url)  # must not raise
    if url.startswith("https://api.example.test"):
        # Contrast: the permit is the https scheme, not the host: the same host over http is refused.
        with pytest.raises(ValueError, match="non-https"):
            require_https_or_loopback(url.replace("https://", "http://"))
    else:
        # Loopback is permitted over either scheme.
        require_https_or_loopback(url.replace("http://", "https://"))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/x",
        "http://not-loopback.internal/x",
    ],
)
def test_require_https_or_loopback_refuses(url: str) -> None:
    with pytest.raises(ValueError, match="non-https"):
        require_https_or_loopback(url)


def test_outbound_http_client_disables_redirects() -> None:
    client = outbound_http_client(timeout=1.0)
    try:
        assert client.follow_redirects is False
    finally:
        client.close()


def test_outbound_http_client_is_an_httpx_client() -> None:
    client = outbound_http_client(timeout=1.0)
    try:
        assert isinstance(client, httpx.Client)
    finally:
        client.close()
