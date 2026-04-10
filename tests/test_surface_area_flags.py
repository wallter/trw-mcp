"""Tests for effective_* surface area properties on TRWConfig (PRD-CORE-125).

Verifies profile-aware flag resolution:
- None sentinel on TRWConfig -> delegates to client profile
- Explicit config value -> overrides profile
- Profile-specific defaults (opencode = light profile)
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._sub_models import ToolsConfig


# ---------------------------------------------------------------------------
# effective_nudge_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_effective_nudge_enabled_default() -> None:
    """Default config (None) with claude-code profile -> True."""
    cfg = TRWConfig()
    assert cfg.nudge_enabled is None
    assert cfg.effective_nudge_enabled is True


@pytest.mark.unit
def test_effective_nudge_enabled_explicit_false() -> None:
    """Explicit nudge_enabled=False overrides profile default."""
    cfg = TRWConfig(nudge_enabled=False)
    assert cfg.effective_nudge_enabled is False


@pytest.mark.unit
def test_effective_nudge_enabled_explicit_true() -> None:
    """Explicit nudge_enabled=True overrides profile default."""
    cfg = TRWConfig(nudge_enabled=True, target_platforms=["opencode"])
    assert cfg.effective_nudge_enabled is True


@pytest.mark.unit
def test_effective_nudge_enabled_opencode_profile() -> None:
    """None config + opencode profile -> False (light profile disables nudges)."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.nudge_enabled is None
    assert cfg.client_profile.nudge_enabled is False
    assert cfg.effective_nudge_enabled is False


# ---------------------------------------------------------------------------
# effective_hooks_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_effective_hooks_enabled_default() -> None:
    """Default config (None) with claude-code profile -> True."""
    cfg = TRWConfig()
    assert cfg.hooks_enabled is None
    assert cfg.effective_hooks_enabled is True


@pytest.mark.unit
def test_effective_hooks_enabled_explicit_false() -> None:
    """Explicit hooks_enabled=False overrides profile default."""
    cfg = TRWConfig(hooks_enabled=False)
    assert cfg.effective_hooks_enabled is False


@pytest.mark.unit
def test_effective_hooks_enabled_opencode_profile() -> None:
    """None config + opencode profile -> False (light profile disables hooks)."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.hooks_enabled is None
    assert cfg.client_profile.hooks_enabled is False
    assert cfg.effective_hooks_enabled is False


# ---------------------------------------------------------------------------
# effective_tool_exposure_mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_effective_tool_exposure_mode_default() -> None:
    """Default config ('all') with claude-code profile -> 'all'."""
    cfg = TRWConfig()
    assert cfg.tool_exposure_mode == "all"
    assert cfg.effective_tool_exposure_mode == "all"


@pytest.mark.unit
def test_effective_tool_exposure_mode_explicit_core() -> None:
    """Explicit tool_exposure_mode='core' overrides profile."""
    cfg = TRWConfig(tool_exposure_mode="core")
    assert cfg.effective_tool_exposure_mode == "core"


@pytest.mark.unit
def test_effective_tool_exposure_mode_opencode_profile() -> None:
    """Default config ('all') + opencode profile -> 'standard' (from profile)."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.tool_exposure_mode == "all"
    assert cfg.client_profile.tool_exposure_mode == "standard"
    assert cfg.effective_tool_exposure_mode == "standard"


@pytest.mark.unit
def test_effective_tool_exposure_mode_explicit_overrides_opencode() -> None:
    """Explicit tool_exposure_mode='minimal' with opencode -> 'minimal'."""
    cfg = TRWConfig(tool_exposure_mode="minimal", target_platforms=["opencode"])
    assert cfg.effective_tool_exposure_mode == "minimal"


# ---------------------------------------------------------------------------
# effective_skills_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_effective_skills_enabled_default() -> None:
    """Default config (None) with claude-code profile -> True."""
    cfg = TRWConfig()
    assert cfg.skills_enabled is None
    assert cfg.effective_skills_enabled is True


@pytest.mark.unit
def test_effective_skills_enabled_explicit_false() -> None:
    """Explicit skills_enabled=False overrides profile."""
    cfg = TRWConfig(skills_enabled=False)
    assert cfg.effective_skills_enabled is False


