"""Feedback nudge configuration (PRD-INFRA-132 FR07).

Owns the ``feedback`` nested sub-config on ``TRWConfig``. The nudge engine
in ``trw_mcp.state._feedback_nudge`` reads these values to decide whether
and when to surface the ``/trw-feedback`` reminder once per session.

NFR04: ``feedback.proactive`` defaults to ``False`` -- the engine is fully
opt-in. No counters can produce a nudge while the gate is off.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FeedbackFields(BaseModel):
    """Feedback-nudge sub-configuration (PRD-INFRA-132 FR07).

    Counters tracked per session by the nudge engine:
      - build_check fail count
      - unhandled tool-exception count
      - bug-tagged trw_learn entries (must include both ``bug`` and
        ``trw-internal`` tags)

    A nudge fires when ANY counter meets its threshold AND
    ``proactive`` is ``True``. After firing once per session it is
    throttled for the lifetime of that session.
    """

    model_config = ConfigDict(extra="ignore")

    proactive: bool = Field(
        default=False,
        description=(
            "Master opt-in gate (NFR04). When False, no nudge ever "
            "fires regardless of counter state."
        ),
    )
    build_check_fail_threshold: int = Field(
        default=3,
        ge=1,
        description="Consecutive build_check failures that arm the nudge.",
    )
    unhandled_exception_threshold: int = Field(
        default=2,
        ge=1,
        description="Unhandled tool exceptions in a session that arm the nudge.",
    )
    bug_learning_threshold: int = Field(
        default=1,
        ge=1,
        description=(
            "trw_learn entries tagged BOTH 'bug' AND 'trw-internal' that "
            "arm the nudge."
        ),
    )


class _FeedbackFields:
    """Feedback domain mixin -- mixed into _TRWConfigFields via MI."""

    # -- Feedback nudge (PRD-INFRA-132 FR07) --

    feedback: FeedbackFields = Field(default_factory=FeedbackFields)
