"""Compatibility projections remain registry-derived and reversible (INFRA-164 NFR06)."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.bootstrap import _DATA_FILE_MAP
from trw_mcp.bootstrap._template_updater import _ALWAYS_UPDATE
from trw_mcp.canons import registry as reg
from trw_mcp.canons._loader import parse_registry
from trw_mcp.canons._views import install_view


def test_legacy_views_are_registry_derived_and_reversible() -> None:
    registry = reg.load_registry()
    expected = set(install_view(registry))
    assert expected <= set(_DATA_FILE_MAP)
    assert expected <= set(_ALWAYS_UPDATE)

    managed_names = {artifact.package_resource for artifact in registry.artifacts}
    assert {pair for pair in _DATA_FILE_MAP if pair[0] in managed_names} == expected
    assert {pair for pair in _ALWAYS_UPDATE if pair[0] in managed_names} == expected

    target = {"path": "docs/FRAMEWORK.md", "role": "project_reference", "update_policy": "managed"}
    payload = json.loads(reg.bundled_manifest_bytes())
    payload["artifacts"][0]["install_targets"].append(target)
    migrated = parse_registry(json.dumps(payload).encode())
    assert ("framework.md", "docs/FRAMEWORK.md") in install_view(migrated)
    payload["artifacts"][0]["install_targets"].remove(target)
    reverted = parse_registry(json.dumps(payload).encode())
    assert install_view(reverted) == install_view(registry)


def test_bootstrap_consumers_have_no_literal_canon_install_map() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "bootstrap"
    for path in (root / "__init__.py", root / "_template_updater.py"):
        source = path.read_text(encoding="utf-8")
        assert '("framework.md", ".trw/frameworks/FRAMEWORK.md")' not in source
        assert '("aaref.md", ".trw/frameworks/AARE-F-FRAMEWORK.md")' not in source
