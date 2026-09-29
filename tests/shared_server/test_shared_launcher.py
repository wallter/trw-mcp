"""Opting a project into the shared server repoints every client generator through the one launcher seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import _utils
from trw_mcp.bootstrap._codex import _trw_mcp_server_entry as codex_entry
from trw_mcp.bootstrap._grok import merge_grok_config
from trw_mcp.bootstrap._utils import resolve_trw_mcp_launcher

pytestmark = pytest.mark.integration


def _project(tmp_path: Path, config: str, *scripts: str) -> Path:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text(config, encoding="utf-8")
    for script in scripts:
        path = tmp_path / ".venv" / "bin" / script
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
    return tmp_path


ON = "shared_mcp:\n  enabled: true\n"


@pytest.mark.parametrize(
    ("config", "scripts", "path_has", "expected"),
    [
        (ON, ("trw-mcp", "trw-mcp-proxy"), None, (".venv/bin/trw-mcp-proxy", [])),
        (ON, ("trw-mcp",), "trw-mcp-proxy", ("trw-mcp-proxy", [])),
        (ON, ("trw-mcp",), None, ("python3", ["-m", "trw_mcp.shared_server"])),
        (ON, ("trw-mcp", "python"), None, (".venv/bin/python", ["-m", "trw_mcp.shared_server"])),
        ("debug: true\n", ("python",), None, (".venv/bin/python", ["-m", "trw_mcp.server"])),
        ("shared_mcp:\n  enabled: false\n", ("trw-mcp", "trw-mcp-proxy"), None, (".venv/bin/trw-mcp", [])),
        ("shared_mcp: [unclosed\n", ("trw-mcp",), None, (".venv/bin/trw-mcp", [])),
        ("debug: true\n", ("trw-mcp",), None, (".venv/bin/trw-mcp", [])),
    ],
    ids=[
        "on-venv-proxy",
        "on-path-proxy",
        "on-python-fallback",
        "on-venv-python",
        "off-venv-python",
        "off",
        "unreadable-is-off",
        "absent-is-off",
    ],
)
def test_the_launcher_follows_the_shared_mcp_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: str,
    scripts: tuple[str, ...],
    path_has: str | None,
    expected: tuple[str, list[str]],
) -> None:
    monkeypatch.setattr(_utils.shutil, "which", lambda name: f"/usr/bin/{name}" if name == path_has else None)
    assert resolve_trw_mcp_launcher(_project(tmp_path, config, *scripts)) == expected


def test_client_generators_emit_the_proxy_when_enabled(tmp_path: Path) -> None:
    project = _project(tmp_path, ON, "trw-mcp", "trw-mcp-proxy")
    assert codex_entry(project)["command"] == ".venv/bin/trw-mcp-proxy"
    grok = merge_grok_config({}, target_dir=project)
    assert grok["mcp_servers"]["trw"]["command"] == ".venv/bin/trw-mcp-proxy"  # type: ignore[index]
