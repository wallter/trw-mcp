"""Focused regression tests for PRD-FIX-062 locking portability."""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest
import structlog

from trw_mcp.models.config import TRWConfig


@pytest.mark.unit
def test_locking_windows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.delitem(sys.modules, "trw_mcp._locking", raising=False)

    module = importlib.import_module("trw_mcp._locking")

    assert module._lock_sh(1) is None
    assert module._lock_ex(1) is None
    assert module._lock_ex_nb(1) is None
    assert module._lock_un(1) is None

    monkeypatch.delitem(sys.modules, "trw_mcp._locking", raising=False)
    monkeypatch.delitem(sys.modules, "fcntl", raising=False)
    importlib.import_module("trw_mcp._locking")


@pytest.mark.unit
def test_proxy_uses_portable_locking_shim() -> None:
    import trw_mcp.server._proxy as proxy

    source = inspect.getsource(proxy.ensure_http_server)
    assert "import fcntl" not in source
    assert "from trw_mcp._locking import _lock_ex, _lock_ex_nb, _lock_un" in source


@pytest.mark.unit
def test_proxy_start_wait_default_covers_slow_boot_gc() -> None:
    assert TRWConfig().mcp_startup_wait_seconds >= 90


@pytest.mark.unit
def test_ensure_http_server_uses_configured_startup_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import trw_mcp.server._proxy as proxy

    calls: dict[str, Any] = {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(proxy, "_is_port_open", lambda _host, _port: False)
    monkeypatch.setattr(proxy, "_clean_stale_pid", lambda _pid_path, _log: None)
    monkeypatch.setattr(proxy, "_spawn_http_server", lambda _config, _trw_dir, *, debug: 12345)

    def fake_wait_for_port(
        _host: str,
        _port: int,
        *,
        poll_interval: float = 0.5,
        max_polls: int = 240,
    ) -> bool:
        calls["poll_interval"] = poll_interval
        calls["max_polls"] = max_polls
        return True

    monkeypatch.setattr(proxy, "_wait_for_port", fake_wait_for_port)

    config = TRWConfig.model_validate(
        {
            "mcp_transport": "streamable-http",
            "mcp_startup_wait_seconds": 120,
        }
    )

    assert proxy.ensure_http_server(config, structlog.get_logger(__name__), debug=True) == "http://127.0.0.1:8100/mcp"
    assert calls == {"poll_interval": 0.5, "max_polls": 240}
