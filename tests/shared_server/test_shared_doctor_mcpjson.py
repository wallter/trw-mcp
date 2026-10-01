"""HOTRELOAD-MCPJSON-DOCTOR: ``doctor``'s shared_mcp row warns when ``.mcp.json`` still launches stdio."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _doctor
from trw_mcp.shared_server._doctor import check_shared_mcp

_STDIO = "`.mcp.json` launches stdio, not the proxy"
_OK = "shared trw-mcp on"


def _run(tmp_path: Path, *, enabled: bool = True) -> tuple[str, str]:
    cfg = SimpleNamespace(
        trw_dir=".trw",
        embeddings_enabled=True,
        shared_mcp=SharedMcpConfig(enabled=enabled, envs_dir=str(tmp_path / "envs")),
    )
    result = check_shared_mcp(tmp_path, cfg)
    return result.status, result.message


def _write(tmp_path: Path, servers: Any) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _trw(command: Any, args: Any) -> dict[str, Any]:
    return {"trw": {"command": command, "args": args}}


def test_warns_when_enabled_but_mcpjson_is_stdio(tmp_path: Path) -> None:
    _write(tmp_path, _trw(".venv/bin/trw-mcp", []))
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert _STDIO in message
    assert "`trw`" in message
    assert "trw-mcp update-project" in message


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("/abs/.venv/bin/trw-mcp-proxy", []),
        ("C:\\venv\\Scripts\\trw-mcp-proxy.exe", []),
        ("uv", ["run", "trw-mcp-proxy"]),
        ("uvx", ["--from", "trw-mcp", "trw-mcp-proxy"]),
        ("python", ["-m", "trw_mcp.shared_server"]),
    ],
)
def test_proxy_launchers_are_ok(tmp_path: Path, command: str, args: list[str]) -> None:
    _write(tmp_path, _trw(command, args))
    status, message = _run(tmp_path)
    assert (status, _STDIO in message, _OK in message) == ("PASS", False, True)


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("/abs/trw-mcp-proxy-old", []),
        ("python", ["-m", "trw_mcp.shared_server_x"]),
        ("python", ["trw_mcp.shared_server"]),
        ("bash", ["-c", "exec .venv/bin/trw-mcp-proxy --x"]),
        ("python", ["-m"]),
    ],
)
def test_decoys_warn(tmp_path: Path, command: str, args: list[str]) -> None:
    _write(tmp_path, _trw(command, args))
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert _STDIO in message


@pytest.mark.parametrize(
    ("servers", "fragment"),
    [
        ([], "no `mcpServers` object"),
        ({"other": {"command": "x", "args": []}}, "no trw server entry"),
        ({"trw": "not-a-dict"}, "no trw server entry"),
        (_trw("trw-mcp-proxy", "not-a-list"), _STDIO),
        (_trw(["trw-mcp-proxy"], []), _STDIO),
        (_trw("trw-mcp-proxy", [1, 2]), _STDIO),
    ],
)
def test_malformed_shapes_warn_with_distinct_messages(tmp_path: Path, servers: Any, fragment: str) -> None:
    _write(tmp_path, servers)
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert fragment in message


def test_non_dict_top_level_warns(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("[1]", encoding="utf-8")
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert "no `mcpServers` object" in message


def test_any_proxy_entry_among_trw_like_keys_is_ok(tmp_path: Path) -> None:
    _write(tmp_path, {"trw": {"command": "trw-mcp", "args": []}, "my-TRW-proxy": {"command": "trw-mcp-proxy"}})
    assert _OK in _run(tmp_path)[1]


def test_stdio_message_names_trw_key_else_trw_like_keys(tmp_path: Path) -> None:
    _write(tmp_path, {"my-TRW": {"command": "trw-mcp", "args": []}, "unrelated": {"command": "x"}})
    _, message = _run(tmp_path)
    assert "`my-TRW`" in message
    assert "unrelated" not in message


def test_missing_mcpjson_warns_when_enabled(tmp_path: Path) -> None:
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert "`.mcp.json` is missing" in message


def test_unparseable_mcpjson_warns_when_enabled(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert "`.mcp.json` is unparseable" in message


def test_both_warn_messages_are_concatenated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, _trw("trw-mcp", []))
    monkeypatch.setattr(_doctor, "doctor_row", lambda *_, **__: ("WARN", "env broke"))
    status, message = _run(tmp_path)
    assert status == "WARN"
    assert message.startswith("env broke; ")
    assert _STDIO in message


def test_disabled_is_unchanged_even_with_stdio_mcpjson(tmp_path: Path) -> None:
    _write(tmp_path, _trw(".venv/bin/trw-mcp", []))
    status, message = _run(tmp_path, enabled=False)
    assert status == "SKIP"
    assert _STDIO not in message
