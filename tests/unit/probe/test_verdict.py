"""FR-06 — polarity + contradiction detection (PRD-CORE-144)."""

from __future__ import annotations

from datetime import datetime, timezone

from trw_mcp.models.probe import ProbeAssumption, ProbeEvidence, ProbeResult
from trw_mcp.probe.verdict import contradicts_claim, detect_dissent


def _result(verdict: str) -> ProbeResult:
    return ProbeResult(
        hypothesis="h",
        hypothesis_id="H1",
        verdict=verdict,  # type: ignore[arg-type]
        evidence=ProbeEvidence(wall_ms=1),
        confidence=0.9,
        ts=datetime(2026, 4, 16, tzinfo=timezone.utc),
        run_id="run-1",
    )


def test_positive_claim_refuted_is_contradiction() -> None:
    assert contradicts_claim("positive", "refutes") is True


def test_positive_claim_supported_is_not_contradiction() -> None:
    assert contradicts_claim("positive", "supports") is False


def test_negative_claim_supported_is_contradiction() -> None:
    assert contradicts_claim("negative", "supports") is True


def test_inconclusive_never_contradicts() -> None:
    # RISK-005: inconclusive is first-class neutral.
    assert contradicts_claim("positive", "inconclusive") is False
    assert contradicts_claim("negative", "inconclusive") is False


def test_detect_dissent_builds_entry_on_contradiction() -> None:
    assumption = ProbeAssumption(hypothesis_id="H1", claim="x<5s", polarity="positive")
    entry = detect_dissent(assumption, _result("refutes"), probe_evidence_ref="probe-42")
    assert entry is not None
    assert entry.hypothesis_id == "H1"
    assert entry.claim == "x<5s"
    assert entry.probe_verdict == "refutes"
    assert entry.probe_evidence_ref == "probe-42"  # FR-06 A2: linked by ref


def test_detect_dissent_returns_none_when_no_contradiction() -> None:
    assumption = ProbeAssumption(hypothesis_id="H1", claim="x<5s", polarity="positive")
    assert detect_dissent(assumption, _result("supports"), probe_evidence_ref="p") is None
