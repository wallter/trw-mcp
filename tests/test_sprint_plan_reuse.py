"""Verify delivered sprint instructions, not model adherence or claimed savings."""

from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "src/trw_mcp/data"


@pytest.mark.parametrize("client", ["claude", "codex", "copilot", "cursor"])
def test_native_installer_delivers_sprint_plan_reuse(tmp_path: Path, client: str) -> None:
    if client == "claude":
        from trw_mcp.bootstrap._init_project_skills import _install_skills

        result = {"created": [], "skipped": [], "errors": []}
        _install_skills(tmp_path, force=False, result=result)
        relative = ".claude/skills/trw-sprint-init/SKILL.md"
        source = "skills/trw-sprint-init/SKILL.md"
    elif client == "codex":
        from trw_mcp.bootstrap._codex import install_codex_skills

        result = install_codex_skills(tmp_path)
        relative = ".agents/skills/trw-sprint-init/SKILL.md"
        source = "codex/skills/trw-sprint-init/SKILL.md"
    elif client == "copilot":
        from trw_mcp.bootstrap._copilot_artifacts import install_copilot_skills

        result = install_copilot_skills(tmp_path)
        relative = ".github/skills/trw-sprint-init/SKILL.md"
        source = "copilot/skills/trw-sprint-init/SKILL.md"
    else:
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_commands

        result = generate_cursor_ide_commands(tmp_path)
        relative = ".cursor/commands/trw-sprint-init.md"
        source = "cursor_ide/commands/trw-sprint-init.md"
    if client == "cursor":
        assert relative in result["created"]
    else:
        assert not result["errors"]
    delivered = (tmp_path / relative).read_bytes()
    assert delivered == (DATA / source).read_bytes()
    text = delivered.decode()
    for contract in (
        "Ordinary work does not require a sprint",
        "path and task IDs or section anchors",
        "not recreate per-PRD tasks, status, ownership, dependencies, or verification commands",
        "cross-PRD ordering",
        "integration ownership",
        "aggregate/manual acceptance",
        "existing governing artifact and readiness workflow",
        "Preserve project-required separate formats and existing sprint artifacts",
        "do not auto-migrate them",
        "keep per-PRD progress in its governing artifact",
    ):
        assert contract.lower() in text.lower(), (client, contract)
    assert "tracks:\n" not in text
    assert "owned_paths: [<path>]" not in text
    if client == "cursor":
        assert "before scheduling them as ready; selection grants no approval" in text
        assert "Surface conflicts rather than overriding authority" in text
    else:
        assert "before scheduling that work as ready" in text
        assert "Sprint selection does not grant approval" in text
        assert "Surface conflicting plans or ownership for resolution" in text
        assert "rather than silently choosing or overwriting an authority" in text


def test_development_and_plugin_sprint_projections_match_their_owner() -> None:
    root = Path(__file__).resolve().parents[2]
    for local, bundled in (
        (".agents/skills", "codex/skills"),
        (".claude/skills", "skills"),
    ):
        local_file = root / local / "trw-sprint-init/SKILL.md"
        if local_file.is_file():
            assert local_file.read_bytes() == (DATA / bundled / "trw-sprint-init/SKILL.md").read_bytes()
    assert (DATA / "copilot/plugin/skills/trw-sprint-init/SKILL.md").read_bytes() == (
        DATA / "copilot/skills/trw-sprint-init/SKILL.md"
    ).read_bytes()
