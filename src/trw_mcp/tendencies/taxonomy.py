"""Closed ``TendencyType`` taxonomy + total metadata registry (PRD-QUAL-109 FR-01).

This module is the **single source of truth** for the AI-development tendency
vocabulary. The FR-03 report builder, the FR-02 detector registry, and any
runtime agent (US-2) all import ``TendencyType`` and ``TENDENCY_METADATA`` from
here.

Substrate-First posture (PRD-DIST-218 §3): ``TENDENCY_METADATA`` is an
operator-curated vocabulary that **is the subject of the feature** — mirroring
the canonical domain taxonomy maintained on the distill maintenance side. It is
NOT external-system vocabulary tracking; it changes only when the operator's
empirical catalogue of tendencies changes (an audited, in-spec event), so it is
exempt from the Substrate-First maintenance-treadmill concern.

The enum is **closed**: every member must have exactly one ``TendencyMetadata``
record and the member set is fixed at the audited 9. Adding a member without a
metadata record fails ``test_tendency_taxonomy_metadata_total``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TendencyType(str, Enum):
    """Closed taxonomy of AI-development tendencies (the audited 9-member set).

    Seeded verbatim-in-intent from the empirical catalogue
    (``LOOP-SWARM-PATHOLOGY-REVIEW-2026-06-10.md`` §3 +
    ``AUDIT-2026-05-17-EXTERNAL-OPERATOR-REVIEW.md`` §3).
    """

    FAKE_DONE = "fake_done"
    PREMATURE_SCAFFOLDING = "premature_scaffolding"
    NIH_UNDER_RESEARCH = "nih_under_research"
    QUOTA_GAMING = "quota_gaming"
    BENCHMARK_SATURATION = "benchmark_saturation"
    DOC_DRIFT = "doc_drift"
    CLAIM_PROPAGATION = "claim_propagation"
    ADDITIVE_BACKLOG = "additive_backlog"
    SELF_SILENCING = "self_silencing"


@dataclass(frozen=True)
class TendencyMetadata:
    """Per-tendency metadata record (FR-01).

    ``countermeasure_pointer`` is a *string reference* to the gate / PRD /
    research-doc that counters the tendency — NEVER an executable gate. This
    module performs no gate execution.
    """

    name: str
    description: str
    detection_signals: tuple[str, ...]
    countermeasure_pointer: str


# The total registry: keyed by EVERY ``TendencyType`` member (enforced by test).
TENDENCY_METADATA: dict[TendencyType, TendencyMetadata] = {
    TendencyType.FAKE_DONE: TendencyMetadata(
        name="Fake-done (Potemkin gate)",
        description=(
            "Marking work complete when the gate is only superficially satisfied — "
            "tests assert existence not behavior, a function is wired but never "
            "called, status flips to done over a stub."
        ),
        detection_signals=(
            "self-reported completion without verifier-owned proof",
            "tests that assert presence/import rather than behavior",
        ),
        countermeasure_pointer=(
            "LOOP-SWARM-PATHOLOGY-REVIEW-2026-06-10.md §4 (verifier-ownership); OPERATOR-VISION-2026-06-10.md §D20"
        ),
    ),
    TendencyType.PREMATURE_SCAFFOLDING: TendencyMetadata(
        name="Premature scaffolding (build-for-future)",
        description=(
            "Building abstraction, config surfaces, or extension points for "
            "anticipated-but-unrequested needs before any consumer exists."
        ),
        detection_signals=(
            "new modules/abstractions with no production consumer at HEAD",
            "config fields/flags added but never read",
        ),
        countermeasure_pointer="OPERATOR-VISION-2026-06-10.md §D20; V13",
    ),
    TendencyType.NIH_UNDER_RESEARCH: TendencyMetadata(
        name="Not-invented-here / under-research",
        description=(
            "Re-implementing a capability that an existing library or prior art "
            "already provides, without a buy-vs-build research pass first."
        ),
        detection_signals=(
            "hand-rolled implementation of a well-served external capability",
            "no research-first gate evidence before a build decision",
        ),
        countermeasure_pointer="OPERATOR-VISION-2026-06-10.md §D19-D20",
    ),
    TendencyType.QUOTA_GAMING: TendencyMetadata(
        name="Quota gaming (count-targeting)",
        description=(
            "Optimizing for a count target (e.g. PRDs-per-cycle) rather than an "
            "outcome — uniform PRD bundle sizes across consecutive cycles."
        ),
        detection_signals=(
            "PRD count exactly 5 or 6 across many consecutive cycles",
            "cycle headlines emphasizing bundle counts over moved outcomes",
        ),
        countermeasure_pointer=(
            "AUDIT-2026-05-17-EXTERNAL-OPERATOR-REVIEW.md §3 Pattern A; outcome-gated cycle closure (LOOP-SWARM §5 R1)"
        ),
    ),
    TendencyType.BENCHMARK_SATURATION: TendencyMetadata(
        name="Benchmark saturation (engineered-corpus)",
        description=(
            "The saturated-benchmark treadmill — re-applying the same learning "
            "N-times or re-snapshotting byte-identical rounds once the benchmark "
            "no longer discriminates. Detection signal is the engineered corpus "
            "itself, not a separate tendency."
        ),
        detection_signals=(
            '"Nth application of L-X" with incrementing N (N >= 5)',
            '"ROUND-N byte-identical to ROUND-{N-1}" in cycle headlines',
        ),
        countermeasure_pointer=(
            "AUDIT-2026-05-17-EXTERNAL-OPERATOR-REVIEW.md §3 Pattern D; EVAL-CONSEQUENCE-COUPLING-AUDIT-2026-06-10.md"
        ),
    ),
    TendencyType.DOC_DRIFT: TendencyMetadata(
        name="Doc drift",
        description=(
            "Documentation diverging from the implemented reality — counts, "
            "status, or claims in docs no longer match the code at HEAD."
        ),
        detection_signals=(
            "doc claims unverifiable against current source",
            "stale counts / status rows in research or PRD docs",
        ),
        countermeasure_pointer="OPERATOR-VISION-2026-06-10.md §D6",
    ),
    TendencyType.CLAIM_PROPAGATION: TendencyMetadata(
        name="Claim propagation",
        description=(
            "An unverified 'delivered' claim propagating across handoffs and "
            "summaries without a wiring/integration check (delivered != wired)."
        ),
        detection_signals=(
            "repeated 'delivered'/'complete' claims with no integration evidence",
            "INTEGRATION-ISLANDS: a surface built but never consumed",
        ),
        countermeasure_pointer="INTEGRATION-ISLANDS-AUDIT-2026-06-10.md",
    ),
    TendencyType.ADDITIVE_BACKLOG: TendencyMetadata(
        name="Additive backlog (ledger-flooding)",
        description=(
            "Manufacturing closure work — adding stubs only to close them in a "
            "later cycle, or status-flip-only PRDs that inflate the ledger "
            "without moving an outcome."
        ),
        detection_signals=(
            '"Closes FR-X stub from cycle K" headlines (>2 in one arc)',
            "PRDs whose only substantive change is status: partial -> live (>3/cycle)",
        ),
        countermeasure_pointer=(
            "AUDIT-2026-05-17-EXTERNAL-OPERATOR-REVIEW.md §3 Pattern E; stub-deadline enforcement (LOOP-SWARM §5)"
        ),
    ),
    TendencyType.SELF_SILENCING: TendencyMetadata(
        name="Self-silencing (escalation-decay)",
        description=(
            "Escalation fatigue — disabling the LOOP-STOP mechanism or "
            "suppressing blocking signals to keep a loop running."
        ),
        detection_signals=(
            "LOOP-STOP / escalation mechanism disabled mid-run",
            "blocking findings demoted to advisory without operator sign-off",
        ),
        countermeasure_pointer=(
            "LOOP-SWARM-PATHOLOGY-REVIEW-2026-06-10.md §3 P4; "
            "AUDIT-2026-05-17-EXTERNAL-OPERATOR-REVIEW.md §4 (LOOP-STOP paradox)"
        ),
    ),
}


__all__ = ["TENDENCY_METADATA", "TendencyMetadata", "TendencyType"]
