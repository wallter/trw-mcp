"""Evidence-backed reversible skill lifecycle (PRD-CORE-218-FR07).

Belongs to the ``skill_discovery.py`` domain. ``skill_discovery`` imports the
advertising filter from here so retired skills stop being surfaced; the rest of
this module is the truthful evidence stream, the non-causal contribution signal,
the bounded active-cap, near-duplicate flagging, and the finite/reversible
lifecycle state machine.

Design invariants (all machine-checked by ``tests/test_skill_lifecycle.py``):

- The evidence record schema carries NO causal-success field. Contribution is a
  recency-weighted engagement measure, never a claim that a skill *caused* task
  success. Fabricating such a field is the exact failure mode this replaces.
- The append-only evidence ledger is bounded (oldest evicted) so a long session
  cannot grow it without limit.
- Lifecycle moves forward one adjacent state at a time
  (active -> deprecated -> hidden -> retired -> removed). Every forward
  transition requires owner, evidence window, expiry, replacement, and a
  rollback snapshot; a transition missing any field is refused.
- Retirement is reversible: a record may be restored to its prior state at any
  point BEFORE ``removed``. ``removed`` is terminal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from trw_mcp.state.skill_lifecycle import (
    LifecycleTransition,
    LifecycleTransitionError,
    SkillLifecycleRecord,
    SkillLifecycleState,
    advance,
    is_advertisable,
    restore,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Recency half-life for the contribution signal, expressed in discovery "steps"
# (a monotonic integer index, never wall-clock — determinism is required by the
# acceptance test). After this many steps a piece of evidence contributes half.
_DEFAULT_RECENCY_HALF_LIFE = 5.0

# Normalized-token Jaccard overlap at or above which two descriptions are flagged
# as near-duplicates. Flag only — never an automatic merge.
_DEFAULT_DUPLICATE_THRESHOLD = 0.6

# Engagement weights. ``invoked`` outweighs ``surfaced`` because an agent chose
# to run the skill, but BOTH are engagement, not causal success.
_KIND_WEIGHT = {"surfaced": 1.0, "invoked": 2.0}


class SkillEvidenceKind(str, Enum):
    """The two observable, non-causal skill-evidence events."""

    SURFACED = "surfaced"
    INVOKED = "invoked"


@dataclass(frozen=True, slots=True)
class SkillEvidenceRecord:
    """One append-only evidence event.

    ``at_step`` is a monotonic integer index supplied by the caller so recency
    is deterministic and testable. There is intentionally NO success / outcome /
    caused field: contribution is engagement, never causation.
    """

    skill_name: str
    kind: SkillEvidenceKind
    at_step: int


class SkillEvidenceLedger:
    """Bounded, append-only evidence ledger.

    Records are appended in order; once ``max_records`` is exceeded the oldest
    records are evicted so a long session cannot grow the ledger unbounded.
    """

    def __init__(self, *, max_records: int) -> None:
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self._max = max_records
        self._records: list[SkillEvidenceRecord] = []

    def record(self, skill_name: str, kind: SkillEvidenceKind, *, at_step: int) -> None:
        self._records.append(SkillEvidenceRecord(skill_name, kind, at_step))
        overflow = len(self._records) - self._max
        if overflow > 0:
            del self._records[:overflow]

    @property
    def records(self) -> tuple[SkillEvidenceRecord, ...]:
        return tuple(self._records)


def contribution_signal(
    ledger: SkillEvidenceLedger,
    skill_name: str,
    *,
    now_step: int,
    half_life: float = _DEFAULT_RECENCY_HALF_LIFE,
) -> float:
    """Return a recency-weighted, NON-CAUSAL contribution signal for a skill.

    This measures how recently and how often a skill was surfaced or invoked; it
    is NOT a claim that the skill caused any task to succeed. No causal-success
    input exists in :class:`SkillEvidenceRecord`, so this signal cannot smuggle
    one in. Deterministic: identical ledger + ``now_step`` always yields the same
    value.
    """
    total = 0.0
    for rec in ledger.records:
        if rec.skill_name != skill_name:
            continue
        age = max(now_step - rec.at_step, 0)
        weight = 0.5 ** (age / half_life)
        total += _KIND_WEIGHT[rec.kind.value] * weight
    return total


def rank_by_contribution(
    ledger: SkillEvidenceLedger,
    skill_names: Sequence[str],
    *,
    now_step: int,
    half_life: float = _DEFAULT_RECENCY_HALF_LIFE,
) -> tuple[str, ...]:
    """Rank skills by contribution signal (desc), ties broken by name (asc)."""
    scored = [(contribution_signal(ledger, name, now_step=now_step, half_life=half_life), name) for name in skill_names]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(name for _, name in scored)


def apply_active_cap(ranked: Sequence[str], cap: int | None) -> tuple[str, ...]:
    """Apply an optional bounded active-cap AFTER ranking.

    ``None`` (default) is a no-op returning every ranked skill. A non-negative
    integer truncates to the top-N without reordering.
    """
    if cap is None:
        return tuple(ranked)
    return tuple(ranked[: max(cap, 0)])


@dataclass(frozen=True, slots=True)
class DuplicateFlag:
    """A flagged (never auto-merged) near-duplicate skill pair."""

    skill_a: str
    skill_b: str
    overlap: float


def _norm_tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def flag_near_duplicates(
    descriptions: Mapping[str, str],
    *,
    threshold: float = _DEFAULT_DUPLICATE_THRESHOLD,
) -> tuple[DuplicateFlag, ...]:
    """Flag near-duplicate descriptions by normalized token overlap.

    Returns a deterministic, name-ordered tuple of flagged pairs. This never
    merges or removes a skill — duplicate resolution is a human review decision.
    """
    items = sorted(descriptions.items())
    tokens = {name: _norm_tokens(desc) for name, desc in items}
    names = [name for name, _ in items]
    flags: list[DuplicateFlag] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            overlap = _jaccard(tokens[names[i]], tokens[names[j]])
            if overlap >= threshold:
                flags.append(DuplicateFlag(names[i], names[j], overlap))
    return tuple(flags)


__all__ = [
    "DuplicateFlag",
    "LifecycleTransition",
    "LifecycleTransitionError",
    "SkillEvidenceKind",
    "SkillEvidenceLedger",
    "SkillEvidenceRecord",
    "SkillLifecycleRecord",
    "SkillLifecycleState",
    "advance",
    "apply_active_cap",
    "contribution_signal",
    "flag_near_duplicates",
    "is_advertisable",
    "rank_by_contribution",
    "restore",
]
