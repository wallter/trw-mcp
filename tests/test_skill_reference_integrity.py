"""Packaged skill references must resolve to the current curated roster."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
CANONICAL_SKILLS = PACKAGE_ROOT / "src/trw_mcp/data/skills"
COMMAND_REFERENCE = re.compile(r"(?<![\w./-])/(trw-[a-z0-9-]+)(?=$|[\s`\"',.;:!?()\[\]{}<>])")


def test_packaged_command_references_resolve_to_canonical_skills() -> None:
    skill_names = {path.name for path in CANONICAL_SKILLS.iterdir() if path.is_dir()}
    for skill_path in CANONICAL_SKILLS.glob("*/SKILL.md"):
        for command in COMMAND_REFERENCE.findall(skill_path.read_text(encoding="utf-8")):
            assert command in skill_names, f"{skill_path}: dead command reference /{command}"


#: Roots whose contents SHIP. A retired skill surviving in one of these reaches
#: user projects, which is what retirement is meant to prevent.
PACKAGED_SKILL_ROOTS = (
    CANONICAL_SKILLS,
    PACKAGE_ROOT / "src/trw_mcp/data/codex/skills",
    PACKAGE_ROOT / "src/trw_mcp/data/copilot/skills",
    PACKAGE_ROOT / "src/trw_mcp/data/copilot/plugin/skills",
    PACKAGE_ROOT / "src/trw_mcp/data/opencode/skills",
)

#: This monorepo's OWN client configuration. Bootstrap installs *into* these
#: from the packaged roots, so a skill present only here ships to nobody — it is
#: an internal tool for working on TRW itself. `scripts/check-bundle-sync.sh`
#: already models this, reporting such entries as `DEV-ONLY (not in bundled)`.
DEV_SKILL_ROOTS = (
    ROOT / ".agents/skills",
    ROOT / ".claude/skills",
    ROOT / ".cursor/skills",
    ROOT / ".github/skills",
)


def _canonical() -> set[str]:
    return {path.name for path in CANONICAL_SKILLS.iterdir() if path.is_dir()}


def test_retired_skills_have_no_packaged_projection() -> None:
    """A skill retired from the install must not survive in anything that ships.

    REMOVE-S8a: "retired" is no longer a name list but the predicate the sweep uses: a ``trw-*`` projection
    whose name is not a canonical skill. Scoped to the packaged roots, matching this test's name: the dev
    roots legitimately hold internal skills (``trw-release-verify``) that were retired from the install only.
    """
    canonical = _canonical()
    for root in PACKAGED_SKILL_ROOTS[1:]:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if entry.name.startswith("trw-"):
                assert entry.name in canonical, f"projection of a non-canonical (retired) skill: {entry}"


def test_no_shipped_surface_references_a_retired_skill() -> None:
    """A reference is broken when the referencing surface ships and the target does not.

    Checked per root rather than repo-wide, because the two cases differ. A
    PACKAGED skill naming a non-canonical command is broken for every user, since
    they will not have it. A DEV-ONLY skill naming another dev-only skill resolves
    fine in this monorepo — `/trw-release` offering `/trw-release-verify` as an
    opt-in gate is correct, and both live in `.claude/skills`. So a dev-root
    reference is a failure only when the target exists nowhere in that root.
    """
    canonical = _canonical()

    for root in PACKAGED_SKILL_ROOTS:
        if not root.is_dir():
            continue
        for skill_path in root.glob("*/SKILL.md"):
            content = skill_path.read_text(encoding="utf-8")
            # A skill names its own command in its usage line — not a dangling reference.
            referenced = set(COMMAND_REFERENCE.findall(content)) - {skill_path.parent.name}
            assert referenced <= canonical, (
                f"{skill_path} ships and references non-canonical skills {referenced - canonical}"
            )

    for root in DEV_SKILL_ROOTS:
        if not root.is_dir():
            continue
        for skill_path in root.glob("*/SKILL.md"):
            content = skill_path.read_text(encoding="utf-8")
            referenced = set(COMMAND_REFERENCE.findall(content)) - {skill_path.parent.name}
            unresolvable = {name for name in referenced - canonical if not (root / name).is_dir()}
            assert not unresolvable, f"{skill_path}: references skills that exist nowhere: {unresolvable}"

    # Source may also name a shipped opencode command (the distill channel's) or the server binary in a path.
    from trw_mcp.channels.opencode._custom_commands import opencode_distill_command_contents

    commands = {Path(key).stem for key in opencode_distill_command_contents()}
    commands |= {path.stem for path in (PACKAGE_ROOT / "src/trw_mcp/data/opencode/commands").glob("*.md")}
    resolvable = canonical | commands | {"trw-mcp"}
    source_roots = (PACKAGE_ROOT / "src/trw_mcp",)
    for source_root in source_roots:
        if not source_root.is_dir():
            continue
        for source_path in source_root.rglob("*.py"):
            referenced = set(COMMAND_REFERENCE.findall(source_path.read_text(encoding="utf-8")))
            assert referenced <= resolvable, f"{source_path}: references non-canonical skills {referenced - resolvable}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Run /trw-review-pr", {"trw-review-pr"}),
        ("Use `/trw-audit PRD-1`.", {"trw-audit"}),
        ("command: '/trw-prd-ready'", {"trw-prd-ready"}),
        (".agents/skills/trw-review-pr/SKILL.md", set()),
        ("trw-mcp/data/skills/trw-audit", set()),
    ],
)
def test_command_reference_detection_respects_path_boundaries(text: str, expected: set[str]) -> None:
    assert set(COMMAND_REFERENCE.findall(text)) == expected
