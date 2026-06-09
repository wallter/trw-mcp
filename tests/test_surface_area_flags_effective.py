"""Tests for effective_* surface area properties on TRWConfig."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._sub_models import ToolsConfig


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


@pytest.mark.unit
def test_effective_tool_exposure_mode_default() -> None:
    """Default config (None sentinel) with claude-code profile -> 'all' from profile."""
    cfg = TRWConfig()
    assert cfg.tool_exposure_mode is None  # None = "not explicitly set"
    assert cfg.effective_tool_exposure_mode == "all"  # claude-code profile default


@pytest.mark.unit
def test_effective_tool_exposure_mode_explicit_core() -> None:
    """Explicit tool_exposure_mode='core' overrides profile."""
    cfg = TRWConfig(tool_exposure_mode="core")
    assert cfg.effective_tool_exposure_mode == "core"


@pytest.mark.unit
def test_effective_tool_exposure_mode_opencode_profile() -> None:
    """None sentinel + opencode profile -> 'standard' (profile governs when not set)."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.tool_exposure_mode is None  # None = "not explicitly set"
    assert cfg.client_profile.tool_exposure_mode == "standard"
    assert cfg.effective_tool_exposure_mode == "standard"


@pytest.mark.unit
def test_effective_tool_exposure_mode_explicit_all_overrides_profile() -> None:
    """Explicit tool_exposure_mode='all' wins over opencode profile's 'standard'."""
    cfg = TRWConfig(tool_exposure_mode="all", target_platforms=["opencode"])
    assert cfg.tool_exposure_mode == "all"
    assert cfg.client_profile.tool_exposure_mode == "standard"
    assert cfg.effective_tool_exposure_mode == "all"  # explicit 'all' wins


@pytest.mark.unit
def test_effective_tool_exposure_mode_explicit_overrides_opencode() -> None:
    """Explicit tool_exposure_mode='minimal' with opencode -> 'minimal'."""
    cfg = TRWConfig(tool_exposure_mode="minimal", target_platforms=["opencode"])
    assert cfg.effective_tool_exposure_mode == "minimal"


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


@pytest.mark.unit
def test_effective_agents_enabled_default() -> None:
    """Default config (None) -> True (no profile field yet, default enabled)."""
    cfg = TRWConfig()
    assert cfg.agents_enabled is None
    assert cfg.effective_agents_enabled is True


@pytest.mark.unit
def test_effective_agents_enabled_explicit_false() -> None:
    """Explicit agents_enabled=False overrides the default."""
    cfg = TRWConfig(agents_enabled=False)
    assert cfg.effective_agents_enabled is False


@pytest.mark.unit
def test_effective_agents_enabled_explicit_true() -> None:
    """Explicit agents_enabled=True with any profile -> True."""
    cfg = TRWConfig(agents_enabled=True, target_platforms=["opencode"])
    assert cfg.effective_agents_enabled is True


@pytest.mark.unit
def test_effective_framework_ref_enabled_default() -> None:
    """Default config (None) with claude-code profile -> True (profile default)."""
    cfg = TRWConfig()
    assert cfg.framework_md_enabled is None
    assert cfg.client_profile.include_framework_ref is True
    assert cfg.effective_framework_ref_enabled is True


@pytest.mark.unit
def test_effective_framework_ref_enabled_explicit_false() -> None:
    """Explicit framework_md_enabled=False overrides profile default."""
    cfg = TRWConfig(framework_md_enabled=False)
    assert cfg.effective_framework_ref_enabled is False


@pytest.mark.unit
def test_effective_framework_ref_enabled_explicit_true() -> None:
    """Explicit framework_md_enabled=True overrides any profile."""
    cfg = TRWConfig(framework_md_enabled=True)
    assert cfg.effective_framework_ref_enabled is True


@pytest.mark.unit
def test_tools_sub_config_default() -> None:
    """config.tools returns ToolsConfig with sentinel None when not explicitly set."""
    cfg = TRWConfig()
    tools = cfg.tools
    assert isinstance(tools, ToolsConfig)
    assert tools.tool_exposure_mode is None  # None = not explicitly set; profile governs
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


@pytest.mark.unit
def test_nudge_density_default_is_none() -> None:
    """Default config + claude-code profile -> None (no profile opts in today)."""
    cfg = TRWConfig()
    assert cfg.nudge_density is None
    assert cfg.client_profile.nudge_density is None
    assert cfg.effective_nudge_density is None


@pytest.mark.unit
@pytest.mark.parametrize("density", ["low", "medium", "high"])
def test_nudge_density_explicit_override_wins(density: str) -> None:
    """Explicit TRWConfig.nudge_density overrides profile default."""
    cfg = TRWConfig(nudge_density=density)
    assert cfg.effective_nudge_density == density


@pytest.mark.unit
def test_nudge_density_none_override_falls_back_to_profile() -> None:
    """None TRWConfig override + opencode profile (also None) -> stays None."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.nudge_density is None
    assert cfg.client_profile.nudge_density is None
    assert cfg.effective_nudge_density is None
