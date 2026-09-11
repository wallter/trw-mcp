"""Restored optional commit skill: distribution and safety contract."""

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
PATHS = [
    ROOT / p / "trw-commit"
    for p in (
        ".agents/skills",
        ".claude/skills",
        "trw-mcp/src/trw_mcp/data/skills",
        "trw-mcp/src/trw_mcp/data/codex/skills",
    )
]


@requires_monorepo
def test_skill_copies_are_identical_and_concise():
    bodies = [(p / "SKILL.md").read_text() for p in PATHS]
    assert len(set(bodies)) == 1
    assert len(bodies[0].splitlines()) < 70
    for contract in (
        "only when the user asks",
        "does not add a mandatory run",
        "does **not**",
        "Do not push unless requested",
        "never invent authorship",
        "without separate authorization",
    ):
        assert contract in bodies[0]
    assert len({(p / "PR-TEMPLATE.md").read_text() for p in PATHS}) == 1


def test_package_skill_is_bundled():
    for p in (
        PACKAGE_ROOT / "src/trw_mcp/data/skills/trw-commit",
        PACKAGE_ROOT / "src/trw_mcp/data/codex/skills/trw-commit",
    ):
        assert (p / "SKILL.md").is_file()
        assert (p / "PR-TEMPLATE.md").is_file()


def test_update_does_not_retire_restored_or_custom_commit_skill():
    from trw_mcp.bootstrap._version_migration import PREDECESSOR_MAP

    renames = PREDECESSOR_MAP["skills"]
    assert "trw-commit" not in renames
    assert "commit" not in renames
