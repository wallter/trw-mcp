"""Delivery pipeline step result TypedDicts (_deferred_delivery.py)."""

from __future__ import annotations

from typing import TypedDict

from typing_extensions import NotRequired

from trw_mcp.models.typed_dicts._analytics import TierDistribution

# LearnResult was merged into LearnResultDict in _tools.py (PRD-CORE-080).
# Re-exported here so existing ``from ... _delivery import LearnResult`` call-sites work.
from trw_mcp.models.typed_dicts._tools import LearnResultDict as LearnResult

__all__ = ["LearnResult"]


class StepResultBase(TypedDict):
    """Shared ``status`` field for delivery step results."""

    status: str


class TrustIncrementResult(TypedDict, total=False):
    """Return shape of ``_step_trust_increment()``.

    Two distinct shapes are returned:

    Incremented path (build_check_passed or productive_session)::

        {"session_count": int, "previous_tier": str, "new_tier": str,
         "transitioned": bool, "reason": str}

    Skipped path::

        {"skipped": True, "reason": str}
    """

    session_count: int
    previous_tier: str
    new_tier: str
    transitioned: bool
    skipped: bool
    reason: str


class TelemetryStepResult(StepResultBase):
    """Return shape of ``_step_telemetry()``."""

    events: int
    ceremony_score: int


class TierSweepStepResult(TypedDict):
    """Return shape of ``_step_tier_sweep()``."""

    status: str
    promoted: int
    demoted: int
    purged: int
    errors: int
    impact_tier_distribution: NotRequired[TierDistribution]


class ProgressionItem(TypedDict):
    """Single PRD progression result from ``auto_progress_prds()``."""

    prd_id: str
    from_status: str
    to_status: str
    applied: bool
    reason: str


class _AutoProgressStepResultRequired(TypedDict):
    """Required keys for AutoProgressStepResult."""

    status: str


class AutoProgressStepResult(_AutoProgressStepResultRequired, total=False):
    """Return shape of ``_do_auto_progress()``.

    ``status`` is always present.  ``total_evaluated``, ``applied``,
    ``progressions`` are present on the success path; ``reason`` on skipped paths.
    """

    reason: str
    total_evaluated: int
    applied: int
    progressions: list[ProgressionItem]


class OutcomeCorrelationStepResult(StepResultBase):
    """Return shape of ``_step_outcome_correlation()``."""

    updated: int


class RecallOutcomeStepResult(StepResultBase):
    """Return shape of ``_step_recall_outcome()``."""

    recorded: int


class ConsolidationStepResult(TypedDict, total=False):
    """Return shape of ``_step_consolidation()``.

    Three distinct paths share this type:

    Disabled::

        {"status": "skipped", "reason": str}

    No clusters found::

        {"status": "no_clusters", "clusters_found": 0, "consolidated_count": 0}

    Completed (with optional errors list)::

        {"status": "completed", "clusters_found": int, "consolidated_count": int}
        # + optional "errors": list[str]

    Dry-run (via ``consolidate_cycle(dry_run=True)``)::

        {"dry_run": True, "clusters": list, "consolidated_count": 0}
    """

    status: str
    reason: str
    clusters_found: int
    consolidated_count: int
    errors: list[str]
    dry_run: bool
    clusters: list[dict[str, object]]


class PublishLearningsResult(TypedDict):
    """Return shape of ``publish_learnings()`` and ``_step_publish_learnings()``."""

    published: int
    skipped: int
    unchanged: int
    errors: int
    skipped_reason: str | None


# Alias: PublishResult is structurally identical to PublishLearningsResult.
# It names the return at the publisher module boundary rather than the
# delivery-pipeline step boundary.
PublishResult = PublishLearningsResult


class BatchSendResult(TypedDict):
    """Return shape of ``BatchSender.send()`` and ``_step_batch_send()``."""

    sent: int
    failed: int
    remaining: int
    skipped_reason: str | None


class CeremonyFeedbackStepResult(TypedDict, total=False):
    """Return shape of ``_step_ceremony_feedback()``.

    Success path (escalation fired)::

        {"recorded": True, "auto_escalation": dict, "proposal": dict | None}

    Success path (no escalation)::

        {"recorded": True, "proposal": dict | None}

    Skipped / error path::

        {"skipped": True, "reason": str}
    """

    recorded: bool
    skipped: bool
    reason: str
    proposal: dict[str, object] | None
    auto_escalation: dict[str, object]


class IndexSyncResult(TypedDict):
    """Return shape of ``_do_index_sync()``."""

    status: str
    index: dict[str, object]
    roadmap: dict[str, object]
