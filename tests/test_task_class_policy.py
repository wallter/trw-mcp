"""PRD-CORE-290-FR02: one task-class policy table drives every bundled agent's tier and effort.

The table (task class -> capability tier -> effort) encodes the operator's
2026-09-22 model/effort decision in portable vocabulary (PRD-CORE-289-FR09);
client adapters map tiers to model names. An agent whose frontmatter disagrees
with its class's row fails here, so the table stays the one source.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trw_mcp.agents.agent_frontmatter import split_agent_document
from trw_mcp.agents.task_policy import AGENT_TASK_CLASS, TASK_POLICY

pytestmark = pytest.mark.unit

_AGENTS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "agents"
_AGENTS = sorted(_AGENTS_DIR.glob("*.md"))


def test_every_bundled_agent_has_exactly_one_task_class() -> None:
    assert _AGENTS, f"no bundled agents under {_AGENTS_DIR}"
    assert sorted(path.stem for path in _AGENTS) == sorted(AGENT_TASK_CLASS)
    assert set(AGENT_TASK_CLASS.values()) <= set(TASK_POLICY)


@pytest.mark.parametrize("agent", _AGENTS, ids=lambda p: p.stem)
def test_agent_frontmatter_matches_its_task_class(agent: Path) -> None:
    frontmatter = yaml.safe_load(split_agent_document(agent.read_text(encoding="utf-8"))[0])
    policy = TASK_POLICY[AGENT_TASK_CLASS[agent.stem]]
    assert (frontmatter.get("model"), frontmatter.get("effort")) == (policy.tier, policy.effort), (
        f"{agent.stem} is class {AGENT_TASK_CLASS[agent.stem]!r}: the table says "
        f"model={policy.tier} effort={policy.effort}; edit the table or the class, never one agent"
    )


def test_the_table_never_defaults_above_high_and_never_names_a_model() -> None:
    """The operator sees diminishing returns above ``high``; tiers are portable vocabulary."""
    assert {policy.effort for policy in TASK_POLICY.values()} <= {"low", "medium", "high"}
    assert {policy.tier for policy in TASK_POLICY.values()} <= {"frontier", "balanced", "local-large", "local-small"}