@pytest.mark.unit
def test_effective_skills_enabled_opencode_profile() -> None:
    """None config + opencode profile -> False (light profile disables skills)."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.skills_enabled is None
    assert cfg.client_profile.skills_enabled is False
    assert cfg.effective_skills_enabled is False


# ---------------------------------------------------------------------------
# effective_learning_recall_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_effective_learning_recall_enabled_default() -> None:
    """Default config (None) with claude-code profile -> True."""
    cfg = TRWConfig()
    assert cfg.learning_recall_enabled is None
    assert cfg.effective_learning_recall_enabled is True


@pytest.mark.unit
def test_effective_learning_recall_enabled_explicit_false() -> None:
    """Explicit learning_recall_enabled=False overrides profile."""
    cfg = TRWConfig(learning_recall_enabled=False)
    assert cfg.effective_learning_recall_enabled is False


@pytest.mark.unit
def test_effective_learning_recall_enabled_opencode_profile() -> None:
    """None config + opencode profile -> True (light profiles still enable recall)."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.learning_recall_enabled is None
    assert cfg.client_profile.learning_recall_enabled is True
    assert cfg.effective_learning_recall_enabled is True


# ---------------------------------------------------------------------------
# effective_mcp_instructions_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_effective_mcp_instructions_enabled_default() -> None:
    """Default config (None) with claude-code profile -> True."""
    cfg = TRWConfig()
    assert cfg.mcp_server_instructions_enabled is None
    assert cfg.effective_mcp_instructions_enabled is True


@pytest.mark.unit
def test_effective_mcp_instructions_enabled_explicit_false() -> None:
    """Explicit mcp_server_instructions_enabled=False overrides profile."""
    cfg = TRWConfig(mcp_server_instructions_enabled=False)
    assert cfg.effective_mcp_instructions_enabled is False


