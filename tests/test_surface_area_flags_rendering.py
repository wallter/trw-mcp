"""Resolver and rendering tests for TRW surface area flags."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig


@pytest.mark.unit
def test_resolve_surface_disabled_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('nudge') returns '' when nudges are disabled."""
    from trw_mcp.state import surface_resolver

    cfg = TRWConfig(nudge_enabled=False)
    monkeypatch.setattr(surface_resolver, "get_config", lambda: cfg, raising=False)
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("nudge") == ""


@pytest.mark.unit
def test_resolve_surface_enabled_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('nudge') returns '__ENABLED__' when nudges are enabled."""
    cfg = TRWConfig(nudge_enabled=True)
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("nudge") == "__ENABLED__"


@pytest.mark.unit
def test_resolve_surface_disabled_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('recall') returns '' when recall is disabled."""
    cfg = TRWConfig(learning_recall_enabled=False)
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("recall") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('hooks') returns '' when hooks are disabled."""
    cfg = TRWConfig(hooks_enabled=False)
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("hooks") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('skills') returns '' when skills are disabled."""
    cfg = TRWConfig(skills_enabled=False)
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("skills") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('agents') returns '' when agents are disabled."""
    cfg = TRWConfig(agents_enabled=False)
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("agents") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_framework_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('framework_ref') returns '' when disabled."""
    cfg = TRWConfig(framework_md_enabled=False)
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("framework_ref") == ""


@pytest.mark.unit
def test_resolve_surface_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface with unknown ID returns '__ENABLED__' (permissive)."""
    cfg = TRWConfig()
    monkeypatch.setattr("trw_mcp.models.config._loader.get_config", lambda: cfg)

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("unknown_surface_xyz") == "__ENABLED__"


@pytest.mark.unit
def test_render_framework_ref_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_framework_reference() returns '' when include_framework_ref=False."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    patched_profile = cfg.client_profile.model_copy(update={"include_framework_ref": False})
    monkeypatch.setattr(
        _static_sections,
        "get_config",
        lambda: _MockConfigWithProfile(patched_profile),
    )

    from trw_mcp.state.claude_md._static_sections import render_framework_reference

    result = render_framework_reference()
    assert result == ""


@pytest.mark.unit
def test_render_framework_ref_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_framework_reference() returns content when include_framework_ref=True (default)."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    monkeypatch.setattr(_static_sections, "get_config", lambda: cfg)

    from trw_mcp.state.claude_md._static_sections import render_framework_reference

    result = render_framework_reference()
    assert "Framework Reference" in result
    assert "FRAMEWORK.md" in result


@pytest.mark.unit
def test_render_delegation_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_delegation_protocol() returns '' when include_delegation=False."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    patched_profile = cfg.client_profile.model_copy(update={"include_delegation": False})
    monkeypatch.setattr(
        _static_sections,
        "get_config",
        lambda: _MockConfigWithProfile(patched_profile),
    )

    from trw_mcp.state.claude_md._static_sections import render_delegation_protocol

    result = render_delegation_protocol()
    assert result == ""


@pytest.mark.unit
def test_render_delegation_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_delegation_protocol() returns content when include_delegation=True (default)."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    monkeypatch.setattr(_static_sections, "get_config", lambda: cfg)

    from trw_mcp.state.claude_md._static_sections import render_delegation_protocol

    result = render_delegation_protocol()
    assert "Delegation" in result


@pytest.mark.unit
def test_render_agent_teams_disabled_by_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_agent_teams_protocol() returns '' when include_agent_teams=False."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    patched_profile = cfg.client_profile.model_copy(update={"include_agent_teams": False})
    monkeypatch.setattr(
        _static_sections,
        "get_config",
        lambda: _MockConfigWithProfile(patched_profile),
    )

    from trw_mcp.state.claude_md._static_sections import render_agent_teams_protocol

    result = render_agent_teams_protocol()
    assert result == ""


@pytest.mark.unit
def test_render_agent_teams_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_agent_teams_protocol() is an empty compatibility shim in v25."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    monkeypatch.setattr(_static_sections, "get_config", lambda: cfg)

    from trw_mcp.state.claude_md._static_sections import render_agent_teams_protocol

    result = render_agent_teams_protocol()
    assert result == ""


class _MockConfigWithProfile:
    """Minimal mock that exposes a client_profile property and agent_teams_enabled."""

    def __init__(self, profile: object) -> None:
        self._profile = profile
        self.agent_teams_enabled = True

    @property
    def client_profile(self) -> object:
        return self._profile
