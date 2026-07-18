"""Keep portable guidance aligned with project-owned coverage policy."""

from __future__ import annotations

import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
PACKAGE = DATA.parent
FORBIDDEN_DEFAULTS = (
    re.compile(r"coverage_threshold:\s*80\b", re.IGNORECASE),
    re.compile(r"threshold:\s*80%", re.IGNORECASE),
    re.compile(r"default to 80% if not specified", re.IGNORECASE),
    re.compile(r"80% as the TRW default recommendation", re.IGNORECASE),
    re.compile(r"target(?:s|ing)?\s*(?:>=|≥)?\s*90%[^\n]*coverage", re.IGNORECASE),
    re.compile(r"coverage target:\s*(?:>=|≥)\s*90%", re.IGNORECASE),
    re.compile(r"coverage:\s*global\s*(?:>=|≥)\s*85%,\s*diff\s*(?:>=|≥)\s*90%", re.IGNORECASE),
    re.compile(r"coverage\s*(?:>=|≥)\s*90%\s*on new code", re.IGNORECASE),
)


def test_packaged_guidance_does_not_invent_coverage_floors() -> None:
    """Coverage gates come from project config or explicit requirements, never TRW folklore."""
    failures: list[str] = []
    for path in sorted(DATA.rglob("*")):
        if path.suffix not in {".md", ".yaml", ".toml"}:
            continue
        content = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_DEFAULTS:
            for match in pattern.finditer(content):
                line = content.count("\n", 0, match.start()) + 1
                failures.append(f"{path.relative_to(DATA)}:{line}: {match.group(0)}")

    assert not failures, "packaged guidance invents universal coverage floors:\n" + "\n".join(failures)


def test_primary_coverage_workflows_name_the_no_threshold_behavior() -> None:
    """The two coverage-owning skills must say what to do when no floor exists."""
    for relative in (
        "skills/trw-sprint-finish/SKILL.md",
        "skills/trw-sprint-init/SKILL.md",
        "skills/trw-test-strategy/SKILL.md",
        "codex/skills/trw-sprint-finish/SKILL.md",
        "codex/skills/trw-sprint-init/SKILL.md",
        "codex/skills/trw-test-strategy/SKILL.md",
    ):
        content = (DATA / relative).read_text(encoding="utf-8").lower()
        assert "if no coverage threshold is configured" in content, relative
        assert "do not invent" in content, relative


def test_read_only_test_strategy_does_not_mutate_build_gate_state() -> None:
    """A focused audit must not overwrite validation evidence or advance the active run."""
    for relative in (
        "skills/trw-test-strategy/SKILL.md",
        "codex/skills/trw-test-strategy/SKILL.md",
        "opencode/skills/trw-test-strategy/SKILL.md",
    ):
        content = (DATA / relative).read_text(encoding="utf-8")
        assert "trw_build_check(" not in content, relative
        assert "mcp__trw__trw_build_check" not in content, relative


def test_copilot_testing_instruction_uses_project_coverage_policy() -> None:
    """Generated Copilot path guidance must not impose a framework percentage."""
    content = (PACKAGE / "bootstrap" / "_copilot.py").read_text(encoding="utf-8")
    assert "Target 90%+ coverage" not in content
    assert "project-configured coverage gate" in content
    assert "without inventing a percentage" in content
