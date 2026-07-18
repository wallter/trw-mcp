"""Behavioral contracts for slim, client-neutral packaged agents."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents"


def test_requirement_review_uses_canonical_category_aware_readiness() -> None:
    content = (AGENTS / "trw-requirement-reviewer.md").read_text(encoding="utf-8")
    for phrase in (
        "sections_expected",
        "validation_partial",
        "risk-scaled",
        "actionable remediation",
        "recall is not proof",
    ):
        assert phrase in content
    for forbidden in ("Structure: >= 90%", "NEVER suggest specific fixes", "All 12 AARE-F sections"):
        assert forbidden not in content


def test_traceability_uses_configured_gate_and_unknown_links() -> None:
    content = (AGENTS / "trw-traceability-checker.md").read_text(encoding="utf-8")
    for phrase in (
        "Configured gate: none",
        "Gate status: REPORT_ONLY",
        "project configuration or an explicit requirement",
        "UNKNOWN",
        "source and test evidence separately",
    ):
        assert phrase in content
    assert "Gate Threshold | 90%" not in content
    assert "PRD-level comment covers all FRs" not in content


def test_requirement_writer_selects_syntax_and_verification_to_fit() -> None:
    content = (AGENTS / "trw-requirement-writer.md").read_text(encoding="utf-8")
    for phrase in (
        "Use EARS",
        "when an event, state, feature",
        "Given/When/Then for externally observable",
        "Test**, **Analysis**, **Inspection**, or **Demonstration",
        "never invent a percentage",
        "candidate/open question",
    ):
        assert phrase in content
    assert "Every requirement you" not in content


def test_implementer_keeps_evidence_and_simplification_without_harness_folklore() -> None:
    content = (AGENTS / "trw-implementer.md").read_text(encoding="utf-8")
    for phrase in (
        "shared workspace",
        "before or alongside production code",
        "production path",
        "project-native",
        "records checks; it does not execute them",
        "its tests, and surrounding files as one",
        "remove only proven dead code",
        "required only when the caller/run contract supplies it",
    ):
        assert phrase in content
    for forbidden in (
        "2-3x",
        "10 minutes",
        "350-eLOC",
        "6,800 green tests",
        "TEAMMATE:",
        "QoL fixes",
        "Max 4 shards",
        "JSONL with ts",
    ):
        assert forbidden not in content
