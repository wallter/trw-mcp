"""The post-upgrade reconnect list comes from the client-profile catalog, so every client with an MCP config is named.

Feedback #158 (sub_2e-ASgP7E8qUg0xn): the list reused the launcher-divergence registry, which omits Grok.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.server._doctor_launcher_divergence import reconnect_client_configs

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


@pytest.mark.parametrize("rel", [".mcp.json", ".codex/config.toml", ".cursor/mcp.json", ".grok/config.toml"])
def test_each_present_client_mcp_config_is_listed(tmp_path: Path, rel: str) -> None:
    (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rel).write_text("x", encoding="utf-8")
    assert reconnect_client_configs(tmp_path) == [rel]


def test_a_hook_or_settings_file_is_not_a_reconnect_target(tmp_path: Path) -> None:
    for rel in (".claude/settings.json", ".codex/hooks.json", "CLAUDE.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x", encoding="utf-8")
    assert reconnect_client_configs(tmp_path) == []


def test_the_installer_reads_the_catalog_derived_list() -> None:
    assert "reconnect_client_configs(" in _SCRIPT.read_text(encoding="utf-8")
