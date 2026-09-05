"""The shipped delivery skills must treat `trw_build_check` as a REPORTER.

Replaces `test_eval_deliver_skill_build_contract.py`, deleted in `69abca6cfc`.
That module guarded this same property, but only on a vendored trw-mcp mirror
kept inside the (proprietary) eval package — a tree deleted wholesale by
`a77650f238`. Because its paths
sat behind `skipif(not all(p.exists()))`, it had already degraded to a permanent
skip asserting nothing, and its assertion strings appear nowhere in the canonical
skills, so it could not simply be repointed.

The invariant itself is framework-level, not eval-local:
`.trw/frameworks/FRAMEWORK-CORE.md` states `trw_build_check` "records observed
project-native validation at VALIDATE and before DELIVER after code/test
changes; **it does not run checks**". A delivery skill that tells an agent the
tool runs validation produces exactly the failure the deliver gate exists to
prevent — a passing build record bound to no executed command.

So the guard is restored here, against the surfaces that actually ship.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

#: Every bundled projection of the delivery skill. Unlike the module this
#: replaces, these are NOT existence-guarded: they ship in the public wheel, so
#: an absent one is a packaging defect, not an environment difference.
#: All four were found by `test_every_bundled_deliver_projection_is_covered` on
#: its first run: the module this replaces named only two paths, and both were
#: vendored. The live surface is twice as wide as the retired guard implied.
DELIVER_SKILLS = (
    _ROOT / "trw-mcp/src/trw_mcp/data/skills/trw-deliver/SKILL.md",
    _ROOT / "trw-mcp/src/trw_mcp/data/codex/skills/trw-deliver/SKILL.md",
    _ROOT / "trw-mcp/src/trw_mcp/data/copilot/skills/trw-deliver/SKILL.md",
    _ROOT / "trw-mcp/src/trw_mcp/data/opencode/skills/trw-deliver/SKILL.md",
)

#: Phrasings that would tell an agent the tool executes validation itself.
#: Matched case-insensitively against the whole document.
_EXECUTOR_CLAIMS = (
    r"trw_build_check[^.\n]{0,40}\bruns?\b[^.\n]{0,30}\b(test|check|validation|pytest|suite)",
    r"trw_build_check[^.\n]{0,40}\bexecut",
    r"(call|use)\s+`?trw_build_check`?[^.\n]{0,30}\bto run\b",
)


@pytest.mark.unit
@pytest.mark.parametrize("skill_path", DELIVER_SKILLS, ids=lambda p: p.parent.parent.name)
def test_deliver_skill_does_not_claim_build_check_runs_validation(skill_path: Path) -> None:
    """No projection may describe `trw_build_check` as executing the checks."""
    assert skill_path.is_file(), f"bundled delivery skill missing: {skill_path}"
    content = skill_path.read_text(encoding="utf-8")
    for pattern in _EXECUTOR_CLAIMS:
        match = re.search(pattern, content, re.IGNORECASE)
        assert match is None, (
            f"{skill_path} implies trw_build_check executes validation "
            f"(matched {match.group(0)!r}); the tool only RECORDS a result"
        )


@pytest.mark.unit
@pytest.mark.parametrize("skill_path", DELIVER_SKILLS, ids=lambda p: p.parent.parent.name)
def test_deliver_skill_runs_validation_before_recording_it(skill_path: Path) -> None:
    """Running the validation must be its own step, ordered before the record.

    Asserts ORDER, not layout. The projections legitimately differ in shape:
    the canonical skill splits "Run the narrowest meaningful validation" and
    "Call `trw_build_check(...)`" into two numbered steps, while the opencode
    variant combines them into one ("Run project-native validation, then record
    its observed result with `trw_build_check`"). Both are correct; a test that
    demanded two separate steps would have failed the opencode skill for its
    formatting, which is how a guard starts getting weakened to pass.

    Frontmatter is stripped first: `allowed-tools` names `trw_build_check`
    above every step, so searching the raw document would find the tool before
    any instruction and report a false inversion.
    """
    body = re.sub(r"\A---\n.*?\n---\n", "", skill_path.read_text(encoding="utf-8"), flags=re.DOTALL)
    run_step = re.search(r"\bRun\b[^.\n]{0,60}\bvalidation\b", body, re.IGNORECASE)
    record_step = re.search(r"trw_build_check", body)

    assert run_step is not None, f"{skill_path} never instructs the agent to RUN validation"
    assert record_step is not None, f"{skill_path} never instructs the agent to record via trw_build_check"
    assert run_step.start() < record_step.start(), (
        f"{skill_path} records the build result before running validation; "
        "build evidence must postdate the check it claims to cover"
    )


@pytest.mark.unit
def test_every_bundled_deliver_projection_is_covered() -> None:
    """Fails when a new trw-deliver projection ships without joining this guard.

    The module this replaces went stale precisely because its path list stopped
    matching what was on disk, and nothing said so.
    """
    discovered = {
        path
        for path in (_ROOT / "trw-mcp/src/trw_mcp/data").rglob("skills/trw-deliver/SKILL.md")
        if "plugin" not in path.parts
    }
    assert discovered, "no bundled trw-deliver skill found — has the layout changed?"
    missing = sorted(str(p.relative_to(_ROOT)) for p in discovered - set(DELIVER_SKILLS))
    assert not missing, f"bundled trw-deliver projections not covered by DELIVER_SKILLS: {missing}"
