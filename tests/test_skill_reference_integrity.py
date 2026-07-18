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


def test_retired_skills_have_no_packaged_projection() -> None:
    retired = {name for name, successor in PREDECESSOR_MAP["skills"].items() if successor is None}
    projection_roots = (
        CANONICAL_SKILLS,
        ROOT / "trw-mcp/src/trw_mcp/data/codex/skills",
        ROOT / "trw-mcp/src/trw_mcp/data/copilot/skills",
        ROOT / "trw-mcp/src/trw_mcp/data/copilot/plugin/skills",
        ROOT / "trw-mcp/src/trw_mcp/data/opencode/skills",
        ROOT / ".agents/skills",
        ROOT / ".claude/skills",
        ROOT / ".cursor/skills",
        ROOT / ".github/skills",
        ROOT / "trw-eval/trw-mcp-local/src/trw_mcp/data/skills",
        ROOT / "trw-eval/trw-mcp-local/src/trw_mcp/data/codex/skills",
        ROOT / "trw-eval/trw-mcp-local/src/trw_mcp/data/opencode/skills",
    )
    for root in projection_roots:
        if not root.is_dir():
            continue
        for name in retired:
            assert not (root / name).exists(), f"retired skill projection remains: {root / name}"
        for skill_path in root.glob("*/SKILL.md"):
            content = skill_path.read_text(encoding="utf-8")
            referenced = set(COMMAND_REFERENCE.findall(content))
            assert retired.isdisjoint(referenced), f"{skill_path}: references retired skills {retired & referenced}"

    source_roots = (
        ROOT / "trw-mcp/src/trw_mcp",
        ROOT / "trw-eval/trw-mcp-local/src/trw_mcp",
    )
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
