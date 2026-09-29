"""FR-02 — structured ProbeResult schema (PRD-CORE-144).

Asserts schema pinning, lossless round-trip, and validation bounds.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from trw_mcp.models.probe import (
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


def test_budget_status_roundtrip() -> None:
    status = ProbeBudgetStatus(used=2, remaining=1, total=3)
    restored = ProbeBudgetStatus.model_validate_json(status.model_dump_json())
    assert restored == status
