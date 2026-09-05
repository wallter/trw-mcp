"""Packaged skill references must resolve to the current curated roster."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.bootstrap import PREDECESSOR_MAP

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SKILLS = ROOT / "trw-mcp/src/trw_mcp/data/skills"
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
    ROOT / "trw-mcp/src/trw_mcp/data/codex/skills",
    ROOT / "trw-mcp/src/trw_mcp/data/copilot/skills",
    ROOT / "trw-mcp/src/trw_mcp/data/copilot/plugin/skills",
    ROOT / "trw-mcp/src/trw_mcp/data/opencode/skills",
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


def test_retired_skills_have_no_packaged_projection() -> None:
    """A skill retired from the install must not survive in anything that ships.

    Scoped to the packaged roots, matching this test's name. It previously also
    swept the dev-repo roots, which conflated two different things: `trw-release-verify`
    was deliberately retired from the *install* in v0.60.0 (`86d3543c37`) while
    remaining a live internal skill the operator invokes in this monorepo. Asserting
    over `.claude/skills` made "retired from the package" mean "must not exist
    anywhere", so the only way to satisfy it was to delete a working tool.
    """
    retired = {name for name, successor in PREDECESSOR_MAP["skills"].items() if successor is None}
    for root in PACKAGED_SKILL_ROOTS:
        if not root.is_dir():
            continue
        for name in retired:
            assert not (root / name).exists(), f"retired skill projection remains: {root / name}"


def test_no_shipped_surface_references_a_retired_skill() -> None:
    """A reference is broken when the referencing surface ships and the target does not.

    Checked per root rather than repo-wide, because the two cases differ. A
    PACKAGED skill naming a retired command is broken for every user, since they
    will not have it. A DEV-ONLY skill naming another dev-only skill resolves
    fine in this monorepo — `/trw-release` offering `/trw-release-verify` as an
    opt-in gate is correct, and both live in `.claude/skills`. So a dev-root
    reference is a failure only when the target exists nowhere in that root.
    """
    retired = {name for name, successor in PREDECESSOR_MAP["skills"].items() if successor is None}

    for root in PACKAGED_SKILL_ROOTS:
        if not root.is_dir():
            continue
        for skill_path in root.glob("*/SKILL.md"):
            content = skill_path.read_text(encoding="utf-8")
            # A skill names its own command in its usage line — not a dangling reference.
            referenced = set(COMMAND_REFERENCE.findall(content)) - {skill_path.parent.name}
            assert retired.isdisjoint(referenced), (
                f"{skill_path} ships and references retired skills {retired & referenced}"
            )

    for root in DEV_SKILL_ROOTS:
        if not root.is_dir():
            continue
        for skill_path in root.glob("*/SKILL.md"):
            content = skill_path.read_text(encoding="utf-8")
            referenced = set(COMMAND_REFERENCE.findall(content)) - {skill_path.parent.name}
            unresolvable = {name for name in retired & referenced if not (root / name).is_dir()}
            assert not unresolvable, f"{skill_path}: references skills that exist nowhere: {unresolvable}"

    source_roots = (ROOT / "trw-mcp/src/trw_mcp",)
    for source_root in source_roots:
        if not source_root.is_dir():
            continue
        for source_path in source_root.rglob("*.py"):
            referenced = set(COMMAND_REFERENCE.findall(source_path.read_text(encoding="utf-8")))
            assert retired.isdisjoint(referenced), f"{source_path}: references retired skills {retired & referenced}"


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
