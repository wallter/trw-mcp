"""Tests for PRD-FIX-056: PRD Status Integrity & Lifecycle State Machine.

Covers:
  FR01 — Status drift detection in validate_prd_quality_v2
  FR02 — FR-level Status annotation injected in generated PRD body
  FR03 — State machine in prd_status.py (validate_status_transition)
  FR04 — null approved_by warning in check_transition_guards
  FR05 — partially_implemented_frs warning in validate_prd_quality_v2
"""

from __future__ import annotations

import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal PRD fixtures
# ---------------------------------------------------------------------------

_FRONTMATTER_TMPL = """\
---
prd:
  id: PRD-TEST-001
  title: Test PRD
  version: '1.0'
  status: {status}
  priority: P1
  category: TEST
  approved_by: {approved_by}
  partially_implemented_frs: {partial_frs}
  evidence:
    level: moderate
    sources: []
  confidence:
    implementation_feasibility: 0.8
    requirement_clarity: 0.8
    estimate_confidence: 0.7
    test_coverage_target: 0.85
  traceability:
    implements: [PRD-CORE-001]
    depends_on: []
    enables: []
    conflicts_with: []
  metrics:
    success_criteria: []
    measurement_method: []
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
    consistency_validation_min: 0.95
  dates:
    created: '2026-03-13'
    updated: '2026-03-13'
---
"""

_BODY_TMPL = """\
# PRD-TEST-001: Test PRD

**Quick Reference**:
- **Status**: {prose_status}
- **Priority**: P1
- **Evidence**: Moderate
- **Implementation Confidence**: 0.8

---

## 1. Problem Statement

### Background
Test background.

### Problem
Test problem statement here with enough text to score well.

### Impact
Test impact analysis here.

## 2. Goals & Non-Goals

### Goals
- [x] G1: Test goal one here
- [x] G2: Test goal two here

### Non-Goals
- Not doing X.
- Not doing Y.

## 3. User Stories

### US-001: Basic
**As a** user **I want** something **So that** value.

**Acceptance Criteria**:
- [x] Given state, When action, Then outcome.

## 4. Functional Requirements

{fr_content}

## 5. Non-Functional Requirements

### PRD-TEST-001-NFR01: Performance
- Response time < 200ms p99.

### PRD-TEST-001-NFR02: Reliability
- 99.9% availability.

### PRD-TEST-001-NFR03: Security
- Auth required.

## 6. Technical Approach

### Architecture Impact
None.

### Key Files
| File | Changes |
|------|---------|
| `test.py` | Test changes |

## 7. Test Strategy

### Unit Tests
- `test_fix056_status_integrity.py::test_status_drift_detection`

## 8. Rollout Plan

### Phase 1
- Deploy.

## 9. Success Metrics

| Metric | Baseline | Target |
|--------|----------|--------|
| Zero drift | 30% | 0% |

## 10. Dependencies & Risks

### Dependencies
| ID | Description | Status | Blocking |
|----|-------------|--------|----------|
| DEP-001 | None | Resolved | No |

### Risks
| ID | Risk | Probability | Impact | Mitigation | Residual Risk |
|----|------|-------------|--------|------------|---------------|
| RISK-001 | Low | Low | Low | None needed | Low |

## 11. Open Questions

- [x] OQ-001: None. `[blocking: no]`

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | Audit | `prd_quality.py` | `test_fix056_status_integrity.py` | Pending |

"""

_FR_WITH_STATUS = """\
### PRD-TEST-001-FR01: First Requirement
**Priority**: Must Have
**Status**: active
**Description**: A well-defined requirement.
**Acceptance**: Given X, When Y, Then Z.
**Dependencies**: None
**Confidence**: 0.9
"""

_FR_WITHOUT_STATUS = """\
### PRD-TEST-001-FR01: First Requirement
**Priority**: Must Have
**Description**: A well-defined requirement.
**Acceptance**: Given X, When Y, Then Z.
**Dependencies**: None
**Confidence**: 0.9
"""


