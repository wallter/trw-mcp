"""Installed Codex readiness resources, configuration and preservation."""

from pathlib import Path

import pytest
import tomllib

from tests._layout import requires_monorepo
from trw_mcp.bootstrap._codex import generate_codex_config, install_codex_skills


@pytest.mark.parametrize("legacy_enabled", [None, False, True])
def test_readiness_phase_resources_in_nongit_install(tmp_path, legacy_enabled):
    from trw_mcp.bootstrap._client_skills import canonical_skills_dir, render_skill_md

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
        canonical_text = (canonical_skills_dir() / name / "SKILL.md").read_text(encoding="utf-8")
        assert resource.read_bytes() == render_skill_md(canonical_text, "codex").encode("utf-8")
        # CANONICAL-SKILL CONTENT GAP CLOSED (PRD-CORE-291-FR04): the canonical
        # body (src/trw_mcp/data/skills/trw-prd-ready/SKILL.md) now names each
        # sibling contract by its installed filename as one of three equally
        # valid resolution paths ("the packaged internal `trw-prd-groom`
        # contract (the `trw-prd-groom` skill, or `trw-prd-groom-contract.md`
        # beside this skill) (inline if unavailable)", lines ~141/163/207). A
        # codex agent reading trw-prd-ready/SKILL.md now has a textual pointer
        # to the sibling files this same install call materializes beside it.
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
    """Repo-root mirror parity: expected to fail until mirrors are regenerated.

    Codex no longer forks these skills on disk (PRD-CORE-291-FR04); the
    canonical source plus its codex rendering replaces the deleted
    ``data/codex/skills`` fork as the comparison basis.
    """
    from trw_mcp.bootstrap._client_skills import canonical_skills_dir, render_skill_md

    root = Path(__file__).resolve().parents[2]
    for name in ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        canonical_text = (canonical_skills_dir() / name / "SKILL.md").read_text(encoding="utf-8")
        expected = render_skill_md(canonical_text, "codex").encode("utf-8")
        resource = root / ".agents/skills/trw-prd-ready" / f"{name}-contract.md"
        assert resource.read_bytes() == expected
