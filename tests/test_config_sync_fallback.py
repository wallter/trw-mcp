"""Tests for backend_url/backend_api_key fallback to platform_* config.

Resolved accessors:
    TRWConfig.resolved_backend_url
    TRWConfig.resolved_backend_api_key

Precedence: explicit backend_* wins; otherwise fall back to the first entry
of platform_urls and platform_api_key. When both groups are empty, returns "".
"""

from __future__ import annotations

import inspect

from pydantic import SecretStr

from trw_mcp.models.config import TRWConfig


class TestResolvedBackendUrl:
    def test_explicit_backend_url_beats_platform_urls(self) -> None:
        config = TRWConfig(
            backend_url="http://explicit.example:9000",
            backend_api_key="explicit-key",
            platform_urls=["https://api.trwframework.com", "http://localhost:5002"],
            platform_api_key=SecretStr("platform-key"),
        )
        assert config.resolved_backend_url == "http://explicit.example:9000"

    def test_empty_backend_url_falls_back_to_first_platform_url(self) -> None:
        config = TRWConfig(
            backend_url="",
            platform_urls=["https://api.trwframework.com", "http://localhost:5002"],
            platform_api_key=SecretStr("platform-key"),
        )
        assert config.resolved_backend_url == "https://api.trwframework.com"

    def test_both_empty_returns_empty_string(self) -> None:
        config = TRWConfig(backend_url="", platform_urls=[])
        assert config.resolved_backend_url == ""


class TestResolvedBackendApiKey:
    def test_explicit_backend_api_key_beats_platform_api_key(self) -> None:
        config = TRWConfig(
            backend_url="http://explicit.example:9000",
            backend_api_key="explicit-key",
            platform_api_key=SecretStr("platform-key"),
        )
        assert config.resolved_backend_api_key == "explicit-key"

    def test_empty_backend_api_key_falls_back_to_platform_api_key(self) -> None:
        config = TRWConfig(
            backend_api_key="",
            platform_urls=["https://api.trwframework.com"],
            platform_api_key=SecretStr("platform-key"),
        )
        assert config.resolved_backend_api_key == "platform-key"

    def test_both_empty_returns_empty_string(self) -> None:
        config = TRWConfig(backend_api_key="", platform_api_key=SecretStr(""))
        assert config.resolved_backend_api_key == ""


class TestSyncLifespanUsesResolvedAccessors:
    """Wiring test: daemon gate in _build_sync_lifespan calls resolved accessors."""

    def test_build_sync_lifespan_references_resolved_accessors(self) -> None:
        from trw_mcp.server import _app

        source = inspect.getsource(_app._build_sync_lifespan)
        assert "resolved_backend_url" in source
        assert "resolved_backend_api_key" in source

    def test_backend_sync_client_uses_resolved_accessors(self) -> None:
        from trw_mcp.sync import client as sync_client_module

        source = inspect.getsource(sync_client_module.BackendSyncClient)
        assert "resolved_backend_url" in source
        assert "resolved_backend_api_key" in source
