"""Canon parity: the installed FRAMEWORK values block must not say less than the Constitution.

FRAMEWORK.md is the only normative text an agent in a user project reads; the Constitution
is not installed. The FRAMEWORK "Values and hard limits" paragraph is therefore a projection
of the Constitution's hard tier, and a projection drifts by compression. ID-only checks
passed a block that had dropped "explicit" from HB-2, credential exposure from the refusal
list and the whole minimum-agency clause (canon swarm 2026-09-25, H2/H8 findings), so this
test pins per-clause obligation tokens on both sides: if the Constitution still states an
obligation, the installed block must still carry its qualifier.
"""

from __future__ import annotations

import re

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT

if MONOREPO_ROOT is None:
    pytest.skip(
        "monorepo-only invariant (docs/CONSTITUTION.md absent in standalone mirror)",
        allow_module_level=True,
    )

_CONSTITUTION = MONOREPO_ROOT / "docs" / "CONSTITUTION.md"
_FRAMEWORK = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "framework.md"

_VALUE_ORDER = "Truthfulness > Quality > Knowledge > Velocity"

#: clause -> (tokens the Constitution must contain, tokens the installed block must contain).
#: Matching is case-insensitive substring. Keep the tokens to the qualifiers that change behaviour;
#: the Constitution side makes a deliberate Constitution rewrite fail here for review.
_CLAUSES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "HB-2 explicit authorization": (("explicit authorization",), ("explicit authorization",)),
    "HB-3 breaking changes": (("breaking changes",), ("breaking changes",)),
    "HB-5 credentials and scope": (
        ("credentials", "irreversible action outside the authorized task scope"),
        ("credential exposure", "outside the authorized scope"),
    ),
    "HB-6 material state and tombstone": (
        ("material, authorized state for convenience", "tombstone"),
        ("material, authorized state for convenience", "tombstone"),
    ),
    "deliver gate is hard tier": (("the §1.a deliver gate",), ("and the deliver gate above",)),
    "minimum agency": (
        ("minimum functionality, minimum permissions, minimum autonomy", "fewest tools", "narrowest permissions"),
        ("minimum functionality, permissions and autonomy", "fewest tools", "narrowest permissions"),
    ),
    "reversible choice": (("the one you can undo",), ("the one you can undo",)),
    "delegate credentials": (("sub-agents or downstream tools",), ("to a delegate beyond its sub-task",)),
    "precedence order": (
        ("p1. authenticated host instructions", "p2. task-local requirements", "p4. convenience"),
        ("host's own authority order", "task-local requirements", "> convenience"),
    ),
    "data carries no authority": (
        ("operator authorized", "your own earlier generated text"),
        ('"operator authorized"', "own earlier text and plans"),
    ),
    "refuse before acting": (
        ("credential exposure", "*before* acting"),
        ("credential exposure", "misrepresentation", "before acting"),
    ),
    "unattended halt trigger": (
        ("when any due escalate has no reachable authorizer",),
        ("when an escalation is due and no authorizer is reachable", "never self-authoriz"),
    ),
    "proceed conditions": (
        ("no urgency claimed",),
        ("low-stakes, reversible", "claimed urgency"),
    ),
    "anti-manipulation": (("compelling argument",), ("persuasive case for an exception",)),
}


def _values_block(framework: str) -> str:
    match = re.search(r"^\*\*Values and hard limits\*\*.*$", framework, re.MULTILINE)
    assert match is not None, "FRAMEWORK.md has no installed 'Values and hard limits' block"
    return match.group(0)


def parity_failures(framework: str, constitution: str) -> list[str]:
    """Every clause whose qualifier the Constitution states but the installed block dropped."""
    block = _values_block(framework).lower()
    source = constitution.lower()
    failures: list[str] = []
    if _VALUE_ORDER not in framework or _VALUE_ORDER not in constitution:
        failures.append(f"value order: literal '{_VALUE_ORDER}' must appear in both")
    for hb in (f"HB-{n}" for n in range(1, 7)):
        if hb not in _values_block(framework) or hb not in constitution:
            failures.append(f"{hb}: id missing from the installed block or the Constitution")
    for clause, (source_tokens, block_tokens) in _CLAUSES.items():
        missing_src = [t for t in source_tokens if t not in source]
        missing_blk = [t for t in block_tokens if t not in block]
        if missing_src:
            failures.append(f"{clause}: Constitution no longer states {missing_src} — review the projection")
        if missing_blk:
            failures.append(f"{clause}: installed block dropped {missing_blk}")
    return failures


def test_installed_values_block_matches_constitution() -> None:
    failures = parity_failures(_FRAMEWORK.read_text(encoding="utf-8"), _CONSTITUTION.read_text(encoding="utf-8"))
    assert not failures, "values-block parity drift:\n" + "\n".join(failures)


def test_parity_check_catches_known_compression_drift() -> None:
    """The cycle-1 block (2026-09-25) passed an ID-only design; this check must reject it."""
    constitution = _CONSTITUTION.read_text(encoding="utf-8")
    drifted = (
        "**Values and hard limits** (summary). Value hierarchy: " + _VALUE_ORDER + ". "
        "HB-1 never misrepresent completion; HB-2 never discard uncommitted work without authorization; "
        "HB-3 never hide material failures, risks or vulnerabilities; HB-4 never fabricate evidence; "
        "HB-5 no credential exposure outside the authorized scope; HB-6 never trade away authorized state. "
        "Refuse and explain deception, concealment or fabrication; escalate before irreversible acts."
    )
    failures = parity_failures(drifted, constitution)
    dropped = " ".join(failures)
    for expected in (
        "HB-2 explicit authorization",
        "HB-3 breaking changes",
        "minimum agency",
        "deliver gate is hard tier",
    ):
        assert expected in dropped, f"drift in '{expected}' went undetected"
