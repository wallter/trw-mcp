"""Tests for backend_url/backend_api_key fallback to platform_* config.

Resolved accessors:
    TRWConfig.resolved_backend_url
    TRWConfig.resolved_backend_api_key

Precedence: explicit backend_* wins; otherwise fall back to the first entry
of platform_urls and platform_api_key. When both groups are empty, returns "".
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
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


class TestDeferredBootUsesResolvedAccessors:
    """Wiring: the backend-sync gate consumes the resolved accessors.

    This used to grep ``_build_sync_lifespan``'s source for the accessor NAMES.
    PRD-CORE-248-FR01 moved that resolution off the ``initialize`` critical path
    into ``server/_boot_deferred``, so the names left the lifespan and the text
    assertion went red without any behaviour changing. Grepping the new location
    would repeat the mistake, so the gate is now driven for real: a config whose
    backend credentials exist ONLY as ``platform_*`` must still resolve and start
    a client, which is exactly what a gate reading ``config.backend_url``
    directly could not do.
    """

    def _drive_gate(self, monkeypatch: pytest.MonkeyPatch, config: TRWConfig, tmp_path: Path) -> list[TRWConfig]:
        """Run the real deferred boot step against *config*.

        Returns the configs the sync client was constructed with — an empty list
        means the gate resolved no usable credentials and started nothing.
        """
        from trw_mcp.server import _boot_deferred

        constructed: list[TRWConfig] = []

        class _RecordingClient:
            def __init__(self, *, config: TRWConfig, trw_dir: Path) -> None:
                constructed.append(config)

            async def run_sync_loop(self) -> None:  # pragma: no cover - never awaited here
                return None

        monkeypatch.setattr("trw_mcp.server._app._try_load_config", lambda: config)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: tmp_path)
        monkeypatch.setattr("trw_mcp.sync.client.BackendSyncClient", _RecordingClient)
        _boot_deferred.reset_deferred_boot_state()
        try:
            assert _boot_deferred.ensure_deferred_boot_work() is True
        finally:
            _boot_deferred.reset_deferred_boot_state()
        return constructed

    def test_platform_only_credentials_still_start_a_client(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The positive branch a gate reading ``backend_url`` directly cannot reach."""
        config = TRWConfig(
            backend_url="",
            backend_api_key="",
            platform_urls=["https://api.trwframework.com"],
            platform_api_key=SecretStr("platform-key"),
        )
        assert self._drive_gate(monkeypatch, config, tmp_path) == [config]

    def test_no_credentials_anywhere_starts_no_client(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Negative branch: with nothing to resolve, nothing is constructed.

        Paired with the test above this is what makes the pair a wiring proof
        rather than a smoke test — the ONLY difference between them is where the
        credentials live, so a client can only appear via the resolved accessors.
        """
        config = TRWConfig(backend_url="", backend_api_key="", platform_urls=[], platform_api_key=SecretStr(""))
        assert self._drive_gate(monkeypatch, config, tmp_path) == []

    def test_backend_sync_client_uses_resolved_accessors(self) -> None:
        from trw_mcp.sync import client as sync_client_module

        source = inspect.getsource(sync_client_module.BackendSyncClient)
        assert "resolved_backend_url" in source
        assert "resolved_backend_api_key" in source
