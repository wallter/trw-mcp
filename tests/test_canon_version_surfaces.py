"""Governed version-surface classification (PRD-INFRA-164 FR11 / D-25 / D-26)."""

from __future__ import annotations

import json

import pytest

from trw_mcp.canons import registry as reg
from trw_mcp.canons._errors import CanonErrorCode, CanonRegistryError
from trw_mcp.canons._loader import parse_registry
from trw_mcp.canons._models import SurfaceUsage
from trw_mcp.canons._views import current_default_surfaces

# The bounded D-25 governed documentation set named in the PRD.
_GOVERNED_D25 = {
    "docs/TRW-COMPREHENSIVE-GUIDE.md",
    "docs/documentation/INDEX.md",
    "docs/documentation/agent-guide.md",
    "docs/documentation/architecture-overview.md",
    "docs/documentation/prd-implementation-status.md",
}


def test_d23_d25_d26_current_historical_migration_and_classification() -> None:
    reg.clear_cache()
    registry = reg.load_registry()
    surfaces = {s.id: s for s in registry.version_surfaces}

    # D-25: every governed doc is registered and classified (version-agnostic by decision).
    governed_paths = {s.path for s in registry.version_surfaces}
    assert _GOVERNED_D25 <= governed_paths
    for s in registry.version_surfaces:
        if s.path in _GOVERNED_D25:
            assert s.usage is SurfaceUsage.VERSION_AGNOSTIC
            assert not s.is_current_authority

    # D-26: installer-meta selectors are historical_install_snapshot and NEVER current authority.
    installer = [s for s in registry.version_surfaces if s.path == ".trw/installer-meta.yaml"]
    assert installer, "installer snapshot must be a governed surface"
    for s in installer:
        assert s.usage is SurfaceUsage.HISTORICAL_INSTALL_SNAPSHOT
        assert not s.is_current_authority
    # The legacy install-time selector names are explicitly install-time (not current).
    assert {s.selector for s in installer} == {
        "framework_version_at_install",
        "aaref_version_at_install",
    }

    # No current_default surface currently exists (all governed guides are version-agnostic),
    # so there is nothing that must equal the registry version — but the accessor works.
    assert current_default_surfaces(registry) == ()
    assert "comprehensive_guide" in surfaces


def test_unknown_usage_is_rejected_so_no_occurrence_is_unclassified() -> None:
    data = json.loads(reg.bundled_manifest_bytes().decode("utf-8"))
    data["version_surfaces"].append({"id": "bogus", "path": "docs/x.md", "selector": None, "usage": "made_up"})
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.UNSUPPORTED_USAGE


def test_current_default_surface_classification_is_available_for_future_guides() -> None:
    # Mechanism check: a seeded current_default surface is reported as current-authority.
    data = json.loads(reg.bundled_manifest_bytes().decode("utf-8"))
    data["version_surfaces"].append(
        {"id": "seeded_current", "path": "docs/current.md", "selector": "framework_version", "usage": "current_default"}
    )
    registry = parse_registry(json.dumps(data).encode("utf-8"))
    current = current_default_surfaces(registry)
    assert {s.id for s in current} == {"seeded_current"}
    assert current[0].is_current_authority


def test_historical_record_requires_selector_value_and_rationale() -> None:
    data = json.loads(reg.bundled_manifest_bytes().decode("utf-8"))
    data["version_surfaces"].append(
        {"id": "history", "path": "docs/history.md", "selector": None, "usage": "historical_record"}
    )
    with pytest.raises(CanonRegistryError) as exc:
        parse_registry(json.dumps(data).encode("utf-8"))
    assert exc.value.code is CanonErrorCode.MISSING_FIELD


def test_historical_record_preserves_explicit_value_and_rationale() -> None:
    data = json.loads(reg.bundled_manifest_bytes().decode("utf-8"))
    data["version_surfaces"].append(
        {
            "id": "history",
            "path": "docs/history.md",
            "selector": "framework_version",
            "usage": "historical_record",
            "expected_value": "v25_TRW",
            "rationale": "2026-06-10 release record",
        }
    )
    registry = parse_registry(json.dumps(data).encode("utf-8"))
    history = next(surface for surface in registry.version_surfaces if surface.id == "history")
    assert history.expected_value == "v25_TRW"
    assert history.rationale == "2026-06-10 release record"
