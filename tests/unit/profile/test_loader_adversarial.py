"""ROUND-2 HARDENING — adversarial battery for the profile-layer loaders.

Surfaces:
  * ``trw_mcp.profile.loader.load_layer`` / ``discover_layers`` — persistent
    org/domain/task layers under ``.trw/profiles/`` (fail-CLOSED: a malformed
    persistent layer raises ``LayerLoadError`` per FR-12).

Behavior contract:
  * Persistent loader is FAIL-CLOSED — non-mapping roots, NaN/Inf floats,
    ceremony_tier-as-list, unknown keys all raise ``LayerLoadError`` (never a
    silent default).
  * Path containment (round-2 FIX): a ``domain`` / ``task_type`` carrying
    ``../`` or a symlink planted under ``.trw/profiles`` MUST NOT let
    ``load_layer`` read a file outside the profiles dir — it raises
    ``LayerLoadError``.
  * Session loader is FAIL-OPEN — a malformed / non-mapping / symlinked-to-
    garbage session file degrades to ``None`` (no usable layer, never a crash).

All tests assert the SAFE behavior and are kept as regression.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.profile.loader import LayerLoadError, discover_layers, load_layer


@pytest.fixture
def profiles(tmp_path: Path) -> Path:
    base = tmp_path / ".trw" / "profiles"
    base.mkdir(parents=True)
    return base


# --------------------------------------------------------------------------- #
# Persistent loader fail-CLOSED on malformed content.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        "- a\n- b\n",  # non-mapping list root
        "just a scalar string\n",  # non-mapping scalar root
        "42\n",  # non-mapping int root
    ],
)
def test_non_mapping_root_raises(profiles: Path, body: str) -> None:
    p = profiles / "domain-x.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(LayerLoadError):
        load_layer("domain", p, base_dir=profiles)


@pytest.mark.parametrize(
    "body",
    [
        "ceremony_tier: [A, B]\n",  # list where a scalar tier is expected
        "review_threshold: .nan\n",  # NaN float
        "review_threshold: .inf\n",  # Inf float
        "review_threshold: -.inf\n",
        "totally_unknown_key: 1\n",  # extra=forbid
    ],
)
def test_schema_invalid_layer_raises(profiles: Path, body: str) -> None:
    p = profiles / "domain-x.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(LayerLoadError):
        load_layer("domain", p, base_dir=profiles)


@pytest.mark.parametrize("body", ["", "~\n", "null\n"])
def test_empty_or_null_layer_is_empty_overlay(profiles: Path, body: str) -> None:
    # An empty/null doc is the legitimate empty overlay, not a malformed layer.
    p = profiles / "domain-x.yaml"
    p.write_text(body, encoding="utf-8")
    layer = load_layer("domain", p, base_dir=profiles)
    assert layer is not None and layer.name == "domain"


def test_absent_file_is_none(profiles: Path) -> None:
    assert load_layer("domain", profiles / "domain-missing.yaml", base_dir=profiles) is None


def test_deeply_nested_mapping_raises(profiles: Path) -> None:
    body = "a:\n" + "".join("  " * i + "k:\n" for i in range(1, 100))
    p = profiles / "domain-deep.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(LayerLoadError):
        load_layer("domain", p, base_dir=profiles)


# --------------------------------------------------------------------------- #
# PATH TRAVERSAL containment (round-2 FIX) — must not read outside profiles.
# --------------------------------------------------------------------------- #


def test_traversal_via_domain_name_is_blocked(tmp_path: Path, profiles: Path) -> None:
    # Plant a secret OUTSIDE the profiles dir; a domain with a ``../`` chain
    # whose intermediate segment exists would otherwise escape.
    secret = profiles.parent.parent / "secret.yaml"
    secret.write_text("ceremony_tier: COMPREHENSIVE\nrationale: ESCAPED\n", encoding="utf-8")
    (profiles / "domain-x").mkdir()
    escaping = profiles / "domain-x/../../../secret.yaml"
    with pytest.raises(LayerLoadError, match="escapes the profiles directory"):
        load_layer("domain", escaping, base_dir=profiles)


def test_symlink_out_of_profiles_is_blocked(tmp_path: Path, profiles: Path) -> None:
    secret = tmp_path / "outside_secret.yaml"
    secret.write_text("ceremony_tier: COMPREHENSIVE\nrationale: ESCAPED\n", encoding="utf-8")
    link = profiles / "domain-evil.yaml"
    os.symlink(secret, link)
    with pytest.raises(LayerLoadError, match="escapes the profiles directory"):
        load_layer("domain", link, base_dir=profiles)


def test_discover_layers_blocks_traversal_domain(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    profiles = trw_dir / "profiles"
    profiles.mkdir(parents=True)
    secret = tmp_path / "secret.yaml"
    secret.write_text("ceremony_tier: COMPREHENSIVE\nrationale: ESCAPED\n", encoding="utf-8")
    (profiles / "domain-x").mkdir()
    with pytest.raises(LayerLoadError):
        discover_layers(trw_dir, domain="x/../../../secret", task_type="generic")


def test_discover_layers_blocks_symlink_domain(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    profiles = trw_dir / "profiles"
    profiles.mkdir(parents=True)
    secret = tmp_path / "secret.yaml"
    secret.write_text("ceremony_tier: COMPREHENSIVE\n", encoding="utf-8")
    os.symlink(secret, profiles / "domain-aliased.yaml")
    with pytest.raises(LayerLoadError):
        discover_layers(trw_dir, domain="aliased", task_type="generic")


def test_contained_symlink_is_allowed(profiles: Path) -> None:
    # A symlink whose target stays INSIDE profiles is legitimate and allowed.
    real = profiles / "real-domain.yaml"
    real.write_text("ceremony_tier: MINIMAL\n", encoding="utf-8")
    os.symlink(real, profiles / "domain-alias.yaml")
    layer = load_layer("domain", profiles / "domain-alias.yaml", base_dir=profiles)
    assert layer is not None and layer.overrides.ceremony_tier == "MINIMAL"


def test_legit_org_layer_still_loads(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    profiles = trw_dir / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "org.yaml").write_text("ceremony_tier: STANDARD\n", encoding="utf-8")
    layers = discover_layers(trw_dir, domain="unknown", task_type="generic")
    assert [layer.name for layer in layers] == ["org"]
    assert layers[0].overrides.ceremony_tier == "STANDARD"


def test_load_layer_without_base_dir_does_not_enforce(profiles: Path) -> None:
    # Backward-compat: when base_dir is omitted, no containment check runs
    # (callers that pass a trusted path keep the old behavior).
    p = profiles / "org.yaml"
    p.write_text("ceremony_tier: STANDARD\n", encoding="utf-8")
    layer = load_layer("org", p)
    assert layer is not None
