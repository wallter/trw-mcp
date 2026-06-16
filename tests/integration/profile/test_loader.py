"""FR-5 / FR-10 / FR-12 — loader + legacy-shim tests (filesystem).

Integration-tier: writes layer YAML files under tmp_path/profiles.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog.testing

from trw_mcp.profile import (
    LayerLoadError,
    discover_layers,
    load_layer,
    translate_legacy_client_profile,
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_org_layer_discovery_from_disk(tmp_path: Path) -> None:
    """FR-5: an existing org.yaml is loaded as the org layer."""
    _write(
        tmp_path / "profiles" / "org.yaml",
        "rationale: house rules\nreview_threshold: STANDARD\n",
    )
    layers = discover_layers(tmp_path)
    names = [layer.name for layer in layers]
    assert "org" in names
    org = next(layer for layer in layers if layer.name == "org")
    assert org.overrides.review_threshold == "STANDARD"
    assert org.rationale == "house rules"


def test_missing_org_file_excludes_org_from_layers_applied(tmp_path: Path) -> None:
    """FR-5: an absent org.yaml yields no org layer (empty overlay)."""
    layers = discover_layers(tmp_path)
    assert all(layer.name != "org" for layer in layers)


def test_domain_and_task_layers_discovered(tmp_path: Path) -> None:
    """FR-5/6/7: domain-{x}.yaml and task-{y}.yaml are discovered by name."""
    _write(tmp_path / "profiles" / "domain-frontend.yaml", "build_check_scope: targeted\n")
    _write(tmp_path / "profiles" / "task-bugfix.yaml", "review_threshold: COMPREHENSIVE\n")
    layers = discover_layers(tmp_path, domain="frontend", task_type="bugfix")
    names = {layer.name for layer in layers}
    assert {"domain", "task-type"} <= names


def test_malformed_yaml_fails_loudly_with_path(tmp_path: Path) -> None:
    """FR-12: malformed YAML raises LayerLoadError carrying the path."""
    bad = _write(tmp_path / "profiles" / "org.yaml", "review_threshold: [unclosed\n")
    with pytest.raises(LayerLoadError) as exc:
        load_layer("org", bad)
    assert str(bad) in str(exc.value)


def test_schema_failure_no_silent_fallback(tmp_path: Path) -> None:
    """FR-12: an unknown key fails closed, not silently to defaults."""
    bad = _write(tmp_path / "profiles" / "org.yaml", "totally_unknown_key: 1\n")
    with pytest.raises(LayerLoadError):
        load_layer("org", bad)


def test_missing_layer_returns_none(tmp_path: Path) -> None:
    """FR-5: a missing file returns None (caller skips the layer)."""
    assert load_layer("org", tmp_path / "profiles" / "nope.yaml") is None


def test_legacy_client_profile_shim_roundtrip() -> None:
    """FR-10: the bare 'cursor' id remaps to 'cursor-cli' as a client layer."""
    layer = translate_legacy_client_profile("cursor")
    assert layer.name == "client"
    assert "cursor-cli" in (layer.source_path or "")


def test_deprecation_log_emitted_on_legacy_key() -> None:
    """FR-10: remapping a legacy key emits a deprecation warning log."""
    with structlog.testing.capture_logs() as logs:
        translate_legacy_client_profile("cursor")
    assert any(entry["event"] == "profile_legacy_client_remap" for entry in logs)


def test_non_legacy_client_id_no_remap_log() -> None:
    """FR-10: a current client id does not emit the remap warning."""
    with structlog.testing.capture_logs() as logs:
        layer = translate_legacy_client_profile("claude-code")
    assert not any(entry["event"] == "profile_legacy_client_remap" for entry in logs)
    assert "claude-code" in (layer.source_path or "")
