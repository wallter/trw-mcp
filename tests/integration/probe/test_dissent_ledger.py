"""FR-06 — Dissent Ledger integration (PRD-CORE-144).

This is the PRD's declared ``wiring_test`` (frontmatter seams[].wiring_test).
It proves the full consumer path: a probe runs through the shared sandbox, a
plan-branch assumption is linked to it, and when the verdict contradicts the
claim a DissentEntry is recorded — linked to the ProbeResult by evidence ref.
"""

from __future__ import annotations

import sys
from pathlib import Path

from trw_mcp.models.probe import ProbeAssumption
from trw_mcp.probe.harness import run_probe
from trw_mcp.probe.linkage import (
    read_dissent_ledger,
    record_dissent_if_contradicted,
)


def test_refuted_positive_claim_records_dissent(tmp_path: Path) -> None:
    # Branch A claims "x exits clean" (positive). The probe exits non-zero ->
    # verdict=refutes -> contradiction -> ledger entry.
    assumption = ProbeAssumption(hypothesis_id="HYP-A1", claim="exits clean", polarity="positive")
    result = run_probe(
        hypothesis="exits clean",
        command=f'{sys.executable} -c "import sys; sys.exit(2)"',
        run_id="run-1",
        timeout_s=10,
        hypothesis_id="HYP-A1",
    )
    assert result.verdict == "refutes"

    ledger = tmp_path / "dissent.jsonl"
    entry = record_dissent_if_contradicted(
        assumption,
        result,
        ledger_path=ledger,
        probe_evidence_ref="probe-evidence-0001",
    )
    assert entry is not None
    # FR-06 A2: entry linked to the ProbeResult by evidence ref.
    assert entry.probe_evidence_ref == "probe-evidence-0001"

    persisted = read_dissent_ledger(ledger)
    assert len(persisted) == 1
    assert persisted[0].hypothesis_id == "HYP-A1"
    assert persisted[0].probe_verdict == "refutes"


def test_supported_claim_records_no_dissent(tmp_path: Path) -> None:
    assumption = ProbeAssumption(hypothesis_id="HYP-A2", claim="exits clean", polarity="positive")
    result = run_probe(
        hypothesis="exits clean",
        command=f'{sys.executable} -c "pass"',
        run_id="run-2",
        timeout_s=10,
        hypothesis_id="HYP-A2",
    )
    assert result.verdict == "supports"
    ledger = tmp_path / "dissent.jsonl"
    entry = record_dissent_if_contradicted(assumption, result, ledger_path=ledger, probe_evidence_ref="ref")
    assert entry is None
    assert read_dissent_ledger(ledger) == []


def test_dissent_coverage_at_least_95pct(tmp_path: Path) -> None:
    # FR-06 A3: >= 95% of contradicted claims produce a ledger entry.
    ledger = tmp_path / "dissent.jsonl"
    contradicted = 0
    recorded = 0
    for i in range(20):
        assumption = ProbeAssumption(hypothesis_id=f"H{i}", claim="exits clean", polarity="positive")
        result = run_probe(
            hypothesis="exits clean",
            command=f'{sys.executable} -c "import sys; sys.exit(1)"',
            run_id=f"run-{i}",
            timeout_s=10,
            hypothesis_id=f"H{i}",
        )
        contradicted += 1
        if record_dissent_if_contradicted(assumption, result, ledger_path=ledger, probe_evidence_ref=f"ref-{i}"):
            recorded += 1
    assert recorded / contradicted >= 0.95
    assert len(read_dissent_ledger(ledger)) == recorded
