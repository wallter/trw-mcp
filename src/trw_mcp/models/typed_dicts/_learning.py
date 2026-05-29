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

    This is the canonical typed contract for learning dicts crossing the
    recall layer (``memory_adapter.recall_learnings`` and the
    ``state.recall_factories`` factories that wrap it). Base fields
    (id, summary, tags, impact, status) are always present. Extended
    fields are present when ``compact=False`` (the default).

    Field set mirrors ``_memory_to_learning_dict`` exactly so the recall
    return type can be declared as ``list[LearningEntryDict]`` without a
    cast and without mypy ``extra-key`` errors.
    """

    detail: str
    evidence: list[str] | None
    source_type: str
    source_identity: str
    client_profile: str
    model_id: str
    created: str
    updated: str
    access_count: int
    last_accessed_at: str | None
    q_value: float
    q_observations: int
    recurrence: int
    outcome_history: list[str]
    shard_id: str | None
    # Assertions (PRD-CORE-086) — present only when the entry has them.
    assertions: list[dict[str, object]]
    # Meta-learning typed classification (PRD-CORE-110).
    type: str
    nudge_line: str
    expires: str
    confidence: str
    task_type: str
    domain: list[str]
    phase_origin: str
    phase_affinity: list[str]
    team_origin: str
    protection_tier: str
    # Code-grounded anchors (PRD-CORE-111).
    anchors: list[dict[str, object]]
    anchor_validity: float
    # Outcome attribution (PRD-CORE-108).
    sessions_surfaced: int
    avg_rework_delta: float | None
    outcome_correlation: str
    session_count: int


class PruneCandidateDict(TypedDict):
    """Prune candidate entry returned by ``utility_based_prune_candidates()``."""

    id: str
    summary: object
    age_days: int
    utility: float
    suggested_status: str
    reason: str
