"""Public-surface reduction census for PRD-CORE-218 NFR04."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig

# The ten ``SurfaceConfig`` tests that stood here went with the model in 2.0.0
# (WD-02). ``TRWConfig.surfaces`` was a projection no gate site read -- its only
# consumer was the resolver deleted alongside it -- so these asserted that a
# frozen model nobody consumed carried the right values. The profile-vs-explicit
# resolution they exercised is the ``effective_*`` properties, which ARE read at
# their gate sites and are covered in test_surface_area_flags_effective.py.
#
# ``test_prd_core_218_nfr04`` stays: it is the public-surface reduction census
# and never touched ``surfaces``. The file keeps its name so the PRD-CORE-218 and
# PRD-QUAL-126 references to it by path do not go stale.


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
