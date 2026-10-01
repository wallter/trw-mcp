"""CODEX-PROXY-LAUNCHER: ``doctor``'s shared_mcp row also warns when ``.codex/config.toml`` launches stdio.

A merge once reverted the tracked Codex config to a bare ``trw-mcp`` while ``shared_mcp.enabled`` stayed true, so
every Codex thread ran its own standalone server and none reached the shared backend; nothing said so.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server._doctor import check_shared_mcp

_STDIO = "`.codex/config.toml` launches stdio, not the proxy"
_OK = "shared trw-mcp on"


def _run(tmp_path: Path, *, enabled: bool = True) -> tuple[str, str]:
    cfg = SimpleNamespace(trw_dir=".trw", shared_mcp=SharedMcpConfig(enabled=enabled, envs_dir=str(tmp_path / "envs")))
    result = check_shared_mcp(tmp_path, cfg)
    return result.status, result.message


def _claude_on_the_proxy(tmp_path: Path) -> None:
    servers = {"trw": {"command": "trw-mcp-proxy", "args": []}}
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _codex(tmp_path: Path, text: str) -> None:
    (tmp_path / ".codex").mkdir(exist_ok=True)
    (tmp_path / ".codex" / "config.toml").write_text(text, encoding="utf-8")


def _trw_table(command: str, args: str = "[]") -> str:
    return f'[mcp_servers.trw]\ncommand = "{command}"\nargs = {args}\nenabled = true\n'


def test_warns_when_enabled_but_codex_is_stdio(tmp_path: Path) -> None:
    _claude_on_the_proxy(tmp_path)
    _codex(tmp_path, _trw_table("trw-mcp"))
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert _STDIO in message
    assert "`trw`" in message
    assert "`.mcp.json`" not in message  # the Claude config is fine; only Codex is named


@pytest.mark.parametrize(
    ("command", "args"),
    [
        (".venv/bin/trw-mcp-proxy", "[]"),
        ("/abs/.venv/bin/trw-mcp-proxy", "[]"),
        ("trw-mcp-proxy", "[]"),
        ("python3", '["-m", "trw_mcp.shared_server"]'),
    ],
)
def test_codex_proxy_launchers_are_ok(tmp_path: Path, command: str, args: str) -> None:
    _claude_on_the_proxy(tmp_path)
    _codex(tmp_path, _trw_table(command, args))
    status, message = _run(tmp_path)
    assert (status, _STDIO in message, _OK in message) == ("PASS", False, True)


def test_a_project_without_a_codex_config_is_not_flagged_for_it(tmp_path: Path) -> None:
    _claude_on_the_proxy(tmp_path)
    status, message = _run(tmp_path)
    assert (status, ".codex" in message) == ("PASS", False)


def test_a_codex_config_without_a_trw_server_is_not_flagged(tmp_path: Path) -> None:
    _claude_on_the_proxy(tmp_path)
    _codex(tmp_path, '[mcp_servers.other]\ncommand = "x"\n')
    status, message = _run(tmp_path)
    assert (status, ".codex" in message) == ("PASS", False)


def test_an_unparseable_codex_config_warns_distinctly(tmp_path: Path) -> None:
    _claude_on_the_proxy(tmp_path)
    _codex(tmp_path, "[mcp_servers.trw\ncommand = ")
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert "`.codex/config.toml` is unparseable" in message


def test_both_configs_on_stdio_are_both_named(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"trw": {"command": "trw-mcp", "args": []}}}), encoding="utf-8"
    )
    _codex(tmp_path, _trw_table("trw-mcp"))
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert "`.mcp.json` launches stdio" in message
    assert _STDIO in message


def test_disabled_is_unchanged_even_with_a_stdio_codex_config(tmp_path: Path) -> None:
    _codex(tmp_path, _trw_table("trw-mcp"))
    status, message = _run(tmp_path, enabled=False)
    assert status == "SKIP"
    assert _STDIO not in message
