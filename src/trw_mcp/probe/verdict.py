"""Polarity + contradiction detection for the Dissent Ledger (PRD-CORE-144 FR-06).

Belongs to the ``probe`` facade. Re-exported from ``probe/__init__.py``.

Contradiction detection uses a DECLARED polarity function, not a substring
match (FR-06 A1). A plan-branch assumption declares ``polarity`` ("positive"
or "negative"); the probe returns a ``verdict``. The contradiction table:

    polarity=positive + verdict=refutes  -> contradiction
    polarity=negative + verdict=supports -> contradiction
    verdict=inconclusive                 -> never a contradiction (RISK-005)
"""

from __future__ import annotations

from trw_mcp.models.probe import (
    DissentEntry,
    ProbeAssumption,
    ProbeResult,
    Verdict,
)


def contradicts_claim(polarity: str, verdict: Verdict) -> bool:
    """Return ``True`` when ``verdict`` contradicts an assumption ``polarity``.

    ``inconclusive`` is first-class neutral — never a contradiction — so the
    plan rubric treats it as neither pro nor con (RISK-005).
    """
    if verdict == "inconclusive":
        return False
    if polarity == "positive":
        return verdict == "refutes"
    if polarity == "negative":
        return verdict == "supports"
    # Unknown polarity: be conservative and do not manufacture dissent.
    return False


def detect_dissent(
    assumption: ProbeAssumption,
    result: ProbeResult,
    *,
    probe_evidence_ref: str,
) -> DissentEntry | None:
    """Build a :class:`DissentEntry` when ``result`` contradicts ``assumption``.

    Returns ``None`` when there is no contradiction, so the caller can
    record-when-present. The entry is linked to the full ``ProbeResult`` by
    ``probe_evidence_ref`` (FR-06 A2).
    """
    if not contradicts_claim(assumption.polarity, result.verdict):
        return None
    return DissentEntry(
        hypothesis_id=assumption.hypothesis_id,
        claim=assumption.claim,
        probe_verdict=result.verdict,
        probe_evidence_ref=probe_evidence_ref,
    )


__all__ = ["contradicts_claim", "detect_dissent"]
