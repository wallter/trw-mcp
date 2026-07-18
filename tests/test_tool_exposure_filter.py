"""Removal test: the PRD-CORE-125 tool-exposure authority cannot win.

PRD-CORE-218 FR03/FR04 activation made the kernel/pack resolver
(``SurfaceAuthorityMiddleware``) the SOLE tool-exposure authority. The former
CORE-125 preset filter (``_apply_tool_exposure_filter`` + ``TOOL_PRESETS`` +
``tool_exposure_mode``/``tool_exposure_list``) was removed in the SAME boundary.
This test proves the old path is gone and a legacy config value can no longer
alter the exposed surface (no dormant second authority).
"""

from __future__ import annotations


def test_apply_tool_exposure_filter_symbol_is_gone() -> None:
    """The CORE-125 boot-time preset filter no longer exists in server._tools."""
    import trw_mcp.server._tools as tools_mod

    assert not hasattr(tools_mod, "_apply_tool_exposure_filter")


def test_tool_presets_removed_from_defaults() -> None:
    """TOOL_PRESETS + its tool-group vocabulary are removed from _defaults."""
    import trw_mcp.models.config._defaults as defaults

    assert not hasattr(defaults, "TOOL_PRESETS")
    assert not hasattr(defaults, "TOOL_GROUP_CORE")
    assert not hasattr(defaults, "INTENTIONALLY_UNBRIDGED_TOOLS")


def test_legacy_config_field_ignored_and_surface_unchanged() -> None:
    """A legacy ``tool_exposure_mode`` value is ignored (BaseSettings extra=ignore)
    and does NOT alter the resolved surface — only ``tool_resolution_mode`` does.

    ``TRWConfig`` sets ``extra="ignore"``, so a stale config key is silently
    dropped rather than resurrecting the removed authority."""
    from trw_mcp.models.config import TRWConfig

    # Legacy fields no longer exist on the model.
    assert "tool_exposure_mode" not in TRWConfig.model_fields
    assert "tool_exposure_list" not in TRWConfig.model_fields

    # Passing the legacy key is ignored (no error, no attribute, no effect).
    cfg = TRWConfig(tool_exposure_mode="minimal")  # type: ignore[call-arg]
    assert not hasattr(cfg, "tool_exposure_mode")
    # The sole authority is tool_resolution_mode, still defaulting to standard —
    # the legacy value did not downgrade or otherwise change resolution.
    assert cfg.tool_resolution_mode == "standard"
    assert cfg.resolve_tool_surface_for_task("coding").mode == "standard"
    assert len(cfg.resolve_tool_surface_for_task("coding").tools) == 15


def test_effective_tool_exposure_mode_property_gone() -> None:
    """The CORE-125 profile-resolution property is removed from TRWConfig."""
    from trw_mcp.models.config import TRWConfig

    assert not hasattr(TRWConfig(), "effective_tool_exposure_mode")
