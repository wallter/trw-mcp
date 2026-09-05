"""The standalone installer must MERGE `.trw/frameworks/VERSION.yaml`, not rewrite it.

Fresh-install regression (L-QhRy): `install-trw.py --script` ran
`_write_version_yaml_metadata` after `init-project`, and the helper rewrote the
whole stamp with four keys. That destroyed the `registry_digest` /
`framework_digest` / `aaref_digest` fields the installed package had just
written, so a brand-new install reported a deployment stamp with no registry
digest (`needs_upgrade`) — and, while the stamp was still receipt-bound, a hard
`framework_integrity` FAIL that blocked the release gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._install_trw_pip_target_contract_support import _INSTALLER_PATHS, _load_installer_module

_FRAMEWORK_BODY = "v26.2_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK\n"
_AAREF_BODY = "# AARE-F\n\n**Version**: 3.2.1\n"
_REGISTRY_DIGEST = "fc7ff6da2b8c2a3d8bbc023f14ffd0dcfbfe6c1a26114b155a25acb265b75617"


def _seed_deployment(target: Path, stamp: str | None) -> Path:
    frameworks = target / ".trw" / "frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "FRAMEWORK.md").write_text(_FRAMEWORK_BODY, encoding="utf-8")
    (frameworks / "AARE-F-FRAMEWORK.md").write_text(_AAREF_BODY, encoding="utf-8")
    version_path = frameworks / "VERSION.yaml"
    if stamp is not None:
        version_path.write_text(stamp, encoding="utf-8")
    return version_path


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_version_stamp_refresh_preserves_generation_binding_fields(installer_path: Path, tmp_path: Path) -> None:
    module = _load_installer_module(installer_path)
    version_path = _seed_deployment(
        tmp_path,
        "framework_version: v26.2_TRW\n"
        "aaref_version: v3.2.1\n"
        "trw_mcp_version: 1.0.0\n"
        f"registry_digest: {_REGISTRY_DIGEST}\n"
        "framework_digest: aaaa\n"
        "aaref_digest: bbbb\n"
        "deployed_at: '2026-01-01T00:00:00+00:00'\n",
    )

    module._write_version_yaml_metadata(tmp_path)

    stamp = version_path.read_text(encoding="utf-8")
    assert f"registry_digest: {_REGISTRY_DIGEST}" in stamp
    assert "framework_digest: aaaa" in stamp
    assert "aaref_digest: bbbb" in stamp
    # The installer is authoritative only for the version it just installed.
    assert f"trw_mcp_version: {module.TRW_VERSION}" in stamp
    assert "trw_mcp_version: 1.0.0" not in stamp
    assert "deployed_at: '2026-01-01T00:00:00+00:00'" not in stamp
    # Exactly one line per key — a merge must never duplicate a field.
    for field in ("framework_version", "aaref_version", "trw_mcp_version", "registry_digest", "deployed_at"):
        assert sum(1 for line in stamp.splitlines() if line.startswith(f"{field}:")) == 1


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_version_stamp_refresh_creates_stamp_when_absent(installer_path: Path, tmp_path: Path) -> None:
    module = _load_installer_module(installer_path)
    version_path = _seed_deployment(tmp_path, None)

    module._write_version_yaml_metadata(tmp_path)

    stamp = version_path.read_text(encoding="utf-8")
    assert "framework_version: v26.2_TRW" in stamp
    assert "aaref_version: v3.2.1" in stamp
    assert f"trw_mcp_version: {module.TRW_VERSION}" in stamp


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_version_stamp_refresh_is_skipped_when_bodies_are_undeployed(installer_path: Path, tmp_path: Path) -> None:
    """No deployed bodies means no honest version to stamp — leave the file alone."""
    module = _load_installer_module(installer_path)
    (tmp_path / ".trw" / "frameworks").mkdir(parents=True)
    version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
    version_path.write_text("framework_version: v26.2_TRW\nregistry_digest: keep-me\n", encoding="utf-8")

    module._write_version_yaml_metadata(tmp_path)

    assert version_path.read_text(encoding="utf-8") == "framework_version: v26.2_TRW\nregistry_digest: keep-me\n"