def _make_prd(
    *,
    fm_status: str = "draft",
    prose_status: str = "Draft",
    approved_by: str = "null",
    partial_frs: str = "[]",
    fr_content: str = _FR_WITH_STATUS,
) -> str:
    fm = _FRONTMATTER_TMPL.format(
        status=fm_status,
        approved_by=approved_by,
        partial_frs=partial_frs,
    )
    body = _BODY_TMPL.format(prose_status=prose_status, fr_content=fr_content)
    return fm + body


# ===========================================================================
# FR01 — Status Drift Detection
# ===========================================================================


class TestStatusDriftDetection:
    """Tests for _check_status_drift helper and its integration with validate_prd_quality_v2."""

    def test_no_drift_matching_status(self) -> None:
        """When frontmatter and prose status agree (case-insensitive), no warnings."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = _make_prd(fm_status="draft", prose_status="Draft")
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert warnings == [], f"Expected no drift warnings, got: {warnings}"

    def test_no_drift_case_insensitive(self) -> None:
        """Case-insensitive comparison: 'done' matches 'Done'."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = _make_prd(fm_status="done", prose_status="Done")
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert warnings == []

    def test_drift_detected_mismatch(self) -> None:
        """When frontmatter status differs from prose, a warning is returned."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = _make_prd(fm_status="done", prose_status="Draft")
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert len(warnings) == 1
        assert "Status drift" in warnings[0]
        assert "done" in warnings[0]
        assert "draft" in warnings[0]

    def test_no_drift_no_quick_reference_block(self) -> None:
        """When no prose Quick Reference block exists, drift check skips gracefully."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        # A PRD body without any **Status**: line in the prose
        content = textwrap.dedent("""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: done
              priority: P1
              category: TEST
            ---

            # PRD-TEST-001: Test

            ## 4. Functional Requirements

            No status line anywhere in the body prose.
        """)
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert warnings == [], "Should skip gracefully when no prose status line found"

    def test_validate_v2_includes_drift_warnings(self) -> None:
        """validate_prd_quality_v2 populates status_drift_warnings when drift found."""
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        content = _make_prd(fm_status="done", prose_status="Draft")
        result = validate_prd_quality_v2(content)
        assert isinstance(result.status_drift_warnings, list)
        drift_msgs = [w for w in result.status_drift_warnings if "Status drift" in w]
        assert len(drift_msgs) >= 1, "Expected at least one drift warning from v2 validation"

    def test_validate_v2_no_drift_warnings_when_consistent(self) -> None:
        """validate_prd_quality_v2 produces no status drift warnings when status consistent."""
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        content = _make_prd(fm_status="draft", prose_status="Draft")
        result = validate_prd_quality_v2(content)
        drift_msgs = [w for w in result.status_drift_warnings if "Status drift" in w]
        assert drift_msgs == [], f"Expected no drift warnings, got: {drift_msgs}"


# ===========================================================================
# FR02 — FR Status Annotation in Generated Template
# ===========================================================================


class TestFRStatusAnnotation:
    """Tests for FR-level **Status**: active injection in _substitute_template."""

    def test_generated_body_contains_fr_status(self) -> None:
        """Generated PRD body includes **Status**: active in each FR block."""
        from trw_mcp.tools.requirements import _generate_prd_body

        body = _generate_prd_body(
            "PRD-TEST-001",
            "Test PRD",
            "Input text for test",
            "TEST",
            priority="P1",
            confidence=0.7,
        )
        # The FR blocks should have **Status**: active injected after **Priority**:
        assert "**Status**: active" in body, "Expected '**Status**: active' to appear in generated FR body"

    def test_status_annotation_follows_priority(self) -> None:
        """**Status**: active appears immediately after **Priority**: in FR blocks."""
        from trw_mcp.tools.requirements import _generate_prd_body

        body = _generate_prd_body(
            "PRD-TEST-002",
            "Another Test",
            "More input text",
            "CORE",
            priority="P2",
            confidence=0.6,
        )
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if "**Priority**: Must Have" in line or "**Priority**: Should Have" in line:
                # Check the very next non-empty line has **Status**: active
                next_lines = [l for l in lines[i + 1 : i + 3] if l.strip()]
                if next_lines:
                    assert "**Status**: active" in next_lines[0], (
                        f"Expected **Status**: active after priority line at line {i}, got: {next_lines[0]!r}"
                    )
                break

    def test_fr_annotation_warning_in_validation(self) -> None:
        """Validation warns when an FR section lacks a **Status**: annotation."""
        from trw_mcp.state.validation.prd_quality import _check_fr_annotations

        content = _make_prd(fr_content=_FR_WITHOUT_STATUS)
        warnings = _check_fr_annotations(content)
        assert len(warnings) >= 1, "Expected at least one FR annotation warning"
        assert "FR annotation missing" in warnings[0]

    def test_fr_annotation_no_warning_when_present(self) -> None:
        """No FR annotation warnings when all FR sections have **Status**: lines."""
        from trw_mcp.state.validation.prd_quality import _check_fr_annotations

        content = _make_prd(fr_content=_FR_WITH_STATUS)
        warnings = _check_fr_annotations(content)
        assert warnings == [], f"Expected no FR annotation warnings, got: {warnings}"

    def test_no_fr_section_no_annotation_warning(self) -> None:
        """When there's no Functional Requirements section, no annotation warnings."""
        from trw_mcp.state.validation.prd_quality import _check_fr_annotations

        content = "# PRD\n\nSome body without FR sections.\n"
        warnings = _check_fr_annotations(content)
        assert warnings == []


