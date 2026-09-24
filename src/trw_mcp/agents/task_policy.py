"""One task-class policy table: task class -> capability tier -> effort (PRD-CORE-290-FR02).

The single source for every bundled agent's ``model:`` tier and ``effort:`` and
for dispatch defaults. It encodes the operator's 2026-09-22 model/effort
decision in portable vocabulary (PRD-CORE-289-FR09): client adapters map a tier
to a concrete model (``tier_resolver.py``), and nothing here names a model.

Escalation to the top capability tier is never a class default: it is an
explicit, measured request (operator rule "no Fable subagents").
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AGENT_TASK_CLASS", "TASK_POLICY", "TaskPolicy"]


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    tier: str
    effort: str
    #: Dispatch turn cap for the class (PRD-CORE-290-FR04): ``None`` takes the
    #: dispatch default, 0 exempts the class.
    max_turns: int | None = None
    #: Whether rendered agents of the class carry the final-report size cap.
    report_capped: bool = True


#: Operator decision, 2026-09-22 (effort never defaults above ``high``: the
#: operator sees diminishing returns there and wants ``xhigh``/``max`` only with
#: evidence). A ``local-small`` model takes no effort parameter; its ``low`` is
#: dropped by the adapter, never sent.
#: Review and security classes are exempt from both caps (lead ruling on the b4
#: review): a turn cap truncates an audit mid-evidence, and their findings schema
#: is structured YAML that a character cap would cut.
TASK_POLICY: dict[str, TaskPolicy] = {
    "sweep": TaskPolicy("local-small", "low"),  # sweeps, search, file reading
    "implement": TaskPolicy("balanced", "medium"),  # implementation, triage, test writing, research
    "review": TaskPolicy("frontier", "medium", max_turns=0, report_capped=False),
    "plan": TaskPolicy("frontier", "medium"),
    "lead": TaskPolicy("frontier", "medium"),
    "security": TaskPolicy("frontier", "high", max_turns=0, report_capped=False),  # security review, adversarial audit
}

#: Every bundled agent's class. The frontmatter of ``data/agents/<name>.md`` must
#: equal its class's row (enforced by ``tests/test_task_class_policy.py``).
AGENT_TASK_CLASS: dict[str, str] = {
    "trw-adversarial-auditor": "security",
    "trw-auditor": "review",
    "trw-implementer": "implement",
    "trw-lead": "lead",
    "trw-prd-groomer": "plan",
    "trw-requirement-reviewer": "review",
    "trw-researcher": "implement",
    "trw-reviewer": "review",
}
