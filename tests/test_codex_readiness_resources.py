"""Installed Codex readiness resources, configuration and preservation."""

from pathlib import Path

import pytest
import tomllib

from tests._layout import requires_monorepo
from trw_mcp.bootstrap._codex import generate_codex_config, install_codex_skills


@pytest.mark.parametrize("legacy_enabled", [None, False, True])
def test_readiness_phase_resources_in_nongit_install(tmp_path, legacy_enabled):
    from trw_mcp.bootstrap._codex import _codex_skills_source_dir

    names = ("trw-prd-groom", "trw-prd-review", "trw-exec-plan")
    skills = tmp_path / ".agents" / "skills"
    if legacy_enabled is not None:
        config_dir = tmp_path / ".codex"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            f'[[skills.config]]\npath = ".agents/skills/trw-prd-groom"\nenabled = {str(legacy_enabled).lower()}\n'
        )
        legacy = skills / "trw-prd-groom" / "SKILL.md"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("Operator-maintained legacy body")
    result = install_codex_skills(tmp_path)
    assert not result["errors"]
    assert not generate_codex_config(tmp_path)["errors"]
    config = tomllib.loads((tmp_path / ".codex/config.toml").read_text())
    configured = {e["path"]: e["enabled"] for e in config["skills"]["config"]}
    ready = skills / "trw-prd-ready"
    for name in names:
        resource = ready / f"{name}-contract.md"
        assert resource.read_bytes() == (_codex_skills_source_dir() / name / "SKILL.md").read_bytes()
        assert f"{name}-contract.md" in (ready / "SKILL.md").read_text()
        if name == "trw-prd-groom" and legacy_enabled is not None:
            assert configured[f".agents/skills/{name}"] is legacy_enabled
            assert (skills / name / "SKILL.md").read_text() == "Operator-maintained legacy body"
            assert f".agents/skills/{name}/SKILL.md" in result["preserved"]
        else:
            assert not (skills / name).exists()
            assert f".agents/skills/{name}" not in configured
    assert not (tmp_path / ".git").exists()
    edited = ready / "trw-prd-review-contract.md"
    edited.write_text("Operator review customization")
    installed_again = install_codex_skills(tmp_path)
    assert edited.read_text() == "Operator review customization"
    assert ".agents/skills/trw-prd-ready/trw-prd-review-contract.md" in installed_again["preserved"]


@requires_monorepo
def test_repo_ready_resources_match_canonical_codex_bodies():
    root = Path(__file__).resolve().parents[2]
    for name in ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        source = root / "trw-mcp/src/trw_mcp/data/codex/skills" / name / "SKILL.md"
        resource = root / ".agents/skills/trw-prd-ready" / f"{name}-contract.md"
        assert resource.read_bytes() == source.read_bytes()
