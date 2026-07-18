"""Second-opinion audit role templates for the dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. Each role is a read-only preamble
prepended to the caller's prompt so the dispatched child agent behaves as an
independent reviewer: it must NOT edit code, only report findings with severity.

``apply_role`` is a pure function. An unknown / ``None`` role passes the prompt
through unchanged so callers can dispatch a bare prompt without a role.
"""

from __future__ import annotations

_READ_ONLY_CONTRACT = (
    "You are an independent second-opinion reviewer. Operate STRICTLY read-only: "
    "do NOT edit, create, or delete any files, and do NOT run mutating commands. "
    "Report findings as a list, each with a severity (P0 = blocking/broken, "
    "P1 = significant gap, P2 = minor), a concrete location, the evidence, and a "
    "specific recommendation. End with a one-line overall verdict."
)

ROLE_TEMPLATES: dict[str, str] = {
    "code-review": (
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: code correctness, edge cases, error handling, test quality, and "
        "whether the change does what it claims. Flag bugs, missing validation, "
        "and tests that assert existence rather than behavior."
    ),
    "design-audit": (
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: API/interface design, naming, cohesion, coupling, and DRY. Flag "
        "leaky abstractions, shallow modules, and duplicated logic that should be "
        "a shared source of truth."
    ),
    "architectural-audit": (
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: system-level structure — module boundaries, dependency direction, "
        "data flow, failure modes, and scalability. Flag boundary violations, "
        "hidden coupling, and single points of failure."
    ),
    "adversarial-audit": (
        f"{_READ_ONLY_CONTRACT}\n\n"
        "Focus: actively try to break the work. Hunt for security holes, injection "
        "vectors, race conditions, unhandled inputs, and incorrect assumptions. "
        "Assume the author is wrong until the code proves otherwise."
    ),
}


def apply_role(role: str | None, prompt: str) -> str:
    """Prepend the *role* preamble to *prompt*.

    Returns *prompt* unchanged when *role* is ``None`` or not a known role, so a
    typo or bare dispatch never silently drops the user's instruction.
    """
    if not role:
        return prompt
    preamble = ROLE_TEMPLATES.get(role)
    if preamble is None:
        return prompt
    return f"{preamble}\n\n---\n\n{prompt}"