# ===========================================================================
# FR03 — State Machine Transitions
# ===========================================================================


class TestStatusStateMachine:
    """Tests for validate_status_transition() in prd_status.py."""

    def test_allowed_transitions(self) -> None:
        """Valid transitions return True — derived from canonical VALID_TRANSITIONS in prd_utils.py."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        # Based on canonical VALID_TRANSITIONS in prd_utils.py:
        # DRAFT -> {REVIEW, MERGED}
        # REVIEW -> {APPROVED, DRAFT, MERGED}
        # APPROVED -> {IMPLEMENTED, DEPRECATED, MERGED}
        # IMPLEMENTED -> {DONE, DEPRECATED}
        valid_pairs = [
            ("draft", "review"),
            ("draft", "merged"),
            ("review", "draft"),
            ("review", "approved"),
            ("review", "merged"),
            ("approved", "implemented"),
            ("approved", "deprecated"),
            ("approved", "merged"),
            ("implemented", "done"),
            ("implemented", "deprecated"),
        ]
        for current, target in valid_pairs:
            assert validate_status_transition(current, target), f"Expected {current} -> {target} to be allowed"

    def test_invalid_transitions(self) -> None:
        """Invalid transitions return False — derived from canonical state machine."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        invalid_pairs = [
            ("done", "draft"),
            ("done", "review"),
            ("deprecated", "draft"),
            ("deprecated", "done"),
            ("draft", "done"),
            ("draft", "approved"),
            ("merged", "draft"),
            ("approved", "draft"),  # approved cannot go back to draft
        ]
        for current, target in invalid_pairs:
            assert not validate_status_transition(current, target), f"Expected {current} -> {target} to be blocked"

    def test_identity_transitions_always_valid(self) -> None:
        """Same-to-same transitions are always valid."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        for status in ["draft", "review", "approved", "implemented", "done", "deprecated", "merged"]:
            assert validate_status_transition(status, status), (
                f"Identity transition {status} -> {status} should be valid"
            )

    def test_case_insensitive_input(self) -> None:
        """Transition lookup is case-insensitive."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        assert validate_status_transition("Draft", "Review")
        assert validate_status_transition("APPROVED", "IMPLEMENTED")
        assert not validate_status_transition("Done", "Draft")

    def test_unknown_current_status_returns_false(self) -> None:
        """Unknown current status returns False for any non-identity target."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        assert not validate_status_transition("unknown_status", "review")
        assert not validate_status_transition("foobar", "done")


# ===========================================================================
# FR04 (FR06 in PRD numbering) — null approved_by warning in transition guard
# ===========================================================================


class TestApprovalWarningGuard:
    """Tests for the approved_by null warning in check_transition_guards."""

    def _make_simple_prd(self, *, approved_by: str | None = None) -> str:
        """Build a minimal PRD string for guard testing."""
        approved_by_line = f"  approved_by: {approved_by}" if approved_by else "  approved_by: null"
        return textwrap.dedent(f"""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: implemented
              priority: P1
              category: TEST
            {approved_by_line}
            ---

            # PRD-TEST-001: Test
        """)

    def test_null_approved_by_warning_on_terminal_transition(self) -> None:
        """Transition to DONE with null approved_by includes approval_warning."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by=None)
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DONE, content)
        assert result.allowed is True
        assert "approval_warning" in result.guard_details
        assert result.guard_details["approval_warning"] == "approved_by is null on terminal transition"

    def test_null_approved_by_warning_on_deprecated_transition(self) -> None:
        """Transition to DEPRECATED with null approved_by includes approval_warning."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by=None)
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DEPRECATED, content)
        assert result.allowed is True
        assert "approval_warning" in result.guard_details

    def test_no_warning_when_approved_by_set(self) -> None:
        """No approval_warning when approved_by is populated."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by="Tyler")
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DONE, content)
        assert result.allowed is True
        assert "approval_warning" not in result.guard_details, (
            f"Expected no approval_warning but guard_details={result.guard_details}"
        )

    def test_allowed_remains_true_with_warning(self) -> None:
        """The allowed field stays True even when approval_warning is present."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by=None)
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DONE, content)
        assert result.allowed is True, "Warning must not block the transition"

    def test_non_terminal_transition_no_approval_warning(self) -> None:
        """Transitions to non-terminal states do not produce approval_warning."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        # DRAFT -> REVIEW has its own content density guard, not approval warning
        minimal = textwrap.dedent(
            """\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: draft
              priority: P1
              category: TEST
              approved_by: null
            ---

            # Test

            Long enough content to meet density threshold. """
            + ("x " * 100)
            + """

            ## 1. Problem Statement
            Content here.
            ## 2. Goals & Non-Goals
            Content here.
            ## 3. User Stories
            Content here.
            ## 4. Functional Requirements
            Content here.
            ## 5. Non-Functional Requirements
            Content here.
            ## 6. Technical Approach
            Content here.
            ## 7. Test Strategy
            Content here.
            ## 8. Rollout Plan
            Content here.
            ## 9. Success Metrics
            Content here.
            ## 10. Dependencies & Risks
            Content here.
            ## 11. Open Questions
            Content here.
            ## 12. Traceability Matrix
            Content here.
        """
        )
        result = check_transition_guards(PRDStatus.DRAFT, PRDStatus.REVIEW, minimal)
        assert "approval_warning" not in result.guard_details


