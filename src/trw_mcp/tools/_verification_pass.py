"""Shared assertion + anchor verification pass (PRD-CORE-231 FR02/FR03).

Belongs to the ``_recall_assertion_verification.py`` facade. Extracted so the
recall hot path and the ``maintain-verify`` batch sweep run *the same*
computation and *the same* single persisted write, instead of two drifting
copies (§11 RISK "maintainability" mitigation).

Two seams:

* :func:`run_verification_pass` — pure-ish compute. Reads the filesystem
  (assertion + anchor verification) and returns what should be persisted.
* :func:`persist_verification_outcome` — the single ``backend.update()`` that
  writes ``assertions``, ``verification_status`` and ``anchor_validity``
  together, plus the NFR02 ``verification_status_persist_drift`` self-check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

import structlog

logger = structlog.get_logger(__name__)

VerificationStatus = Literal["stale"] | None


@dataclass(slots=True)
class VerificationOutcome:
    """Everything one entry's verification pass computed."""

    entry_id: str
    updated_assertions: list[dict[str, object]] = field(default_factory=list)
    assertion_status: dict[str, object] = field(default_factory=dict)
    passing: int = 0
    failing: int = 0
    stale: int = 0
    penalty: float = 0.0
    verification_status: VerificationStatus = None
    anchor_validity: float | None = None
    #: False when the entry HAS assertions but none could actually be checked
    #: (e.g. project_root unresolvable => every result is ``passed=None``).
    #: Nothing is persisted in that case: the historical ``first_failed_at``
    #: values would otherwise convict an entry that was never re-examined.
    verifiable: bool = True

    def update_fields(self) -> dict[str, object]:
        """The exact kwargs for the single batched ``backend.update()`` call.

        ``assertions`` is handed over as validated ``Assertion`` MODELS, not a
        pre-serialized JSON string: ``update()`` reconstructs the entry to
        recompute its sync hash, and a raw string there makes that
        reconstruction raise — which is how the FR06 write-back was silently
        dying inside the best-effort handler.
        """
        from trw_memory.models.memory import Assertion

        fields: dict[str, object] = {
            "assertions": [Assertion.model_validate(a, strict=False) for a in self.updated_assertions],
            # Scalar, last-write-wins: passing ``None`` is how a previously
            # persisted 'stale' verdict is cleared (FR02 AC2).
            "verification_status": self.verification_status,
        }
        if self.anchor_validity is not None:
            fields["anchor_validity"] = self.anchor_validity
        return fields


def _assertion_result_detail(
    entry_id: str,
    index: int,
    assertion: Any,
    result: Any,
) -> dict[str, object]:
    """Normalize verification result payloads to the recall response contract."""
    detail = cast("dict[str, object]", result.model_dump())
    detail["id"] = f"{entry_id}:{index}"
    detail.setdefault("type", getattr(assertion, "type", ""))
    detail.setdefault("pattern", getattr(assertion, "pattern", ""))
    detail.setdefault("target", getattr(assertion, "target", ""))
    return detail


def _reverify_anchors(
    raw_anchors: list[object],
    project_root: Path | None,
    entry_id: str,
) -> float | None:
    """Recompute anchor validity against the CURRENT tree (FR03).

    Returns ``None`` when nothing could be computed (no anchors, no resolvable
    project root, or a computation error) so the caller leaves the persisted
    write-time score untouched rather than overwriting it with a guess.
    """
    if not raw_anchors or project_root is None:
        return None
    try:
        from trw_memory.lifecycle.anchor_validation import compute_anchor_validity

        anchors = [a for a in raw_anchors if isinstance(a, dict)]
        if not anchors:
            return None
        return compute_anchor_validity(
            cast("list[dict[str, object]]", anchors),
            str(project_root),
            learning_id=entry_id,
        )
    except Exception:  # justified: fail-open, re-verification is best-effort
        logger.debug("anchor_revalidation_skipped", entry_id=entry_id, exc_info=True)
        return None


