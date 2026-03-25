"""Trust state TypedDicts (state/trust.py)."""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class TrustLevelResult(TypedDict):
    """Return shape of ``trust_level_calculate()``."""

    tier: str
    session_count: int
    review_mode: str
    review_sample_rate: float | None
    locked: bool
    lock_reason: str | None


class HumanReviewResult(TypedDict):
    """Return shape of ``requires_human_review()``."""

    required: bool
    reason: str
    override_tier: bool


class TrustSessionIncrementResult(TypedDict, total=True):
    """Return shape of ``increment_session_count()`` in ``state/trust.py``.

    Note: distinct from ``TrustIncrementResult`` which covers the
    ``_step_trust_increment()`` delivery-pipeline step.
    """

    session_count: int
    previous_tier: str
    new_tier: str
    transitioned: bool
    # Added for observability in _step_trust_increment():
    reason: NotRequired[str]


class TrustLevelQueryResult(TypedDict, total=False):
    """Return shape of ``trw_trust_level`` MCP tool.

    Extends ``TrustLevelResult`` with two optional keys that are only present
    when ``security_tags`` is supplied by the caller.

    Always-present (inherited from TrustLevelResult contract):
    ``tier``, ``session_count``, ``review_mode``, ``review_sample_rate``,
    ``locked``, ``lock_reason``.

    Optional (present only when security_tags evaluated):
    ``review_required``, ``review_reason``.
    """

    tier: str
    session_count: int
    review_mode: str
    review_sample_rate: float | None
    locked: bool
    lock_reason: str | None
    # populated when security_tags were provided
    review_required: bool
    review_reason: str
