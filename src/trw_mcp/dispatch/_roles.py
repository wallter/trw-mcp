"""Second-opinion audit roles for the dispatch layer (PRD-CORE-297-FR01).

Belongs to the ``trw_mcp.dispatch`` package. ``ROLE_TABLE`` is the one source of
truth for a role: its read-only preamble, prepended to the caller's prompt so the
dispatched child behaves as an independent reviewer, and its task class in the
policy table (PRD-CORE-290-FR02/FR03), from which effort, tier and turns come.
Every other role-id list (the CLI's ``--role`` choices) is derived from it.

``apply_role`` and ``role_task_class`` are pure. An unknown / ``None`` role
passes through unchanged so callers can dispatch a bare prompt without a role.
"""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.agents.task_policy import TASK_POLICY

_READ_ONLY_CONTRACT = (
    "You are an independent second-opinion reviewer. Operate STRICTLY read-only: "
    "do NOT edit, create, or delete any files, and do NOT run mutating commands. "
    "Report findings as a list, each with a severity (P0 = blocking/broken, "
    "P1 = significant gap, P2 = minor), a concrete location, the evidence, and a "
    "specific recommendation. End with a one-line overall verdict."
)


@dataclass(frozen=True)
class RoleSpec:
    """What a dispatch role is: its policy-table task class, its preamble, and the
    variant kind its output is written as (PRD-CORE-299-FR04)."""

    task_class: str
    preamble: str
    artifact_kind: str


#: Review and planning run at the review row (``medium``), an adversarial /
#: security audit at the security row (``high``).
ROLE_TABLE: dict[str, RoleSpec] = {
    "code-review": RoleSpec(
        "review",
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: code correctness, edge cases, error handling, test quality, and "
        "whether the change does what it claims. Flag bugs, missing validation, "
        "and tests that assert existence rather than behavior.",
        "review",
    ),
    "design-audit": RoleSpec(
        "review",
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: API/interface design, naming, cohesion, coupling, and DRY. Flag "
        "leaky abstractions, shallow modules, and duplicated logic that should be "
        "a shared source of truth.",
        "audit",
    ),
    "architectural-audit": RoleSpec(
        "review",
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: system-level structure — module boundaries, dependency direction, "
        "data flow, failure modes, and scalability. Flag boundary violations, "
        "hidden coupling, and single points of failure.",
        "audit",
    ),
    "adversarial-audit": RoleSpec(
        "security",
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: actively try to break the work. Hunt for security holes, injection "
        "vectors, race conditions, unhandled inputs, and incorrect assumptions. "
        "Assume the author is wrong until the code proves otherwise.",
        "audit",
    ),
}

# A role whose class is not a policy row would silently run at the client default.
_unknown_classes = {spec.task_class for spec in ROLE_TABLE.values()} - set(TASK_POLICY)
if _unknown_classes:  # pragma: no cover - import-time registry guard
    raise ValueError(f"ROLE_TABLE task classes missing from TASK_POLICY: {sorted(_unknown_classes)}")


def role_task_class(role: str | None) -> str | None:
    """The task class of *role*, or None for an absent or unknown role."""
    spec = ROLE_TABLE.get(role or "")
    return spec.task_class if spec else None


def apply_role(role: str | None, prompt: str) -> str:
    """Prepend the *role* preamble to *prompt*.

    Returns *prompt* unchanged when *role* is ``None`` or not a known role, so a
    typo or bare dispatch never silently drops the user's instruction.
    """
    if not role:
        return prompt
    spec = ROLE_TABLE.get(role)
    if spec is None:
        return prompt
    return f"{spec.preamble}\n\n---\n\n{prompt}"
