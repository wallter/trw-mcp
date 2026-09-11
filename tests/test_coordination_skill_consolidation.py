"""Installed coordination entry points retain one planning contract, not two."""

from pathlib import Path

import pytest

from trw_mcp.bootstrap._codex import install_codex_skills
from trw_mcp.bootstrap._init_project_skills import _install_skills


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_installed_alias_resolves_plan_only_owner(tmp_path: Path, client: str) -> None:
    if client == "codex":
        result = install_codex_skills(tmp_path)
        root = tmp_path / ".agents/skills"
    else:
        result = {"created": [], "skipped": [], "errors": []}
        _install_skills(tmp_path, force=False, result=result)
        root = tmp_path / ".claude/skills"
    assert not result["errors"]
    alias = root / "trw-team-playbook/SKILL.md"
    text = alias.read_text()
    reference = "../trw-sprint-team/SKILL.md"
    assert reference in text
    owner = (alias.parent / reference).resolve()
    assert owner.is_relative_to(tmp_path)
    canonical = owner.read_text()
    assert "original input" in text and "planning-only" in text
    assert "If the sibling contract is unavailable" in text
    assert "do not launch helpers, initialize a formation" in text
    assert "## Planning contract" not in text  # only one procedure owner
    for obligation in (
        "First decide whether coordination is needed",
        "no workstream split is",
        "stop before the decomposition below",
        "Default is planning only",
        "A sprint document is optional",
        "repo-detected layout",
        "exclusive source/config/docs and test paths",
        "Every shared\n   interface has one writer",
        "acceptance\nmethods and verification commands",
        "The brief does not\ncontain those task-specific instructions",
        "Do not require a separately maintained playbook per worker",
    ):
        assert obligation in canonical


def test_bundle_and_development_coordination_projections_agree() -> None:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".agents/skills").is_dir():
        pytest.skip("development projection check requires monorepo")
    data = root / "trw-mcp/src/trw_mcp/data"
    for name in ("trw-sprint-team", "trw-team-playbook"):
        expected = (data / "skills" / name / "SKILL.md").read_bytes()
        for projection in (data / "codex/skills", root / ".claude/skills", root / ".agents/skills"):
            assert (projection / name / "SKILL.md").read_bytes() == expected
    assert not (data / "playbook-template.yaml").exists()
    assert (data / "formation-brief-template.md").is_file()
