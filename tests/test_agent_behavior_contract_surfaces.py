"""Behavioral contracts for slim, client-neutral packaged agents."""

from __future__ import annotations

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
AGENTS = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "agents"


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


def test_prd_groomer_and_requirement_reviewer_grant_prd_validate() -> None:
    """2026-09-04 wiring-defect fix companion: both agents that call
    trw_prd_validate in their body must also GRANT it in frontmatter `tools:`,
    or the grant/usage pair silently diverges (PRD P12 "presence, unconsumed"
    pattern applied in reverse — usage with no grant). Prior to the
    surface-authority fix, the grant alone was insufficient because
    SurfaceAuthorityMiddleware masked the tool for coding-task sessions
    regardless of the agent's own frontmatter; this test only proves the
    grant/usage pair stays wired, not the runtime surface (see
    test_surface_authority_middleware.py::test_coding_run_exposes_coding_packs
    for that)."""
    for name in ("trw-prd-groomer.md", "trw-requirement-reviewer.md"):
        content = (AGENTS / name).read_text(encoding="utf-8")
        assert "mcp__trw__trw_prd_validate" in content, f"{name} does not grant trw_prd_validate"
        assert "trw_prd_validate" in content.split("---", 2)[2], f"{name} grants but never calls trw_prd_validate"
