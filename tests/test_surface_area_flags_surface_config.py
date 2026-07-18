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


@pytest.mark.unit
def test_surface_config_explicit_overrides_profile() -> None:
    """Explicit config values override profile defaults in SurfaceConfig."""
    cfg = TRWConfig(
        target_platforms=["opencode"],
        nudge_enabled=True,
    )
    surfaces = cfg.surfaces
    assert surfaces.nudge.enabled is True


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


@pytest.mark.unit
def test_prd_core_218_nfr04() -> None:
    """NFR04: the public-surface reduction targets (36 tools / 23 skills / 370
    fields) are NOT yet met; completion is honest ONLY because each missed metric
    carries a distinct, unexpired, operator-approved expiring exception. We assert
    the real overage AND its covering exception — the targets are never faked."""
    from datetime import datetime, timezone

    from trw_mcp.bootstrap._init_project_skills import _data_dir
    from trw_mcp.server._surface_manifest_registry import (
        SURFACE_REDUCTION_EXCEPTIONS,
        SURFACE_REDUCTION_TARGETS,
        TOOL_MANIFEST,
        reduction_exception_active,
        surface_reduction_census,
    )

    # Live census — never hardcoded numbers: the registered tool surface, the
    # bundled skill dirs, and TRWConfig top-level fields.
    tool_count = len(TOOL_MANIFEST)
    skills_dir = _data_dir() / "skills"
    skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists())
    config_field_count = len(TRWConfig.model_fields)

    census = surface_reduction_census(
        tool_count=tool_count,
        skill_count=skill_count,
        config_field_count=config_field_count,
    )
    assert set(census) == set(SURFACE_REDUCTION_TARGETS) == {"tools", "skills", "config_fields"}

    # Every metric currently MISSES its target (the honest state of the world).
    assert census["tools"].current == tool_count > SURFACE_REDUCTION_TARGETS["tools"]
    assert census["skills"].current == skill_count > SURFACE_REDUCTION_TARGETS["skills"]
    assert census["config_fields"].current == config_field_count > SURFACE_REDUCTION_TARGETS["config_fields"]

    for metric, status in census.items():
        # No silent pass: a missed metric is "honest" only with an active exception.
        assert status.met is False, metric
        assert status.exception_active is True, metric
        assert status.reported_honestly is True, metric
        # A distinct, complete exception record exists per miss.
        exc = SURFACE_REDUCTION_EXCEPTIONS[metric]
        assert exc.metric == metric
        assert exc.target == SURFACE_REDUCTION_TARGETS[metric]
        assert exc.owner and exc.rationale and exc.reduction_plan_ref and exc.expiry_iso
        assert reduction_exception_active(metric) is True

    # Anti-fakery: an unknown metric has no exception and cannot pass honestly.
    assert reduction_exception_active("nonexistent") is False

    # Anti-fakery: once the exceptions expire the SAME overage is reported
    # DISHONESTLY (met=False AND exception_active=False) rather than silently
    # passing — proving the census does not fake meeting the targets.
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    expired = surface_reduction_census(
        tool_count=tool_count,
        skill_count=skill_count,
        config_field_count=config_field_count,
        now=far_future,
    )
    assert all(s.exception_active is False for s in expired.values())
    assert all(s.reported_honestly is False for s in expired.values())
