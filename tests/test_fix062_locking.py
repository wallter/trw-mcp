"""Focused regression tests for PRD-FIX-062 locking portability."""

from __future__ import annotations

import importlib
import inspect
import sys

import pytest


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
