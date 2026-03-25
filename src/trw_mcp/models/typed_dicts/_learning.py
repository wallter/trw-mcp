"""Learning entry TypedDicts — most-used dicts in the codebase."""

from __future__ import annotations

from typing_extensions import TypedDict


class LearningEntryCompactDict(TypedDict):
    """Compact learning entry (compact=True in recall)."""

    id: str
    summary: str
    tags: list[str]
    impact: float
    status: str


class LearningEntryDict(LearningEntryCompactDict, total=False):
    """Full learning entry returned by ``_memory_to_learning_dict``.

    Base fields (id, summary, tags, impact, status) are always present.
    Extended fields are present when ``compact=False`` (the default).
    """

    detail: str
    evidence: list[str] | None
    source_type: str
    source_identity: str
    created: str
    updated: str
    access_count: int
    last_accessed_at: str | None
    q_value: float
    q_observations: int
    recurrence: int
    shard_id: str | None


class PruneCandidateDict(TypedDict):
    """Prune candidate entry returned by ``utility_based_prune_candidates()``."""

    id: str
    summary: object
    age_days: int
    utility: float
    suggested_status: str
    reason: str
