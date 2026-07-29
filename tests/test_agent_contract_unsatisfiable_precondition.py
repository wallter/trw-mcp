"""``cap-unsatisfiable-precondition`` lint (PRD-CORE-233 FR04).

The pre-existing rules catch a grant the registry does not know
(``schema-unknown-tool``) and a body promising a tool the agent does not hold
(``cap-tool-reference``). Neither covers the defect PRD-CORE-233 was opened for:
a grant that IS valid and IS held, whose PRECONDITION the agent has no way to
establish.

Measured instance: 7 of 11 bundled agents grant ``trw_checkpoint`` but no tool
that can create the run it needs, producing 13 ``pin not found`` failures in one
day's log. The tool resolves, the call is legal, and it fails every time —
completely invisible to a lint that only checks whether names exist.

Follows the sibling convention in ``test_agent_contract_lint.py``: plant a
synthetic bad agent to prove the rule fails closed, rather than only asserting
the real tree is clean (which a vacuous rule also satisfies).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_agent_contracts.py"
AGENTS_DIR = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents"

RULE = "cap-unsatisfiable-precondition"
KNOWN = {"trw_checkpoint", "trw_learn", "trw_init", "trw_adopt_run", "trw_session_start"}

AGENT_TEMPLATE = """---
name: synthetic-agent
description: fixture
tools:
{tools}
---

{body}
"""


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_agent_contracts_fr04", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _agent(tmp_path: Path, tools: list[str], body: str) -> Path:
    path = tmp_path / "synthetic-agent.md"
    path.write_text(
        AGENT_TEMPLATE.format(tools="\n".join(f"  - {t}" for t in tools), body=body),
        encoding="utf-8",
    )
    return path


def _hits(module: ModuleType, path: Path) -> list[object]:
    return [v for v in module.lint_agent(path, known_tools=KNOWN) if v.rule_id == RULE]


@pytest.mark.unit
def test_checkpoint_without_any_run_provider_or_remedy_is_flagged(tmp_path: Path) -> None:
    """The exact shipped defect: holds checkpoint, cannot ever satisfy it."""
    module = _module()
    agent = _agent(
        tmp_path,
        ["Read", "mcp__trw__trw_checkpoint", "mcp__trw__trw_learn"],
        "Do the work and record progress as you go.",
    )
    hits = _hits(module, agent)
    assert len(hits) == 1, "an unsatisfiable checkpoint grant must be a violation"
    assert "active run" in hits[0].message  # type: ignore[attr-defined]


@pytest.mark.unit
def test_documented_run_path_remedy_clears_the_violation(tmp_path: Path) -> None:
    """FR01's fragment IS a valid way to satisfy the precondition — not a defect."""
    module = _module()
    agent = _agent(
        tmp_path,
        ["Read", "mcp__trw__trw_checkpoint"],
        "Your dispatcher passes run_path= to every checkpoint call.",
    )
    assert _hits(module, agent) == []


@pytest.mark.unit
@pytest.mark.parametrize("provider", ["trw_init", "trw_adopt_run", "trw_session_start"])
def test_holding_any_run_provider_clears_the_violation(tmp_path: Path, provider: str) -> None:
    """Any tool that can establish a run satisfies the precondition."""
    module = _module()
    agent = _agent(
        tmp_path,
        ["Read", "mcp__trw__trw_checkpoint", f"mcp__trw__{provider}"],
        "Do the work and record progress as you go.",
    )
    assert _hits(module, agent) == []


@pytest.mark.unit
def test_agent_without_the_precondition_tool_is_never_flagged(tmp_path: Path) -> None:
    """No checkpoint grant, no precondition to be unsatisfiable."""
    module = _module()
    agent = _agent(tmp_path, ["Read", "mcp__trw__trw_recall"], "Read things.")
    assert _hits(module, agent) == []


@pytest.mark.unit
def test_every_bundled_agent_currently_satisfies_the_rule() -> None:
    """The shipped tree must be clean — this is the gate the PRD asks for."""
    module = _module()
    offenders = [p.stem for p in sorted(AGENTS_DIR.glob("*.md")) if _hits(module, p)]
    assert offenders == [], f"bundled agents with an unsatisfiable precondition: {offenders}"


@pytest.mark.unit
def test_the_rule_is_live_on_real_agents_not_vacuously_passing() -> None:
    """Non-vacuity: strip FR01's remedy and the real defect must reappear.

    Without this, deleting the rule's body entirely would still leave
    ``test_every_bundled_agent_currently_satisfies_the_rule`` green.
    """
    module = _module()
    depend_on_remedy = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        frontmatter, body, _ = module.split_frontmatter(path.read_text(encoding="utf-8"))
        tools = module.granted_tools(frontmatter)
        without_remedy = re.sub(r"(?i)run_path\s*=", "REDACTED", body)
        if module._lint_unsatisfiable_preconditions(path.stem, tools, without_remedy):
            depend_on_remedy.append(path.stem)

    assert len(depend_on_remedy) >= 7, (
        "the rule must actually bind the agents PRD-CORE-233 measured (7 grant "
        f"checkpoint with no run provider); it only bound {depend_on_remedy}"
    )
