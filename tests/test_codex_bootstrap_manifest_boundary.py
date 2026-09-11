"""Bootstrap eligibility comes from the manifest, never session middleware."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import tomllib

from trw_mcp.bootstrap import _codex as codex
from trw_mcp.server import _surface_manifest_registry as registry
from trw_mcp.server._app import mcp


@pytest.mark.parametrize("live_names", [[], ["trw_masked_subset"], ["trw_unmanifested", "external"]])
def test_manifest_is_exact_authority_regardless_of_live_surface(monkeypatch, live_names):
    listing = AsyncMock(return_value=[SimpleNamespace(name=name) for name in live_names])
    monkeypatch.setattr(mcp, "list_tools", listing)
    monkeypatch.setattr(registry, "eligible_tool_names", lambda: {"trw_z", "trw_a", "non_trw"})
    assert codex._registered_trw_tool_names() == ["trw_a", "trw_z"]
    listing.assert_not_called()


def test_generate_config_never_dispatches_live_listing_and_preserves_merge(tmp_path, monkeypatch):
    listing = AsyncMock(side_effect=AssertionError("bootstrap must not dispatch middleware"))
    monkeypatch.setattr(mcp, "list_tools", listing)
    expected = sorted(name for name in registry.eligible_tool_names() if name.startswith("trw_"))
    assert expected
    config_dir = tmp_path / ".codex"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        'model = "operator-model"\n[mcp_servers.trw]\n'
        'enabled_tools = ["external_enabled", "trw_unmanifested"]\n'
        f'disabled_tools = ["external_disabled", "trw_unmanifested", "{expected[0]}"]\n'
        '[[skills.config]]\npath = ".agents/skills/trw-deliver"\nenabled = false\n'
    )
    result = codex.generate_codex_config(tmp_path)
    assert result["errors"] == []
    config = tomllib.loads((config_dir / "config.toml").read_text())
    server = config["mcp_servers"]["trw"]
    assert server["enabled_tools"] == sorted([*expected, "external_enabled"])
    assert server["disabled_tools"] == ["external_disabled", "trw_unmanifested"]
    assert config["model"] == "operator-model"
    assert {"path": ".agents/skills/trw-deliver", "enabled": False} in config["skills"]["config"]
    assert "profiles" not in config
    assert not (tmp_path / ".git").exists()
    listing.assert_not_called()
