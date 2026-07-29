"""Bundled agents must satisfy the static contract lint.

``scripts/check_agent_contracts.py`` catches four defect classes that the other
agent guards (LOC caps, mirror-sync hashes, phrase contracts) cannot see:

* ``schema`` — frontmatter keys the sub-agent harness does not read, so a
  declared tool allowlist is silently discarded;
* ``capability`` — instructions the agent cannot execute with its own grant
  (write a report without ``Write``, message a peer with no messaging tool);
* ``portability`` — monorepo-only paths or one language's test stack shipped to
  every user project;
* ``evidence`` — unsourced empirical claims in a framework that ranks
  truthfulness above persuasion.

The lint is the measurement that keeps those classes at zero; this test is what
makes it binding.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_agent_contracts.py"

# Monorepo-only invariant: the repo-root scripts/ layout is absent from the
# standalone trw-mcp mirror. Skip cleanly there.
if not _SCRIPT.is_file():
    pytest.skip("monorepo-only invariant (repo-root scripts/ absent in mirror)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("check_agent_contracts", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_lint = importlib.util.module_from_spec(_spec)
# Register before exec so the module's @dataclass declarations can resolve
# annotations — dataclasses looks the defining module up in sys.modules.
sys.modules[_spec.name] = _lint
_spec.loader.exec_module(_lint)

_BUNDLED = {p.name for p in _lint.AGENTS_DIR.glob("*.md")}
#: Bundled agents plus any dev/channel-authored agent that is not a projection
#: of the bundle — those never pass through the bundled-agent guards.
AGENT_FILES = sorted(_lint.AGENTS_DIR.glob("*.md")) + [
    p for p in sorted(_lint.MIRROR_AGENTS_DIR.glob("*.md")) if p.name not in _BUNDLED
]


def test_agent_files_discovered() -> None:
    """Guard against a silently empty scan (a moved directory would pass otherwise)."""
    assert AGENT_FILES, f"no bundled agents found under {_lint.AGENTS_DIR}"


@pytest.mark.parametrize("agent_path", AGENT_FILES, ids=lambda p: p.stem)
def test_agent_has_no_contract_violations(agent_path: Path) -> None:
    violations = _lint.lint_agent(agent_path)
    detail = "\n".join(f"  L{v.line} [{v.rule_id}] {v.message}\n        {v.text}" for v in violations)
    assert not violations, f"{agent_path.name}: {len(violations)} contract violation(s)\n{detail}"


def test_lint_detects_a_planted_violation(tmp_path: Path) -> None:
    """The lint fails closed: a synthetic bad agent is reported, not passed over."""
    planted = tmp_path / "bad-agent.md"
    planted.write_text(
        "---\nname: bad-agent\ndescription: use when testing\ntools:\n  - Read\nallowedTools:\n  - Read\n---\n\n"
        "1. Write the review to scratch/tm-{your-name}/reviews/R-1.yaml\n"
        "2. Mark task complete and message lead\n"
        "3. Call {tool:trw_deliver} when finished\n",
        encoding="utf-8",
    )
    rules = {v.rule_id for v in _lint.lint_agent(planted)}
    assert {
        "schema-ignored-key",
        "cap-write",
        "cap-message",
        "cap-task-api",
        "port-scratch",
        "cap-tool-reference",
    } <= rules


def test_unknown_mcp_tool_in_a_grant_is_reported(tmp_path: Path) -> None:
    """A misspelled ``mcp__trw__*`` entry silently narrows the agent — catch it here."""
    planted = tmp_path / "typo-agent.md"
    planted.write_text(
        "---\nname: typo-agent\ndescription: use when testing\ntools:\n"
        "  - mcp__trw__trw_recall\n  - mcp__trw__trw_recal\n---\n\nbody\n",
        encoding="utf-8",
    )
    violations = _lint.lint_agent(planted)
    assert [v.rule_id for v in violations] == ["schema-unknown-tool"]
    assert "trw_recal`" in violations[0].message


def test_registry_scan_finds_the_tool_package() -> None:
    """An empty registry would make the unknown-tool rule vacuous."""
    assert len(_lint.registered_trw_tools()) > 20


def test_granted_tools_applies_denials_before_the_allowlist() -> None:
    """``disallowedTools`` is subtracted first; ``tools`` cannot re-add a denial."""
    inherited = _lint.granted_tools({"disallowedTools": ["Write"]})
    assert "*" in inherited and "!Write" in inherited
    assert _lint.granted_tools({"tools": ["Read", "Grep"]}) == {"Read", "Grep"}
    assert _lint.granted_tools({"tools": ["Read", "Write"], "disallowedTools": ["Write"]}) == {"Read"}


def test_denied_tool_still_fails_a_capability_rule_under_an_inherited_grant(tmp_path: Path) -> None:
    """An agent that denies Write must not pass a 'write the report' instruction.

    Regression guard: treating a missing ``tools`` key as a blanket wildcard let
    the exact defect the rule exists for slip through whenever the agent used a
    denylist instead of an allowlist.
    """
    planted = tmp_path / "denylist-agent.md"
    planted.write_text(
        "---\nname: denylist-agent\ndescription: use when testing\ndisallowedTools:\n  - Write\n---\n\n"
        "1. Write the audit report to the artifact path\n",
        encoding="utf-8",
    )
    assert [v.rule_id for v in _lint.lint_agent(planted)] == ["cap-write"]


SKILL_FILES = _lint.skill_files()


def test_skill_files_discovered() -> None:
    """A silently empty skill scan would make the widened lint vacuous."""
    assert len(SKILL_FILES) > 20, f"only {len(SKILL_FILES)} bundled skills found"


@pytest.mark.parametrize("skill_path", SKILL_FILES, ids=lambda p: f"{p.parent.parent.parent.name}/{p.parent.name}")
def test_skill_files_are_linted_with_the_same_rules(skill_path: Path) -> None:
    """PRD-QUAL-128-FR10: skills ship through the same wheel, so they bind too."""
    violations = _lint.lint_skill(skill_path)
    detail = "\n".join(f"  L{v.line} [{v.rule_id}] {v.message}\n        {v.text}" for v in violations)
    assert not violations, f"{skill_path}: {len(violations)} contract violation(s)\n{detail}"


def test_a_planted_bad_skill_reports_the_expected_rule_ids(tmp_path: Path) -> None:
    """The widened lint fails closed on a skill, exactly as it does on an agent."""
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    planted = skill_dir / "SKILL.md"
    planted.write_text(
        "---\nname: bad-skill\ndescription: use when testing\nagent: trw-auditor\n---\n\n"
        "1. Store the audit under scratch/tm-{your-name}/audits/A-1.yaml\n"
        "2. 70% of defects are found this way.\n",
        encoding="utf-8",
    )
    rules = {v.rule_id for v in _lint.lint_skill(planted)}
    assert {"port-scratch", "eve-unsourced-stat"} <= rules, rules


def test_an_unresolvable_skill_agent_target_reports_cannot_verify(tmp_path: Path) -> None:
    """``general-purpose`` is the harness's agent, not ours — do not guess its grant."""
    assert _lint.skill_grant({"agent": "general-purpose"}) is None
    assert _lint.skill_grant({}) is None
    assert _lint.skill_grant({"agent": "trw-auditor"}) == _lint.granted_tools(
        _lint.split_frontmatter((_lint.AGENTS_DIR / "trw-auditor.md").read_text(encoding="utf-8"))[0]
    )

    planted = tmp_path / "SKILL.md"
    planted.write_text(
        "---\nname: unknown-target\ndescription: use when testing\nagent: general-purpose\n---\n\n"
        "1. Call `trw_deliver` when finished.\n",
        encoding="utf-8",
    )
    # No capability verdict without a resolvable grant — but a portability or
    # evidence defect on the same surface must still fire.
    assert [v.rule_id for v in _lint.lint_skill(planted)] == []


def test_skill_frontmatter_is_not_judged_by_the_agent_schema() -> None:
    """``agent:``/``argument-hint:`` are correct in a skill and wrong in an agent."""
    audit_skill = _lint.SKILLS_DIRS[0] / "trw-audit" / "SKILL.md"
    assert audit_skill.is_file()
    assert [v.rule_id for v in _lint.lint_skill(audit_skill) if v.category == "schema"] == []


def test_a_prohibition_is_not_a_capability_promise(tmp_path: Path) -> None:
    """Naming an ungranted tool in order to forbid it is correct, not a defect."""
    planted = tmp_path / "prohibition-agent.md"
    planted.write_text(
        "---\nname: prohibition-agent\ndescription: use when testing\ntools:\n  - Read\n---\n\n"
        "- Do NOT call `trw_deliver` or `trw_init`.\n",
        encoding="utf-8",
    )
    assert _lint.lint_agent(planted) == []