@pytest.mark.unit
def test_effective_mcp_instructions_enabled_opencode_profile() -> None:
    """None config + opencode profile -> False (light profile disables MCP instructions)."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.mcp_server_instructions_enabled is None
    assert cfg.client_profile.mcp_instructions_enabled is False
    assert cfg.effective_mcp_instructions_enabled is False


# ---------------------------------------------------------------------------
# ToolsConfig sub-config
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tools_sub_config_default() -> None:
    """config.tools returns ToolsConfig with default values."""
    cfg = TRWConfig()
    tools = cfg.tools
    assert isinstance(tools, ToolsConfig)
    assert tools.tool_exposure_mode == "all"
    assert tools.tool_exposure_list == []
    assert tools.tool_descriptions_variant == "default"
    assert tools.mcp_server_instructions_enabled is None


@pytest.mark.unit
def test_tools_sub_config_reflects_explicit_values() -> None:
    """config.tools reflects explicitly set values from TRWConfig."""
    cfg = TRWConfig(tool_exposure_mode="core", tool_descriptions_variant="verbose")
    tools = cfg.tools
    assert tools.tool_exposure_mode == "core"
    assert tools.tool_descriptions_variant == "verbose"


# ---------------------------------------------------------------------------
# New fields exist on TRWConfig with correct defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_nudge_budget_chars_default() -> None:
    """nudge_budget_chars defaults to 600."""
    cfg = TRWConfig()
    assert cfg.nudge_budget_chars == 600


@pytest.mark.unit
def test_nudge_dedup_enabled_default() -> None:
    """nudge_dedup_enabled defaults to True."""
    cfg = TRWConfig()
    assert cfg.nudge_dedup_enabled is True


@pytest.mark.unit
def test_learning_injection_preview_chars_default() -> None:
    """learning_injection_preview_chars defaults to 500."""
    cfg = TRWConfig()
    assert cfg.learning_injection_preview_chars == 500


@pytest.mark.unit
def test_framework_md_enabled_default_is_none() -> None:
    """framework_md_enabled defaults to None (sentinel)."""
    cfg = TRWConfig()
    assert cfg.framework_md_enabled is None


@pytest.mark.unit
def test_agents_enabled_default_is_none() -> None:
    """agents_enabled defaults to None (sentinel)."""
    cfg = TRWConfig()
    assert cfg.agents_enabled is None


@pytest.mark.unit
def test_session_start_recall_enabled_default_is_none() -> None:
    """session_start_recall_enabled defaults to None (sentinel)."""
    cfg = TRWConfig()
    assert cfg.session_start_recall_enabled is None


@pytest.mark.unit
def test_nudge_urgency_mode_default() -> None:
    """nudge_urgency_mode defaults to 'adaptive'."""
    cfg = TRWConfig()
    assert cfg.nudge_urgency_mode == "adaptive"


# ---------------------------------------------------------------------------
# SurfaceConfig unified model (PRD-CORE-125 Phase 3 — FR13)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_surface_config_from_defaults() -> None:
    """SurfaceConfig from default TRWConfig resolves all flags to True/enabled."""
    cfg = TRWConfig()
    surfaces = cfg.surfaces

    from trw_mcp.models.config._surface_config import SurfaceConfig

    assert isinstance(surfaces, SurfaceConfig)
    assert surfaces.nudge.enabled is True
    assert surfaces.nudge.urgency_mode == "adaptive"
    assert surfaces.nudge.budget_chars == 600
    assert surfaces.nudge.dedup_enabled is True
    assert surfaces.tool_exposure.mode == "all"
    assert surfaces.tool_exposure.custom_list == ()
    assert surfaces.recall.enabled is True
    assert surfaces.recall.max_results == cfg.recall_max_results
    assert surfaces.recall.injection_preview_chars == 500
    assert surfaces.recall.session_start_recall is True
    assert surfaces.mcp_instructions_enabled is True
    assert surfaces.hooks_enabled is True
    assert surfaces.skills_enabled is True
    assert surfaces.agents_enabled is True
    assert surfaces.framework_ref_enabled is True
    assert surfaces.tool_descriptions_variant == "default"


@pytest.mark.unit
def test_surface_config_from_light_profile() -> None:
    """SurfaceConfig with opencode profile reflects light-mode defaults."""
    cfg = TRWConfig(target_platforms=["opencode"])
    surfaces = cfg.surfaces

    # opencode profile disables nudges, hooks, mcp_instructions, skills
    assert surfaces.nudge.enabled is False
    assert surfaces.hooks_enabled is False
    assert surfaces.mcp_instructions_enabled is False
    assert surfaces.skills_enabled is False
    # opencode profile keeps recall enabled
    assert surfaces.recall.enabled is True
    # opencode profile uses "standard" tool exposure
    assert surfaces.tool_exposure.mode == "standard"


@pytest.mark.unit
def test_surface_config_explicit_overrides_profile() -> None:
    """Explicit config values override profile defaults in SurfaceConfig."""
    cfg = TRWConfig(
        target_platforms=["opencode"],
        nudge_enabled=True,  # Override opencode's False
        tool_exposure_mode="minimal",  # Override opencode's standard
    )
    surfaces = cfg.surfaces
    assert surfaces.nudge.enabled is True
    assert surfaces.tool_exposure.mode == "minimal"


@pytest.mark.unit
def test_surface_config_is_frozen() -> None:
    """SurfaceConfig and sub-models are frozen (immutable)."""
    cfg = TRWConfig()
    surfaces = cfg.surfaces

    with pytest.raises(Exception):  # ValidationError for frozen models
        surfaces.hooks_enabled = False  # type: ignore[misc]

    with pytest.raises(Exception):
        surfaces.nudge.enabled = False  # type: ignore[misc]


@pytest.mark.unit
def test_surface_config_agents_enabled_none_resolves_true() -> None:
    """agents_enabled=None (default) resolves to True in SurfaceConfig."""
    cfg = TRWConfig()
    assert cfg.agents_enabled is None
    assert cfg.surfaces.agents_enabled is True


@pytest.mark.unit
def test_surface_config_agents_enabled_false() -> None:
    """Explicit agents_enabled=False propagates to SurfaceConfig."""
    cfg = TRWConfig(agents_enabled=False)
    assert cfg.surfaces.agents_enabled is False


@pytest.mark.unit
def test_surface_config_framework_md_enabled_none_resolves_true() -> None:
    """framework_md_enabled=None (default) resolves to True in SurfaceConfig."""
    cfg = TRWConfig()
    assert cfg.framework_md_enabled is None
    assert cfg.surfaces.framework_ref_enabled is True


@pytest.mark.unit
def test_surface_config_framework_md_enabled_false() -> None:
    """Explicit framework_md_enabled=False propagates to SurfaceConfig."""
    cfg = TRWConfig(framework_md_enabled=False)
    assert cfg.surfaces.framework_ref_enabled is False


@pytest.mark.unit
def test_surface_config_session_start_recall_false() -> None:
    """Explicit session_start_recall_enabled=False propagates to SurfaceConfig."""
    cfg = TRWConfig(session_start_recall_enabled=False)
    assert cfg.surfaces.recall.session_start_recall is False


# ---------------------------------------------------------------------------
# Surface resolver (PRD-CORE-125 Phase 3 — FR14)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_surface_disabled_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('nudge') returns '' when nudges are disabled."""
    from trw_mcp.models.config._loader import get_config as _real_get_config
    from trw_mcp.state import surface_resolver

    cfg = TRWConfig(nudge_enabled=False)
    monkeypatch.setattr(surface_resolver, "get_config", lambda: cfg, raising=False)
    # Patch at the deferred import location inside resolve_surface
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("nudge") == ""


