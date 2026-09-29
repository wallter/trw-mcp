"""Tests for shared Cursor legacy bootstrap compatibility helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_get_trw_mcp_entry_cursor_uses_binary_when_on_path() -> None:
    """_get_trw_mcp_entry_cursor returns command='trw-mcp' when binary on PATH."""
    from trw_mcp.bootstrap._cursor import _get_trw_mcp_entry_cursor

    with patch("shutil.which", return_value="/usr/local/bin/trw-mcp"):
        entry = _get_trw_mcp_entry_cursor()

    assert entry["command"] == "trw-mcp"
    # No --debug: every client profile emits the same args. Verbose logging is
    # opted into via .trw/config.yaml debug:true.
    assert entry.get("args", []) == []


@pytest.mark.unit
def test_get_trw_mcp_entry_cursor_falls_back_to_a_portable_python() -> None:
    """The fallback must be a bare ``python3``, never ``sys.executable``.

    This test previously asserted ``entry["command"][0] == sys.executable`` —
    it encoded the defect as the contract. ``.cursor/mcp.json`` is committed
    config, so an absolute interpreter path bakes in the machine that ran the
    installer and breaks the entry for everyone else (PRD-SEC-006, audit
    installer-client-12). The hardening had landed only in
    ``bootstrap/_utils.py``; cursor, opencode, codex and antigravity-cli each
    kept their own copy of the old behaviour.
    """
    from trw_mcp.bootstrap._cursor import _get_trw_mcp_entry_cursor

    with patch("shutil.which", return_value=None):
        entry = _get_trw_mcp_entry_cursor()

    assert isinstance(entry["command"], list)
    assert entry["command"] == ["python3", "-m", "trw_mcp.server"]
    assert not entry["command"][0].startswith("/"), "committed config must not carry a machine-absolute path"


@pytest.mark.unit
def test_hook_handler_entry_typeddict_exported() -> None:
    """HookHandlerEntry and CursorHooksV1Config are importable from _cursor."""
    from trw_mcp.bootstrap._cursor import CursorHooksV1Config, HookHandlerEntry

    handler: HookHandlerEntry = {"command": "trw-stop.sh", "type": "command", "timeout": 5}
    config: CursorHooksV1Config = {
        "version": 1,
        "hooks": {"stop": [handler]},
    }
    assert config["version"] == 1
    assert "stop" in config["hooks"]
