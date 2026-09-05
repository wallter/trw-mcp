"""The ONE predicate deciding whether an adversarial posture is proven (FR04).

Belongs to the ``_review_receipt_writer.py`` / ``_delivery_safety_critical_gate.py``
pair. Both call :func:`adversarial_source_is_verified`, so "the flag was trusted
but the rubric was not" (or the reverse) cannot arise from two implementations
drifting apart — there is only one.

Exactly two postures qualify, and both cost the caller something a bare
assertion does not:

* a ``cross_model`` family EARNED through a digest-verified external artifact
  (:mod:`trw_mcp.tools._review_reviewer_family`), or
* an ``operator`` origin whose receipt id RESOLVES to a signed, unexpired
  approval bound to THIS review (:mod:`trw_mcp.state.review_signoffs`).

Before PRD-CORE-255's 2026-09-04 amendment the operator leg accepted any
non-empty string, so the reviewed agent could mint its own operator sign-off,
claim ``adversarial_pass`` on a zero-finding review, and satisfy the whole gate.
Everything that is not one of the two proven postures is refused WITH a reason;
there is no silent ``False`` here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

from trw_mcp.state.review_signoffs import resolve_review_signoff
from trw_mcp.tools._review_reviewer_family import ReviewerFields

logger = structlog.get_logger(__name__)

#: The reviewer is neither a digest-verified cross-model auditor nor an operator
#: at all (``self``/``subagent``/unknown). Distinct from every sign-off reason:
#: nothing was wrong with an approval, there was no operator posture to prove.
REASON_NOT_INDEPENDENT = "adversarial_source_not_independent"


@dataclass(frozen=True)
class AdversarialVerification:
    """Whether the posture is proven, and — when it is not — precisely why."""

    verified: bool
    reason: str = ""


def adversarial_source_is_verified(
    fields: ReviewerFields,
    *,
    review_refs: Sequence[str],
    trw_dir: Path,
    now: datetime | None = None,
) -> AdversarialVerification:
    """PRD-CORE-255-FR04 condition (1): is this reviewer posture independently proven?

    *review_refs* are the identifiers this review may be approved under — its
    ``review_id`` and its content-binding ``scope_digest``. An approval bound to
    any other reference is refused, so one sign-off cannot authorize a second
    review.

    This single predicate decides BOTH whether the adversarial rubric is realized
    and whether an ``adversarial_pass`` claim is honored.
    """
    if fields.verified_cross_model:
        return AdversarialVerification(verified=True)
    if fields.origin != "operator":
        # A downgraded cross_model claim already carries its own reason from the
        # family resolver; the writer prefers that one and this is the fallback.
        return AdversarialVerification(verified=False, reason=REASON_NOT_INDEPENDENT)
    resolution = resolve_review_signoff(trw_dir, fields.identity, review_refs, now=now)
    if resolution.verified:
        logger.info("adversarial_source_verified", posture="operator", approver=resolution.approver)
        return AdversarialVerification(verified=True)
    logger.warning("adversarial_source_refused", posture="operator", reason=resolution.reason)
    return AdversarialVerification(verified=False, reason=resolution.reason)


__all__ = [
    "REASON_NOT_INDEPENDENT",
    "AdversarialVerification",
    "adversarial_source_is_verified",
]
