"""PRD-CORE-295 FR08: every agent-side judgment id (§4.1 class A) is named once, as a class, in the
trw-assess skill, and that skill ships only when assess_enabled is set."""

from __future__ import annotations

from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "skills" / "trw-assess" / "SKILL.md"

#: PRD-CORE-295 §4.1 class A, grouped by the skill heading that covers each id.
CLASS_A: dict[str, tuple[str, ...]] = {
    "Phase transitions": ("JEV-065", "JEV-066", "JEV-067", "JEV-093", "JEV-106"),
    "PRD authoring": ("JEV-069", "JEV-070", "JEV-071", "JEV-073"),
    "Using a learning": ("JEV-075", "JEV-076", "JEV-077"),
    "Review triage": ("JEV-086", "JEV-088", "JEV-090", "JEV-095", "JEV-099", "JEV-121", "JEV-122"),
    "Execution": ("JEV-091", "JEV-092", "JEV-104", "JEV-105", "JEV-125", "JEV-126", "JEV-139", "JEV-140"),
    "Review scoping": ("JEV-128", "JEV-129", "JEV-132"),
    "Messages": ("JEV-C01", "JEV-C02", "JEV-C03", "JEV-C04", "JEV-C06"),
}


def test_every_class_a_id_maps_to_a_class_named_in_the_skill() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    missing = [heading for heading in CLASS_A if f"**{heading}**" not in text]
    assert missing == []
    ids = [i for group in CLASS_A.values() for i in group]
    assert len(ids) == len(set(ids)) == 35


def test_the_skill_ships_only_when_assess_is_enabled() -> None:
    from trw_mcp.bootstrap._optional_skills import CONDITIONAL_SKILLS

    assert CONDITIONAL_SKILLS.get("trw-assess") == "assess_enabled"