# ===========================================================================
# FR05 — partially_implemented_frs warning
# ===========================================================================


class TestPartiallyImplementedFRsWarning:
    """Tests for _check_partially_implemented in prd_quality.py."""

    def test_warning_when_done_with_partial_frs(self) -> None:
        """PRD marked 'done' with partially_implemented_frs produces a warning."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {"status": "done", "partially_implemented_frs": ["FR03"]}
        warnings = _check_partially_implemented(fm)
        assert len(warnings) == 1
        assert "FR03" in warnings[0]
        assert "partially implemented" in warnings[0].lower()

    def test_warning_names_all_partial_frs(self) -> None:
        """Warning message includes all deferred FR IDs."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {
            "status": "done",
            "partially_implemented_frs": ["FR03", "FR06"],
        }
        warnings = _check_partially_implemented(fm)
        assert "FR03" in warnings[0]
        assert "FR06" in warnings[0]

    def test_no_warning_when_not_done(self) -> None:
        """No warning when status is not 'done'."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        for status in ["draft", "review", "approved", "implemented", "deprecated"]:
            fm: dict[str, object] = {
                "status": status,
                "partially_implemented_frs": ["FR03"],
            }
            warnings = _check_partially_implemented(fm)
            assert warnings == [], f"Expected no warning for status={status!r}"

    def test_no_warning_when_partial_frs_empty(self) -> None:
        """No warning when partially_implemented_frs is an empty list."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {"status": "done", "partially_implemented_frs": []}
        warnings = _check_partially_implemented(fm)
        assert warnings == []

    def test_no_warning_when_partial_frs_missing(self) -> None:
        """No warning when partially_implemented_frs key is absent."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {"status": "done"}
        warnings = _check_partially_implemented(fm)
        assert warnings == []

    def test_validate_v2_includes_partial_frs_warning(self) -> None:
        """validate_prd_quality_v2 includes partial FR warnings in status_drift_warnings."""
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        content = _make_prd(
            fm_status="done",
            prose_status="Done",
            partial_frs="[FR03]",
        )
        result = validate_prd_quality_v2(content)
        partial_msgs = [w for w in result.status_drift_warnings if "partially implemented" in w.lower()]
        assert len(partial_msgs) >= 1, (
            f"Expected partial FR warning in status_drift_warnings, got: {result.status_drift_warnings}"
        )


# ===========================================================================
# Model field presence tests
# ===========================================================================


class TestModelFields:
    """Verify new fields are present on the models."""

    def test_validation_result_v2_has_status_drift_warnings(self) -> None:
        """ValidationResultV2 has status_drift_warnings field defaulting to []."""
        from trw_mcp.models.requirements import ValidationResultV2

        result = ValidationResultV2()
        assert hasattr(result, "status_drift_warnings")
        assert result.status_drift_warnings == []

    def test_prd_frontmatter_has_approved_by(self) -> None:
        """PRDFrontmatter has approved_by field defaulting to None."""
        from trw_mcp.models.requirements import PRDFrontmatter

        fm = PRDFrontmatter(id="PRD-TEST-001", title="Test")
        assert hasattr(fm, "approved_by")
        assert fm.approved_by is None

    def test_prd_frontmatter_has_partially_implemented_frs(self) -> None:
        """PRDFrontmatter has partially_implemented_frs field defaulting to []."""
        from trw_mcp.models.requirements import PRDFrontmatter

        fm = PRDFrontmatter(id="PRD-TEST-001", title="Test")
        assert hasattr(fm, "partially_implemented_frs")
        assert fm.partially_implemented_frs == []

    def test_prd_frontmatter_backward_compatible_without_new_fields(self) -> None:
        """Existing PRDs without approved_by or partially_implemented_frs still parse."""
        from trw_mcp.state.prd_utils import parse_frontmatter

        # A PRD without the new fields
        content = textwrap.dedent("""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: draft
              priority: P1
              category: TEST
            ---
            # Body
        """)
        fm = parse_frontmatter(content)
        assert fm.get("id") == "PRD-TEST-001"
        # No error; new fields are absent (defaults kick in at model level)


# ===========================================================================
# FR02 — update_frontmatter() prose auto-sync
# ===========================================================================


class TestUpdateFrontmatterProseSyncFR02:
    """Tests that update_frontmatter() syncs the prose Quick Reference status line."""

    def test_update_frontmatter_syncs_prose(self, tmp_path: Path) -> None:  # type: ignore[name-defined]
        """update_frontmatter with status update must sync prose **Status** line."""
        from trw_mcp.state.prd_utils import parse_frontmatter, update_frontmatter

        prd_file = tmp_path / "PRD-TEST-001.md"
        prd_file.write_text(
            textwrap.dedent("""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test PRD
              version: '1.0'
              status: draft
              priority: P1
              category: TEST
            ---

            # PRD-TEST-001: Test PRD

            **Quick Reference**:
            - **Status**: Draft
            - **Priority**: P1

            ## 1. Problem Statement

            Body here.
        """),
            encoding="utf-8",
        )

        update_frontmatter(prd_file, {"status": "review"})

        updated = prd_file.read_text(encoding="utf-8")

        # Frontmatter must say "review"
        fm = parse_frontmatter(updated)
        assert fm.get("status") == "review", f"Frontmatter status not updated: {fm.get('status')!r}"

        # Prose Quick Reference must also say "Review"
        assert "- **Status**: Review" in updated, f"Prose status not synced in body:\n{updated}"
        # The old value "Draft" must not remain on a **Status** prose line
        import re

        prose_status_lines = re.findall(r"- \*\*Status\*\*: (\w+)", updated)
        assert all(s.lower() == "review" for s in prose_status_lines), (
            f"Found prose status lines not updated: {prose_status_lines}"
        )
