"""Tests for review helper validation and severity counting."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.tools._review_helpers import (
    _compute_verdict,
    _normalize_severity,
    count_by_severity,
    validate_manual_findings,
)
from trw_mcp.tools._review_validation import _VALID_REVIEW_SEVERITIES, SEVERITY_ALIASES


class TestValidateManualFindings:
    """validate_manual_findings: normalization, pass-through, edge cases."""

    def test_valid_critical_finding_passes_through(self) -> None:
        findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]
        result = validate_manual_findings(findings)
        assert len(result) == 1
        assert result[0]["severity"] == "critical"

    def test_valid_warning_finding_passes_through(self) -> None:
        findings = [{"category": "style", "severity": "warning", "description": "Nit"}]
        result = validate_manual_findings(findings)
        assert result[0]["severity"] == "warning"

    def test_valid_info_finding_passes_through(self) -> None:
        findings = [{"category": "docs", "severity": "info", "description": "Comment missing"}]
        result = validate_manual_findings(findings)
        assert result[0]["severity"] == "info"

    def test_empty_list_returns_empty(self) -> None:
        result = validate_manual_findings([])
        assert result == []

    def test_invalid_severity_normalized_via_normalize_severity(self) -> None:
        """A complete finding using the supported high alias is normalized."""
        finding = {"category": "security", "severity": "high", "description": "Bug"}
        result = validate_manual_findings([finding])
        assert len(result) == 1
        assert result[0]["severity"] == "critical"

    def test_invalid_severity_medium_normalized_to_warning(self) -> None:
        finding = {"category": "style", "severity": "medium", "description": "Issue"}
        result = validate_manual_findings([finding])
        assert result[0]["severity"] == "warning"

    def test_invalid_severity_error_normalized_to_critical(self) -> None:
        finding = {"category": "correctness", "severity": "error", "description": "Failure"}
        result = validate_manual_findings([finding])
        assert result[0]["severity"] == "critical"

    def test_incomplete_finding_is_rejected(self) -> None:
        finding: dict[str, str] = {"severity": "high"}
        result = validate_manual_findings([finding])
        assert result == []

    def test_finding_with_unknown_severity_is_rejected(self) -> None:
        finding = {
            "category": "style",
            "severity": "unknown-level",
            "description": "Something",
        }
        result = validate_manual_findings([finding])
        assert result == []

    def test_multiple_findings_all_validated(self) -> None:
        findings = [
            {"category": "correctness", "severity": "critical", "description": "A"},
            {"category": "style", "severity": "warning", "description": "B"},
            {"category": "docs", "severity": "info", "description": "C"},
        ]
        result = validate_manual_findings(findings)
        assert len(result) == 3

    def test_result_contains_all_original_keys(self) -> None:
        finding = {
            "category": "security",
            "severity": "critical",
            "description": "SQL injection",
            "file_path": "app/db.py",
        }
        result = validate_manual_findings([finding])
        assert result[0]["file_path"] == "app/db.py"

    def test_findings_missing_severity_key_are_rejected(self) -> None:
        finding: dict[str, str] = {}
        result = validate_manual_findings([finding])
        assert result == []

    def test_blank_category_or_description_is_rejected(self) -> None:
        findings = [
            {"category": " ", "severity": "info", "description": "Issue"},
            {"category": "style", "severity": "info", "description": "  "},
        ]
        assert validate_manual_findings(findings) == []

    def test_mixed_batch_keeps_valid_findings_and_drops_only_the_invalid_ones(self) -> None:
        """Partial acceptance, in order — every other rejection test is all-invalid.

        Replaces a ``isinstance(result, list)`` check on an empty input, which
        proved nothing a ``return []`` stub would not also satisfy (the empty-list
        boundary is already pinned by ``test_empty_list_returns_empty``). The
        accept-list incident was precisely a batch losing its good findings, so
        the survivors and their order are the contract worth asserting.
        """
        findings = [
            {"category": "correctness", "severity": "critical", "description": "keep me"},
            {"category": "style", "severity": "not-a-severity", "description": "drop me"},
            {"category": "docs", "severity": "info", "description": "keep me too"},
            {"category": "  ", "severity": "warning", "description": "drop me too"},
        ]

        result = validate_manual_findings(findings)

        assert [f["description"] for f in result] == ["keep me", "keep me too"]


class TestAuditSeverityVocabularyIsAccepted:
    """Findings written in TRW's own P0/P1/P2 vocabulary must survive validation.

    Regression: the accept-list and the normalizer were two hand-maintained
    lists, and neither held the P-levels that ``audit-framework.md`` and every
    ``trw-auditor`` report emit. A complete audit handoff normalized to zero
    findings and recorded ``substantive: false`` — silently, because rejection
    only logs. Observed live 2026-07-26: an 8-finding payload recorded
    ``surfaced_findings_count: 0``; the same payload with ``high``/``medium``
    recorded 9.
    """

    @pytest.mark.parametrize(
        ("label", "expected_level"),
        [("P0", "critical"), ("P1", "critical"), ("P2", "warning"), ("P3", "info")],
    )
    def test_audit_severity_is_accepted_and_normalizes(self, label: str, expected_level: str) -> None:
        finding = {"category": "spec_gap", "severity": label, "description": "FR03 has no test"}
        result = validate_manual_findings([finding])
        assert len(result) == 1, f"{label} was dropped by validation"
        assert result[0]["severity"] == expected_level

    def test_p1_finding_blocks_the_verdict(self) -> None:
        """A P1 is verdict-blocking in the audit protocol, so it must be here too.

        This is the behavioral consequence of the P1 -> critical mapping, not a
        restatement of it: audit-framework.md Section E makes PASS require zero
        P0 AND zero P1, and ``high`` already normalized to critical before this
        change, so P1 aligns with both.
        """
        validated = validate_manual_findings(
            [{"category": "correctness", "severity": "P1", "description": "Significant gap"}]
        )
        assert _compute_verdict(list(validated)) == "block"

    def test_lowercase_and_padded_labels_resolve(self) -> None:
        assert _normalize_severity(" p0 ") == "critical"
        assert _normalize_severity("P2") == "warning"

    def test_accept_set_is_derived_from_the_alias_table(self) -> None:
        """The two questions ('accepted?' and 'means what?') share one source.

        Fails if anyone reintroduces a hand-maintained accept-list — the exact
        shape that let the P-levels be acceptable nowhere and meaningful nowhere.
        """
        assert _VALID_REVIEW_SEVERITIES == frozenset(SEVERITY_ALIASES)

    def test_every_alias_resolves_to_a_real_internal_level(self) -> None:
        assert set(SEVERITY_ALIASES.values()) <= {"critical", "warning", "info"}

    def test_unknown_label_is_still_rejected(self) -> None:
        """Widening the vocabulary must not turn the gate into a rubber stamp."""
        finding = {"category": "style", "severity": "P9", "description": "Nope"}
        assert validate_manual_findings([finding]) == []


class TestAuditProtocolSeverityParity:
    """Every severity the shipped audit protocol emits is one review accepts.

    Keyed on the document rather than a copied list, so the two surfaces cannot
    drift apart again the way they had.
    """

    AUDIT_FRAMEWORK = Path(__file__).resolve().parents[1] / "src/trw_mcp/data/skills/trw-audit/audit-framework.md"

    def test_protocol_severity_labels_are_all_accepted(self) -> None:
        if not self.AUDIT_FRAMEWORK.is_file():  # pragma: no cover - standalone mirror
            pytest.skip("audit-framework.md not present in this checkout")
        text = self.AUDIT_FRAMEWORK.read_text(encoding="utf-8")
        emitted = {match.lower() for match in re.findall(r"\bP[0-3]\b", text)}
        assert emitted, "no P-level severities found — has the protocol changed shape?"
        unaccepted = sorted(emitted - _VALID_REVIEW_SEVERITIES)
        assert not unaccepted, (
            f"audit-framework.md emits {unaccepted} but trw_review rejects them; "
            "findings at those severities would be silently discarded"
        )


class TestCountBySeverity:
    """count_by_severity: returns (critical, warning, info) tuple."""

    def test_empty_list_returns_zeros(self) -> None:
        assert count_by_severity([]) == (0, 0, 0)

    def test_single_critical(self) -> None:
        findings = [{"severity": "critical"}]
        assert count_by_severity(findings) == (1, 0, 0)

    def test_single_warning(self) -> None:
        findings = [{"severity": "warning"}]
        assert count_by_severity(findings) == (0, 1, 0)

    def test_single_info(self) -> None:
        findings = [{"severity": "info"}]
        assert count_by_severity(findings) == (0, 0, 1)

    def test_mixed_severities_counted_correctly(self) -> None:
        findings = [
            {"severity": "critical"},
            {"severity": "warning"},
            {"severity": "info"},
            {"severity": "critical"},
            {"severity": "info"},
        ]
        assert count_by_severity(findings) == (2, 1, 2)

    def test_unknown_severity_not_counted_in_any_bucket(self) -> None:
        findings = [{"severity": "unknown"}, {"severity": "high"}]
        critical, warning, info = count_by_severity(findings)
        assert critical == 0
        assert warning == 0
        assert info == 0

    def test_missing_severity_key_not_counted(self) -> None:
        findings = [{"category": "style"}]
        assert count_by_severity(findings) == (0, 0, 0)

    def test_tuple_positions_are_critical_warning_info(self) -> None:
        """All three counts differ, so a permuted return order fails.

        Replaces an ``isinstance(result, tuple)`` check on an empty input. The
        existing mixed case counts (2, 1, 2) — critical and info are equal there,
        so swapping those two positions would still pass it. Callers unpack this
        tuple positionally (``critical, warning, info = ...``), so the order is
        the contract.
        """
        findings = [{"severity": "critical"}] * 3 + [{"severity": "warning"}] + [{"severity": "info"}] * 2

        assert count_by_severity(findings) == (3, 1, 2)

    def test_all_critical_findings(self) -> None:
        findings = [{"severity": "critical"} for _ in range(5)]
        assert count_by_severity(findings) == (5, 0, 0)

    def test_all_info_findings(self) -> None:
        findings = [{"severity": "info"} for _ in range(3)]
        assert count_by_severity(findings) == (0, 0, 3)
