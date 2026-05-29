"""SurfaceConfig-focused tests for TRWConfig surface area flags."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig


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

    assert surfaces.nudge.enabled is False
    assert surfaces.hooks_enabled is False
    assert surfaces.mcp_instructions_enabled is False
    assert surfaces.skills_enabled is False
    assert surfaces.recall.enabled is True
    assert surfaces.tool_exposure.mode == "standard"


@pytest.mark.unit
def test_surface_config_explicit_overrides_profile() -> None:
    """Explicit config values override profile defaults in SurfaceConfig."""
    cfg = TRWConfig(
        target_platforms=["opencode"],
        nudge_enabled=True,
        tool_exposure_mode="minimal",
    )
    surfaces = cfg.surfaces
    assert surfaces.nudge.enabled is True
    assert surfaces.tool_exposure.mode == "minimal"


@pytest.mark.unit
def test_surface_config_is_frozen() -> None:
    """SurfaceConfig and sub-models are frozen (immutable)."""
    cfg = TRWConfig()
    surfaces = cfg.surfaces

    with pytest.raises(Exception):
        surfaces.hooks_enabled = False

    with pytest.raises(Exception):
        surfaces.nudge.enabled = False


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
