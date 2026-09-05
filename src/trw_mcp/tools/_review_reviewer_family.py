"""Digest-verified reviewer-family derivation — PRD-CORE-255-FR02/FR04.

Belongs to the ``_review_receipt_writer.py`` module, which calls
:func:`resolve_reviewer_fields` while minting a :class:`ReviewReceipt`. Split
out as a sibling so the writer stays under the module-size gate and so the
"what does ``cross_model`` cost you" rule has exactly one home.

The rule, stated once:

* Old behavior keyed ``reviewer_family`` on the INTERNAL dispatch ``mode``
  string, so a manual-mode call relaying real cross-family auditor findings was
  stamped ``human_or_self`` and became invisible to any gate that trusted the
  receipt.
* Simply trusting ``reviewer.source == "cross_model"`` instead would be worse:
  the strongest family label would be mintable by assertion, and FR04's gate
  would be satisfiable by anyone who typed the word.
* So a MANUAL-mode ``cross_model`` claim is EARNED, not asserted: the caller
  names an external auditor artifact under the project root and proves knowledge
  of its exact bytes by supplying its SHA-256 digest as ``reviewer_receipt_id``.
  Anything less downgrades, with a machine-readable reason.

The in-process ``auto``/``cross_model`` dispatch paths are untouched — there the
dispatch itself ran a real provider call, which IS the evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

#: Bounded read for the external artifact digest. An auditor transcript is text;
#: a caller pointing the verifier at a multi-gigabyte file must not be able to
#: stall the review path. A file larger than this fails verification (it is not
#: the kind of artifact this check exists to bind), it never silently passes.
MAX_EXTERNAL_RECEIPT_BYTES: int = 16 * 1024 * 1024

#: Machine-readable reasons a ``cross_model`` claim was refused. Closed set —
#: every downgrade names exactly which condition failed, in the structlog event
#: AND in the ``trw_review`` response, because a silent downgrade is how the
#: original mislabel survived (every review this session reported single_family
#: while agy/codex had in fact run).
DOWNGRADE_PATH_MISSING = "external_receipt_path_missing"
DOWNGRADE_PATH_UNREADABLE = "external_receipt_path_unreadable"
DOWNGRADE_DIGEST_MISMATCH = "external_receipt_digest_mismatch"

#: The family label a receipt carries when a cross-family audit was VERIFIED.
FAMILY_CROSS_MODEL = "cross_model"
#: Coverage label mirrored into the ``trw_review`` response on verification;
#: matches ``_review_cross_model.COVERAGE_CROSS_FAMILY``.
COVERAGE_CROSS_FAMILY = "cross_family"


@dataclass(frozen=True)
class ReviewerFields:
    """Reviewer provenance stamped onto a :class:`ReviewReceipt`."""

    origin: str
    identity: str
    family: str
    external_receipt_digest: str = ""
    family_downgraded_reason: str = ""

    @property
    def verified_cross_model(self) -> bool:
        """True only for a family earned through a digest-verified artifact.

        A self-declared ``reviewer_source=cross_model`` never reaches here with
        ``family == FAMILY_CROSS_MODEL`` unless the digest matched, so this is
        the single predicate FR04's gate keys on for its cross-model leg.
        """
        return self.family == FAMILY_CROSS_MODEL and bool(self.external_receipt_digest)


def _resolve_external_receipt(raw_path: str, project_root: Path) -> Path | None:
    """Resolve ``raw_path`` to a readable regular file inside ``project_root``.

    Containment is checked AFTER symlink resolution, so a symlink pointing out
    of the tree does not smuggle an arbitrary filesystem read into the digest.
    """
    try:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        resolved = candidate.resolve()
        resolved.relative_to(project_root.resolve())
        return resolved if resolved.is_file() else None
    except (OSError, ValueError):
        return None


def digest_external_receipt(raw_path: str, project_root: Path) -> str | None:
    """SHA-256 hex digest of the external auditor artifact, or ``None``.

    ``None`` covers every unreadable case — outside the project root, absent, a
    directory, an OS error, or over :data:`MAX_EXTERNAL_RECEIPT_BYTES`. The
    caller turns that into a downgrade, never into a pass.
    """
    resolved = _resolve_external_receipt(raw_path, project_root)
    if resolved is None:
        return None
    try:
        if resolved.stat().st_size > MAX_EXTERNAL_RECEIPT_BYTES:
            logger.warning("external_receipt_too_large", path=str(resolved), limit=MAX_EXTERNAL_RECEIPT_BYTES)
            return None
        return hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        return None


def _downgraded_family(source: str) -> str:
    """The family an unverified claim falls back to (FR02)."""
    return "agent" if source == "subagent" else "human_or_self"


def resolve_reviewer_fields(
    review_data: dict[str, object],
    mode: str,
    project_root: Path,
) -> ReviewerFields:
    """Derive reviewer origin/identity/family for one review artifact.

    ``cross_model`` is reachable two ways and no others:

    1. an in-process ``mode == "cross_model"`` dispatch (the provider call is the
       evidence), or
    2. a manual-mode claim whose ``external_receipt_path`` digests to the
       supplied ``reviewer_receipt_id``.

    Every other manual-mode ``cross_model`` claim is downgraded and carries a
    non-empty :attr:`ReviewerFields.family_downgraded_reason`.
    """
    reviewer = review_data.get("reviewer")
    block = reviewer if isinstance(reviewer, dict) else {}
    origin = str(block.get("source", "unknown") or "unknown")
    identity = str(block.get("receipt_id") or block.get("session_id") or block.get("run_id") or origin)
    dispatch_family = FAMILY_CROSS_MODEL if mode == "cross_model" else ("agent" if mode == "auto" else "human_or_self")

    if mode != "manual" or origin != FAMILY_CROSS_MODEL:
        return ReviewerFields(origin=origin, identity=identity, family=dispatch_family)

    claimed_digest = str(block.get("receipt_id") or "").strip().lower()
    raw_path = str(review_data.get("external_receipt_path") or "").strip()
    if not raw_path:
        reason = DOWNGRADE_PATH_MISSING
    else:
        actual = digest_external_receipt(raw_path, project_root)
        if actual is None:
            reason = DOWNGRADE_PATH_UNREADABLE
        elif actual != claimed_digest:
            reason = DOWNGRADE_DIGEST_MISMATCH
        else:
            logger.info("reviewer_family_verified", family=FAMILY_CROSS_MODEL, external_receipt_digest=actual)
            return ReviewerFields(
                origin=origin,
                identity=identity,
                family=FAMILY_CROSS_MODEL,
                external_receipt_digest=actual,
            )

    logger.warning("reviewer_family_downgraded", reason=reason, claimed_source=origin, mode=mode)
    return ReviewerFields(
        origin=origin,
        identity=identity,
        family=_downgraded_family(origin),
        family_downgraded_reason=reason,
    )


__all__ = [
    "COVERAGE_CROSS_FAMILY",
    "DOWNGRADE_DIGEST_MISMATCH",
    "DOWNGRADE_PATH_MISSING",
    "DOWNGRADE_PATH_UNREADABLE",
    "FAMILY_CROSS_MODEL",
    "MAX_EXTERNAL_RECEIPT_BYTES",
    "ReviewerFields",
    "digest_external_receipt",
    "resolve_reviewer_fields",
]
