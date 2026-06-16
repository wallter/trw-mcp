"""FR-02 — structured ProbeResult schema (PRD-CORE-144).

Asserts schema pinning, lossless round-trip, and validation bounds.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trw_mcp.models.probe import (
    AssumptionSet,
    DissentEntry,
    ProbeAssumption,
    ProbeBudgetStatus,
    ProbeEvidence,
    ProbeResult,
    ResourceBudget,
)


def _result(**over: object) -> ProbeResult:
    base: dict[str, object] = {
        "hypothesis": "parse 50MB JSONL in <5s",
        "hypothesis_id": "HYP-1",
        "verdict": "refutes",
        "evidence": ProbeEvidence(stdout="8.2", exit_code=0, wall_ms=8234),
        "confidence": 0.92,
        "ts": datetime(2026, 4, 16, 14, 22, 17, tzinfo=timezone.utc),
        "run_id": "run-1",
    }
    base.update(over)
    return ProbeResult(**base)  # type: ignore[arg-type]


def test_probe_result_roundtrip_lossless() -> None:
    # FR-02 A2: round-trip JSON serialization is lossless.
    original = _result()
    restored = ProbeResult.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.evidence.wall_ms == 8234
    assert restored.verdict == "refutes"


def test_probe_result_confidence_out_of_range_raises() -> None:
    # FR-02 A3: confidence outside [0,1] raises ValidationError.
    with pytest.raises(ValidationError):
        _result(confidence=1.5)
    with pytest.raises(ValidationError):
        _result(confidence=-0.1)


def test_probe_result_invalid_verdict_rejected() -> None:
    with pytest.raises(ValidationError):
        _result(verdict="maybe")


def test_resource_budget_bounds_enforced() -> None:
    # FR-03: default 256MB, max 2GB, min 16MB.
    assert ResourceBudget().memory_mb == 256
    with pytest.raises(ValidationError):
        ResourceBudget(memory_mb=4096)
    with pytest.raises(ValidationError):
        ResourceBudget(memory_mb=8)


def test_resource_budget_is_frozen() -> None:
    budget = ResourceBudget()
    with pytest.raises(ValidationError):
        budget.memory_mb = 512  # type: ignore[misc]


def test_evidence_wall_ms_non_negative() -> None:
    with pytest.raises(ValidationError):
        ProbeEvidence(wall_ms=-1)


def test_assumption_defaults_polarity_positive() -> None:
    a = ProbeAssumption(hypothesis_id="H1", claim="x<5s")
    assert a.polarity == "positive"
    assert a.probe_result_ref is None


def test_dissent_entry_links_evidence_ref() -> None:
    entry = DissentEntry(
        hypothesis_id="H1",
        claim="x<5s",
        probe_verdict="refutes",
        probe_evidence_ref="probe-0042",
    )
    assert entry.probe_evidence_ref == "probe-0042"


def test_assumption_set_accepts_unique_hypothesis_ids() -> None:
    """FR-05 A2: a plan with unique hypothesis_ids validates."""
    plan = AssumptionSet(
        assumptions=[
            ProbeAssumption(hypothesis_id="H1", claim="a"),
            ProbeAssumption(hypothesis_id="H2", claim="b"),
        ]
    )
    assert {a.hypothesis_id for a in plan.assumptions} == {"H1", "H2"}


def test_assumption_set_rejects_duplicate_hypothesis_id() -> None:
    """FR-05 A2: a duplicate hypothesis_id within a plan raises ValidationError."""
    with pytest.raises(ValidationError) as exc:
        AssumptionSet(
            assumptions=[
                ProbeAssumption(hypothesis_id="DUP", claim="a"),
                ProbeAssumption(hypothesis_id="DUP", claim="b"),
            ]
        )
    assert "duplicate hypothesis_id" in str(exc.value)


def test_budget_status_roundtrip() -> None:
    status = ProbeBudgetStatus(used=2, remaining=1, total=3, planning_mode="TRIANGULATED_WITH_PROBE")
    restored = ProbeBudgetStatus.model_validate_json(status.model_dump_json())
    assert restored == status
