"""Is this review about the plan I currently hold? (PRD-CORE-275-FR06)

A review is advisory feedback about a specific revision. The proposer is the
only party that can decide whether it is still current, because the reviewer
cannot know about a revision it has never seen — a reviewer that replied to a
then-current proposal has done nothing wrong.

ACK IS NOT AGREEMENT, and no verdict here implies it. ``CURRENT`` means the
findings describe the plan in hand; it does not mean the peer approves it. There
is deliberately no value in this enum that any caller could read as consent.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class Currency(str, Enum):
    """Whether a review describes the currently selected proposal."""

    CURRENT = "current"
    STALE = "stale"
    OTHER_PLAN = "other_plan"


def classify(review: dict[str, Any], proposal: dict[str, Any]) -> Currency:
    """Compare a review against the proposal the caller currently holds.

    Digest is checked as well as revision because a revision number can be
    reused by a careless producer, while the digest cannot be reused without
    reproducing the whole body.
    """

    if review["plan_id"] != proposal["plan_id"]:
        return Currency.OTHER_PLAN
    if review["revision"] != proposal["revision"] or review["digest"] != proposal["digest"]:
        return Currency.STALE
    return Currency.CURRENT


def render(verdict: Currency, review: dict[str, Any]) -> str:
    """One line a human or an agent can act on, with no approval language."""

    findings = review.get("findings") or []
    head = {
        Currency.CURRENT: "CURRENT — advisory findings about the plan you hold",
        Currency.STALE: "STALE — findings describe an earlier revision, not approval of the current one",
        Currency.OTHER_PLAN: "OTHER PLAN — this review is about a different plan_id",
    }[verdict]
    lines = [head, f"plan_id={review['plan_id']} revision={review['revision']}"]
    lines.extend(f"  - {finding}" for finding in findings)
    if not findings:
        lines.append("  (no findings)")
    lines.append("A review is advisory. It is not approval, permission or completion.")
    return "\n".join(lines)
