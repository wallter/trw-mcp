"""Finite, reversible skill lifecycle state machine.

State owns this dependency-free transition model so persisted lifecycle storage
never imports upward from the tools layer. The tools compatibility module
re-exports these names for existing callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SkillLifecycleState(str, Enum):
    """Finite forward lifecycle; ``removed`` is terminal."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    HIDDEN = "hidden"
    RETIRED = "retired"
    REMOVED = "removed"


_FORWARD_ORDER: tuple[SkillLifecycleState, ...] = (
    SkillLifecycleState.ACTIVE,
    SkillLifecycleState.DEPRECATED,
    SkillLifecycleState.HIDDEN,
    SkillLifecycleState.RETIRED,
    SkillLifecycleState.REMOVED,
)
_ADVERTISABLE_STATES = frozenset({SkillLifecycleState.ACTIVE, SkillLifecycleState.DEPRECATED})


class LifecycleTransitionError(ValueError):
    """Raised when a lifecycle transition is refused."""


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """One recorded lifecycle transition with its full removal contract."""

    skill_name: str
    from_state: SkillLifecycleState
    to_state: SkillLifecycleState
    owner: str
    evidence_window: str
    expiry: str
    replacement: str
    rollback_snapshot: str


@dataclass(frozen=True, slots=True)
class SkillLifecycleRecord:
    """A skill's current lifecycle state plus its ordered transition history."""

    skill_name: str
    state: SkillLifecycleState = SkillLifecycleState.ACTIVE
    history: tuple[LifecycleTransition, ...] = field(default_factory=tuple)


def is_advertisable(state: SkillLifecycleState) -> bool:
    """Return whether discovery may advertise a skill in ``state``."""
    return state in _ADVERTISABLE_STATES


def advance(
    record: SkillLifecycleRecord,
    to_state: SkillLifecycleState,
    *,
    owner: str,
    evidence_window: str,
    expiry: str,
    replacement: str,
    rollback_snapshot: str,
) -> SkillLifecycleRecord:
    """Advance a skill one adjacent forward lifecycle step."""
    _require_fields(
        owner=owner,
        evidence_window=evidence_window,
        expiry=expiry,
        replacement=replacement,
        rollback_snapshot=rollback_snapshot,
    )
    from_index = _FORWARD_ORDER.index(record.state)
    to_index = _FORWARD_ORDER.index(to_state)
    if to_index != from_index + 1:
        raise LifecycleTransitionError(f"non-adjacent forward transition {record.state.value} -> {to_state.value}")
    transition = LifecycleTransition(
        skill_name=record.skill_name,
        from_state=record.state,
        to_state=to_state,
        owner=owner,
        evidence_window=evidence_window,
        expiry=expiry,
        replacement=replacement,
        rollback_snapshot=rollback_snapshot,
    )
    return SkillLifecycleRecord(record.skill_name, to_state, (*record.history, transition))


def restore(record: SkillLifecycleRecord, *, owner: str, reason: str) -> SkillLifecycleRecord:
    """Reverse the latest transition before the terminal removed state."""
    _require_fields(owner=owner, reason=reason)
    if record.state is SkillLifecycleState.REMOVED:
        raise LifecycleTransitionError("removal is terminal; a removed skill cannot be restored")
    if not record.history:
        raise LifecycleTransitionError("no transition to reverse")
    last = record.history[-1]
    reversal = LifecycleTransition(
        skill_name=record.skill_name,
        from_state=record.state,
        to_state=last.from_state,
        owner=owner,
        evidence_window=last.evidence_window,
        expiry=last.expiry,
        replacement=last.replacement,
        rollback_snapshot=f"restore:{reason}:{last.rollback_snapshot}",
    )
    return SkillLifecycleRecord(record.skill_name, last.from_state, (*record.history, reversal))


def _require_fields(**fields: str) -> None:
    missing = sorted(name for name, value in fields.items() if not str(value).strip())
    if missing:
        raise LifecycleTransitionError(f"lifecycle transition refused, missing required fields: {', '.join(missing)}")


__all__ = [
    "LifecycleTransition",
    "LifecycleTransitionError",
    "SkillLifecycleRecord",
    "SkillLifecycleState",
    "advance",
    "is_advertisable",
    "restore",
]
