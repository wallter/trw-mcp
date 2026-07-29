"""FR09 — classification and actionable reporting."""

from __future__ import annotations

import pytest

from trw_mcp.wiring.baseline import BASELINE, BaselineEntry, partition
from trw_mcp.wiring.detector import DetectorResult
from trw_mcp.wiring.model import EdgeClass, Finding, RegistryError
from trw_mcp.wiring.report import render_report, validate_findings


def _finding(edge: EdgeClass, contract_id: str = "c") -> Finding:
    return Finding(
        contract_id=contract_id,
        edge_class=edge,
        producer_side="p",
        consumer_side="c",
        evidence="observed",
        remedy="do the thing",
    )


def test_all_seven_edge_classes() -> None:
    """All seven classes exist, are distinct, and round-trip through a finding."""
    assert len(EdgeClass) == 7
    for edge in EdgeClass:
        finding = _finding(edge)
        assert finding.key.startswith(edge.value)
        assert edge.value in finding.render()


def test_finding_without_evidence_rejected() -> None:
    with pytest.raises(RegistryError, match="evidence"):
        Finding(
            contract_id="c",
            edge_class=EdgeClass.NEVER_FIRED,
            producer_side="p",
            consumer_side="c",
            evidence="   ",
            remedy="r",
        )


def test_finding_without_remedy_rejected() -> None:
    with pytest.raises(RegistryError, match="remedy"):
        Finding(
            contract_id="c",
            edge_class=EdgeClass.NEVER_FIRED,
            producer_side="p",
            consumer_side="c",
            evidence="e",
            remedy="",
        )


def test_reporter_rejects_an_evidence_free_finding() -> None:
    """The reporter is the last gate before output (NFR04)."""
    finding = _finding(EdgeClass.NEVER_FIRED)
    object.__setattr__(finding, "evidence", "  ")
    with pytest.raises(RegistryError, match="NFR04"):
        validate_findings((finding,))


def test_every_live_finding_names_contract_both_sides_and_evidence(live_result: DetectorResult) -> None:
    for finding in live_result.findings:
        rendered = finding.render()
        assert finding.contract_id in rendered
        assert "producer:" in rendered and "consumer:" in rendered
        assert "evidence:" in rendered and "remedy:" in rendered


def test_baseline_is_exact_match_in_both_directions() -> None:
    """A new finding blocks; a baseline entry that stopped firing ALSO blocks."""
    acknowledged = _finding(EdgeClass.NEVER_FIRED, "known")
    baseline = (BaselineEntry(key=acknowledged.key, ledger_id="UF-010", rationale="tracked in the ledger"),)

    clean = partition([acknowledged], baseline)
    assert clean.is_clean and clean.acknowledged and not clean.blocking

    new_finding = _finding(EdgeClass.NEVER_FIRED, "unknown")
    blocked = partition([acknowledged, new_finding], baseline)
    assert blocked.blocking == (new_finding,)

    stale = partition([], baseline)
    assert stale.stale_keys == (acknowledged.key,)
    assert not stale.is_clean, "a baseline entry that no longer fires must fail, or the list never shrinks"


def test_baseline_entry_requires_a_tracked_disposition() -> None:
    """No ledger ID, no acknowledgement — this is what stops it becoming a waiver list."""
    with pytest.raises(RegistryError, match="DEFECT-LEDGER"):
        BaselineEntry(key="X::y", ledger_id="because-i-said-so", rationale="a long enough rationale")
    with pytest.raises(RegistryError, match="rationale"):
        BaselineEntry(key="X::y", ledger_id="UF-010", rationale="tbd")


def test_no_update_baseline_flag_exists() -> None:
    """A regenerate command turns a ratchet into a rubber stamp."""
    from trw_mcp.wiring import cli

    parser_actions = {action.dest for action in cli._build_parser()._actions}
    assert "update_baseline" not in parser_actions
    assert not any("baseline" in dest for dest in parser_actions)


def test_report_never_claims_clean_while_findings_exist(live_result: DetectorResult) -> None:
    outcome = partition(list(live_result.findings), BASELINE)
    report = render_report(live_result, outcome)
    if outcome.acknowledged:
        assert "no NEW findings" in report
        assert "every registered contract produces its declared artifact" not in report