@pytest.mark.unit
def test_resolve_surface_enabled_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('nudge') returns '__ENABLED__' when nudges are enabled."""
    from trw_mcp.state import surface_resolver

    cfg = TRWConfig(nudge_enabled=True)
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("nudge") == "__ENABLED__"


@pytest.mark.unit
def test_resolve_surface_disabled_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('recall') returns '' when recall is disabled."""
    cfg = TRWConfig(learning_recall_enabled=False)
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("recall") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('hooks') returns '' when hooks are disabled."""
    cfg = TRWConfig(hooks_enabled=False)
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("hooks") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('skills') returns '' when skills are disabled."""
    cfg = TRWConfig(skills_enabled=False)
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("skills") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('agents') returns '' when agents are disabled."""
    cfg = TRWConfig(agents_enabled=False)
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("agents") == ""


@pytest.mark.unit
def test_resolve_surface_disabled_framework_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface('framework_ref') returns '' when disabled."""
    cfg = TRWConfig(framework_md_enabled=False)
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("framework_ref") == ""


@pytest.mark.unit
def test_resolve_surface_unknown_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_surface with unknown ID returns '__ENABLED__' (permissive)."""
    cfg = TRWConfig()
    monkeypatch.setattr(
        "trw_mcp.models.config._loader.get_config", lambda: cfg
    )

    from trw_mcp.state.surface_resolver import resolve_surface

    assert resolve_surface("unknown_surface_xyz") == "__ENABLED__"


# ---------------------------------------------------------------------------
# FR10: Unwired profile flags — render_* gating tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_framework_ref_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_framework_reference() returns '' when include_framework_ref=False."""
    from trw_mcp.models.config._client_profile import ClientProfile
    from trw_mcp.state.claude_md import _static_sections

    # Create a config whose profile has include_framework_ref=False
    cfg = TRWConfig()

    # Override the profile resolution to return a profile with the flag off
    original_profile = cfg.client_profile
    patched_profile = original_profile.model_copy(update={"include_framework_ref": False})
    monkeypatch.setattr(
        _static_sections, "get_config",
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
        _static_sections, "get_config",
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
        _static_sections, "get_config",
        lambda: _MockConfigWithProfile(patched_profile),
    )

    from trw_mcp.state.claude_md._static_sections import render_agent_teams_protocol

    result = render_agent_teams_protocol()
    assert result == ""


@pytest.mark.unit
def test_render_agent_teams_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """render_agent_teams_protocol() returns content when defaults are active."""
    from trw_mcp.state.claude_md import _static_sections

    cfg = TRWConfig()
    monkeypatch.setattr(_static_sections, "get_config", lambda: cfg)

    from trw_mcp.state.claude_md._static_sections import render_agent_teams_protocol

    result = render_agent_teams_protocol()
    assert "Agent Teams" in result


# ---------------------------------------------------------------------------
# Helpers for FR10 tests
# ---------------------------------------------------------------------------


class _MockConfigWithProfile:
    """Minimal mock that exposes a client_profile property and agent_teams_enabled."""

    def __init__(self, profile: object) -> None:
        self._profile = profile
        # Needed for render_agent_teams_protocol which checks agent_teams_enabled
        self.agent_teams_enabled = True

    @property
    def client_profile(self) -> object:
        return self._profile
