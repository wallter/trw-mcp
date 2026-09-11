"""FR06 — predicate coverage, measured READ-ONLY over PRD-CORE-190."""

from __future__ import annotations

import subprocess
from pathlib import Path

from trw_mcp.wiring.checks.predicate import check_predicate_coverage, measure_wiring_gate_coverage
from trw_mcp.wiring.config import DEFAULT_CONFIG
from trw_mcp.wiring.detector import DetectorResult
from trw_mcp.wiring.model import ContractKind, EdgeClass
from trw_mcp.wiring.registry import build_registry

MEASURED_SUBJECT = "trw-mcp/src/trw_mcp/state/validation/_prd_scoring_wiring.py"


def test_wiring_gate_coverage_below_floor(live_result: DetectorResult, repo_root: Path) -> None:
    activating, total = measure_wiring_gate_coverage(repo_root, DEFAULT_CONFIG.prd_glob)
    assert total > 2000, f"expected the full PRD corpus as the domain, measured {total}"
    assert activating / total < 0.05, f"measured coverage {activating}/{total} is no longer below the floor"

    finding = next(f for f in live_result.findings if f.contract_id == "gate:prd-core-190-wiring")
    assert finding.edge_class is EdgeClass.PREDICATE_COVERAGE
    assert f"{activating} of {total}" in finding.evidence


def test_measures_the_live_predicate_not_a_copy() -> None:
    """Importing the real ``_classify_fr_surface`` is the point.

    A copied predicate drifts and silently starts measuring something else — the
    same class of defect this whole detector exists to catch.
    """
    source = Path(__file__).resolve().parents[2] / "src/trw_mcp/wiring/checks/predicate.py"
    text = source.read_text(encoding="utf-8")
    assert "from trw_mcp.state.validation._prd_scoring_wiring import _classify_fr_surface" in text


def test_detector_never_writes_to_the_measured_subject(repo_root: Path) -> None:
    """NFR07 + PRD-CORE-231 ownership: FR06 measures CORE-190 and must not modify it."""
    repo = repo_root
    diff = subprocess.run(
        ["git", "status", "--porcelain", "--", MEASURED_SUBJECT],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert diff.returncode == 0, diff.stderr
    assert diff.stdout.strip() == "", f"the detector's working tree modifies {MEASURED_SUBJECT}: {diff.stdout!r}"


def test_gate_above_floor_produces_no_finding(repo_root: Path, tmp_path: Path) -> None:
    """A gate covering a meaningful share of its domain is silent — the check is not vacuous."""
    prds = tmp_path / "prds"
    prds.mkdir()
    for index in range(4):
        (prds / f"PRD-{index}.md").write_text(
            "---\nip_tier: public\n---\n\n### FR01 — thing\n\n- **Priority**: Must Have\n",
            encoding="utf-8",
        )
    contract = next(c for c in build_registry(repo_root) if c.kind is ContractKind.GATE_PREDICATE)
    findings = check_predicate_coverage(tmp_path, contract, floor=0.05, prd_glob="prds/*.md")
    assert not findings, [f.render() for f in findings]


def test_empty_domain_is_a_finding_not_a_pass(repo_root: Path, tmp_path: Path) -> None:
    """A vacuous gate reports a finding; "nothing to check" must never look like "checked"."""
    contract = next(c for c in build_registry(repo_root) if c.kind is ContractKind.GATE_PREDICATE)
    findings = check_predicate_coverage(tmp_path, contract, floor=0.05, prd_glob="prds/*.md")
    assert [f.edge_class for f in findings] == [EdgeClass.PREDICATE_COVERAGE]
    assert "matched 0 PRDs" in findings[0].evidence