def run_verification_pass(
    entry_id: str,
    raw_assertions: list[object],
    raw_anchors: list[object],
    *,
    assertion_failure_penalty: float,
    assertion_stale_threshold_days: int,
    project_root: Path | None,
    now: datetime | None = None,
) -> VerificationOutcome:
    """Verify one entry's assertions and re-verify its anchors.

    Args:
        entry_id: The learning/memory id (used for result ids + marker bonus).
        raw_assertions: Serialized assertion dicts from the stored entry.
        raw_anchors: Serialized anchor dicts from the stored entry.
        assertion_failure_penalty: ``TRWConfig.assertion_failure_penalty``.
        assertion_stale_threshold_days: ``TRWConfig.assertion_stale_threshold_days``.
        project_root: Repo root for filesystem-scoped verification, or ``None``.
        now: Injectable clock for tests; defaults to ``datetime.now(utc)``.

    Returns:
        A :class:`VerificationOutcome`. Never raises for a per-entry failure —
        the caller keeps scanning the remaining entries.
    """
    from trw_memory.lifecycle.verification import verify_assertions
    from trw_memory.models.memory import Assertion

    moment = now or datetime.now(timezone.utc)
    stale_threshold = moment - timedelta(days=assertion_stale_threshold_days)
    outcome = VerificationOutcome(entry_id=entry_id)
    outcome.anchor_validity = _reverify_anchors(raw_anchors, project_root, entry_id)

    if not raw_assertions:
        return outcome

    assertions_list = [Assertion.model_validate(a, strict=False) for a in raw_assertions if isinstance(a, dict)]
    results = verify_assertions(assertions_list, project_root)

    outcome.passing = sum(1 for r in results if r.passed is True)
    outcome.failing = sum(1 for r in results if r.passed is False)
    outcome.stale = sum(1 for r in results if r.passed is None)
    outcome.verifiable = any(r.passed is not None for r in results)
    outcome.assertion_status = {
        "passing": outcome.passing,
        "failing": outcome.failing,
        "stale": outcome.stale,
        "details": [
            _assertion_result_detail(entry_id, index, assertion, result)
            for index, (assertion, result) in enumerate(zip(assertions_list, results, strict=False), start=1)
        ],
    }
    if outcome.failing > 0 and results:
        outcome.penalty = assertion_failure_penalty * (outcome.failing / len(results))

    # FR06: fold verification results back into the stored assertion payload.
    for assertion, result in zip(assertions_list, results, strict=False):
        # mode="json" is load-bearing: a plain model_dump() leaves datetimes as
        # objects, so json.dumps() raised TypeError and the whole persist was
        # swallowed by the best-effort handler for any assertion that already
        # carried a first_failed_at — i.e. exactly the stale candidates.
        a_dict = assertion.model_dump(mode="json")
        a_dict["last_result"] = result.passed
        a_dict["last_verified_at"] = moment.isoformat()
        a_dict["last_evidence"] = result.evidence
        # FR08: track first_failed_at transitions.
        if result.passed is False:
            if assertion.first_failed_at is None:
                a_dict["first_failed_at"] = moment.isoformat()
        elif result.passed is True:
            a_dict["first_failed_at"] = None
        outcome.updated_assertions.append(a_dict)

    # FR08: every assertion failing for longer than the threshold => stale.
    # An UNVERIFIABLE result (``passed is None``) is not a failure — requiring
    # every assertion to have actually failed on THIS run stops an unresolvable
    # project root from convicting an entry on nothing but historical timestamps.
    all_failing_now = bool(results) and all(r.passed is False for r in results)
    all_persistently_failing = (
        all_failing_now
        and len(outcome.updated_assertions) > 0
        and all(
            a.get("first_failed_at") is not None and datetime.fromisoformat(str(a["first_failed_at"])) < stale_threshold
            for a in outcome.updated_assertions
        )
    )
    if all_persistently_failing:
        outcome.verification_status = "stale"
    return outcome


def persist_verification_outcome(backend: Any, outcome: VerificationOutcome) -> bool:
    """Write an outcome through in ONE ``backend.update()`` call (FR02/FR03).

    Returns ``True`` when the write landed. Emits the NFR02
    ``verification_status_persist_drift`` warning when the value the caller
    computed is not the value that came back from storage — that warning firing
    means the persistence wiring is broken, which is a P1 bug, not a design gap.

    An outcome whose assertions could not be checked at all is skipped (DEBUG,
    no exception) — the PRD-CORE-086-FR09 degradation contract.
    """
    if not outcome.verifiable:
        logger.debug("verification_pass_unverifiable_skipped", entry_id=outcome.entry_id)
        return False
    try:
        updated = backend.update(outcome.entry_id, **outcome.update_fields())
    except Exception:  # justified: persist is best-effort, recall must not fail
        logger.debug("assertion_result_persist_failed", entry_id=outcome.entry_id, exc_info=True)
        return False

    persisted = getattr(updated, "verification_status", _UNCHECKED)
    if persisted is _UNCHECKED or not (persisted is None or isinstance(persisted, str)):
        # A backend that does not return a real entry (test double, YAML shim)
        # gives us nothing to compare against — say so instead of guessing.
        logger.debug("verification_status_persist_uncheckable", entry_id=outcome.entry_id)
        return True
    if persisted != outcome.verification_status:
        logger.warning(
            "verification_status_persist_drift",
            entry_id=outcome.entry_id,
            computed=outcome.verification_status,
            persisted=persisted,
        )
    return True


class _Unchecked:
    """Sentinel for 'the backend returned nothing we can self-check against'."""


_UNCHECKED = _Unchecked()


__all__ = [
    "VerificationOutcome",
    "VerificationStatus",
    "persist_verification_outcome",
    "run_verification_pass",
]
