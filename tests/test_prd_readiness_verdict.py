"""PRD-FIX-141-FR09 — one readiness verdict, derived from the rules.

``trw_prd_validate`` answered "is this ready" twice and differently for the same
PRD in the same payload: ``total_score: 91.87``, ``quality_tier: approved``,
``grade: A`` — and ``valid: false`` with three error-severity failures
(learning L-9GXR). Two of those errors demanded frontmatter keys the PRD
template resource never documented, and one of them fired on mappings that
already declared ``method: test`` with a named pytest file.

A score band is not a readiness decision. ``verdict`` is the decision; the score
stays a secondary signal that now says so out loud when the two disagree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.requirements import (
    PRDQualityTier,
    ValidationFailure,
    ValidationResultV2,
    VerificationMapping,
    VerificationMethod,
)
from trw_mcp.state.validation._prd_validation_findings import finalize_verdict
from trw_mcp.state.validation._verification_mappings import (
    _is_automated_behavioral_evidence,
    validate_verification_mappings,
)


def _mapping(method: str, artifact: str, **kwargs: object) -> VerificationMapping:
    return VerificationMapping(
        requirement_id="PRD-X-001-FR01",
        acceptance_criteria=["Given a, When b, Then c"],
        method=VerificationMethod(method),
        evidence_artifact=artifact,
        pass_condition="the asserted value equals the target",
        **kwargs,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def test_a_valid_complete_result_is_ready() -> None:
    result = ValidationResultV2(valid=True, total_score=91.87, quality_tier=PRDQualityTier.APPROVED, grade="A")

    finalize_verdict(result)

    assert result.verdict == "READY"
    assert result.verdict_note == ""


def test_an_approved_grade_a_result_that_failed_a_rule_is_needs_work() -> None:
    """The exact 2026-09-16 payload: A / approved / 91.87 / valid=false."""
    result = ValidationResultV2(
        valid=False,
        total_score=91.87,
        quality_tier=PRDQualityTier.APPROVED,
        grade="A",
        failures=[
            ValidationFailure(
                field="verification:PRD-X-001-FR01",
                rule="implemented_requirement_automation",
                message="no automated behavioral evidence",
                severity="error",
            ),
            ValidationFailure(
                field="functionality_level",
                rule="aaref_functionality_level_required",
                message="missing functionality_level",
                severity="error",
            ),
        ],
    )

    finalize_verdict(result)

    assert result.verdict == "NEEDS_WORK"
    assert "implemented_requirement_automation" in result.verdict_note
    assert "aaref_functionality_level_required" in result.verdict_note


def test_the_note_names_the_score_band_when_it_disagrees() -> None:
    """The pairing most likely to be misread must say which answer governs."""
    result = ValidationResultV2(
        valid=False,
        total_score=91.87,
        quality_tier=PRDQualityTier.APPROVED,
        grade="A",
        failures=[
            ValidationFailure(field="f", rule="some_rule", message="m", severity="error"),
        ],
    )

    finalize_verdict(result)

    assert "quality_tier=approved" in result.verdict_note
    assert "grade=A" in result.verdict_note
    assert "not readiness" in result.verdict_note


def test_a_low_scoring_rejection_gets_no_score_band_caveat() -> None:
    """The caveat exists for the contradiction, not as boilerplate."""
    result = ValidationResultV2(
        valid=False,
        total_score=40.0,
        quality_tier=PRDQualityTier.SKELETON,
        grade="F",
        failures=[ValidationFailure(field="f", rule="some_rule", message="m", severity="error")],
    )

    finalize_verdict(result)

    assert result.verdict == "NEEDS_WORK"
    assert "not readiness" not in result.verdict_note
    assert "some_rule" in result.verdict_note


def test_a_partial_validation_is_never_ready() -> None:
    """``valid`` after a partial run means "nothing we RAN objected".

    Fast mode and budget exhaustion skip the grounding checks entirely, so
    promoting that to READY would report an unperformed check as a passed one.
    """
    result = ValidationResultV2(
        valid=True,
        total_score=95.0,
        quality_tier=PRDQualityTier.APPROVED,
        grade="A",
        integrity_warnings=[
            "validation_partial: fast mode requested — the dynamic validation checks were SKIPPED (repo_paths)."
        ],
    )

    finalize_verdict(result)

    assert result.verdict == "NEEDS_WORK"
    assert "PARTIAL" in result.verdict_note


def test_a_rejection_with_no_error_finding_still_names_something() -> None:
    """The invariant backstop runs FIRST, so the verdict can always cite a rule."""
    result = ValidationResultV2(
        valid=False,
        failures=[ValidationFailure(field="f", rule="advisory_rule", message="m", severity="warning")],
    )

    finalize_verdict(result)

    assert result.verdict == "NEEDS_WORK"
    assert "valid_without_error_finding" in result.verdict_note


# ---------------------------------------------------------------------------
# method: test counts as automated behavioral evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "artifact", "expected"),
    [
        ("test", "trw-mcp/tests/test_foo.py::test_bar", True),
        ("test", "trw-mcp/tests/test_foo.py", True),
        ("test", "tests/test_a.py", True),
        ("test", "docs/framework-probe-audit-2026-09-16/FINDINGS.md", False),
        ("test", "a notebook of manual results", False),
        ("analysis", "trw-mcp/tests/test_foo.py", False),
        ("inspection", "trw-mcp/tests/test_foo.py", False),
    ],
)
def test_automated_behavioral_evidence_recognition(method: str, artifact: str, expected: bool) -> None:
    """Only ``method: test`` pointing at a TEST-shaped artifact qualifies."""
    assert _is_automated_behavioral_evidence(_mapping(method, artifact)) is expected


def test_the_rule_is_pure_and_does_not_stat_the_filesystem(tmp_path: Path) -> None:
    """A cached pure result must not depend on mutable external state.

    ``tools/_prd_validation_cache.py`` keys on PRD text, config and version, so a
    filesystem-dependent rule would hand back a stale acceptance after the named
    file was deleted. File EXISTENCE is the ``repo_path_exists`` integrity
    check's job, in the dynamic phase that has a repo root. Same verdict for a
    path that exists and one that does not.
    """
    real = tmp_path / "test_real.py"
    real.write_text("def test_x(): pass\n", encoding="utf-8")

    assert _is_automated_behavioral_evidence(_mapping("test", str(real))) is True
    assert _is_automated_behavioral_evidence(_mapping("test", str(tmp_path / "test_absent.py"))) is True


def test_implemented_prd_with_named_pytest_files_raises_no_automation_failure() -> None:
    """The live rule, through its real entry point, on an implemented PRD."""
    content = "### PRD-X-001-FR01: A requirement\n\nBody.\n"
    frontmatter: dict[str, object] = {
        "template_version": "3.2",
        "status": "implemented",
        "verification": {
            "mappings": [
                {
                    "requirement_id": "PRD-X-001-FR01",
                    "acceptance_criteria": ["Given a, When b, Then c"],
                    "method": "test",
                    "evidence_artifact": "trw-mcp/tests/test_prd_readiness_verdict.py",
                    "pass_condition": "assertions pass",
                }
            ]
        },
    }

    failures, coverage = validate_verification_mappings(frontmatter, content, effective_risk_level="high")

    assert [f.rule for f in failures] == []
    assert coverage == 1.0


def test_an_implemented_prd_with_a_non_test_artifact_still_fails() -> None:
    """Non-vacuity: the rule was narrowed, not disabled."""
    content = "### PRD-X-001-FR01: A requirement\n\nBody.\n"
    frontmatter: dict[str, object] = {
        "template_version": "3.2",
        "status": "implemented",
        "verification": {
            "mappings": [
                {
                    "requirement_id": "PRD-X-001-FR01",
                    "acceptance_criteria": ["Given a, When b, Then c"],
                    "method": "inspection",
                    "evidence_artifact": "docs/CONSTITUTION.md",
                    "pass_condition": "a reviewer agrees",
                }
            ]
        },
    }

    failures, _ = validate_verification_mappings(frontmatter, content, effective_risk_level="high")

    assert "implemented_requirement_automation" in {f.rule for f in failures}


def test_an_explicit_automation_optout_still_needs_its_reason() -> None:
    """``automated: false`` is untouched by FR09."""
    content = "### PRD-X-001-FR01: A requirement\n\nBody.\n"
    frontmatter: dict[str, object] = {
        "template_version": "3.2",
        "status": "implemented",
        "verification": {
            "mappings": [
                {
                    "requirement_id": "PRD-X-001-FR01",
                    "acceptance_criteria": ["Given a, When b, Then c"],
                    "method": "test",
                    "evidence_artifact": "trw-mcp/tests/test_prd_readiness_verdict.py",
                    "pass_condition": "assertions pass",
                    "automated": False,
                }
            ]
        },
    }

    failures, _ = validate_verification_mappings(frontmatter, content, effective_risk_level="high")

    assert "automation_exception_reason" in {f.rule for f in failures}


# ---------------------------------------------------------------------------
# The template documents the keys the rules require
# ---------------------------------------------------------------------------


def test_the_prd_template_documents_the_post_implementation_keys() -> None:
    """A rule that demands a key the template never mentions is unmeetable."""
    from trw_mcp.resources import templates

    template = Path(templates.__file__).parent.parent / "data" / "prd_template.md"
    body = template.read_text(encoding="utf-8")

    assert "Required once status is implemented" in body
    assert "aaref_functionality_level_required" in body
    assert "implemented_requirement_automation" in body
    assert "automation_infeasible_reason" in body


def test_the_template_resource_still_serves_that_body() -> None:
    """The documentation must reach the surface an author actually reads."""
    from fastmcp import FastMCP

    from tests.conftest import get_resources_sync
    from trw_mcp.resources.templates import register_template_resources

    server = FastMCP("test")
    register_template_resources(server)
    body = get_resources_sync(server)["trw://templates/prd"].fn()

    assert "Required once status is implemented" in body
