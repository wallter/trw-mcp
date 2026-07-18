"""Integration and wiring tests for requirements tools."""

from __future__ import annotations

from pathlib import Path

from tests._test_tools_requirements_support import _get_tools, set_project_root  # noqa: F401


class TestIntegration:
    """Integration tests for the full template-driven pipeline."""

    def test_full_structure_has_all_sections(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Implement template-driven PRD generation",
            category="CORE",
            priority="P1",
            title="Template PRDs",
        )
        content = result["content"]
        assert result["sections_generated"] == 12

        for i, section in enumerate(
            [
                "Problem Statement",
                "Goals & Non-Goals",
                "User Stories",
                "Functional Requirements",
                "Non-Functional Requirements",
                "Technical Approach",
                "Test Strategy",
                "Rollout Plan",
                "Success Metrics",
                "Dependencies & Risks",
                "Open Questions",
                "Traceability Matrix",
            ],
            1,
        ):
            assert f"## {i}. {section}" in content, f"Missing section {i}. {section}"

    def test_nfr03_security_present(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Security-relevant feature",
            category="CORE",
            title="Secure Feature",
        )
        assert "NFR03: Security" in result["content"]

    def test_appendix_present(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Feature with appendix",
            category="CORE",
            title="Appendix Test",
        )
        assert "## Appendix" in result["content"]

    def test_quality_checklist_present(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Feature with checklist",
            category="CORE",
            title="Checklist Test",
        )
        assert "Quality Checklist" in result["content"]

    def test_template_version_in_frontmatter(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Version check",
            category="CORE",
            title="Version Test",
        )
        assert "template_version" in result["content"]
        assert "3.2" in result["content"]

    def test_backward_compat_return_schema(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Schema check",
            category="CORE",
            title="Schema Test",
        )
        assert "prd_id" in result
        assert "title" in result
        assert "category" in result
        assert "priority" in result
        assert "output_path" in result
        assert "content" in result
        assert "sections_generated" in result
        assert result["sections_generated"] == 12

    def test_prefill_with_file_refs_and_deps(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Modify tools/requirements.py. Depends on PRD-FIX-006.",
            category="CORE",
            title="Prefill Test",
        )
        content = result["content"]
        assert "`tools/requirements.py`" in content
        assert "PRD-FIX-006" in content

    def test_slos_in_frontmatter(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="SLO: latency under 200ms for all API calls",
            category="CORE",
            title="SLO Test",
        )
        content = result["content"]
        assert "slos" in content


class TestCeremonyNudgeWiringRequirements:
    """Verify ceremony_status is injected into PRD tool responses."""

    def test_trw_prd_create_includes_ceremony_status(self, tmp_path: Path) -> None:
        """trw_prd_create response must contain 'ceremony_status' after nudge injection."""
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Test feature for nudge wiring verification",
            category="CORE",
            title="Nudge Wiring PRD",
        )
        assert "ceremony_status" in result, "trw_prd_create did not inject ceremony_status — nudge wiring is broken"
        assert isinstance(result["ceremony_status"], str)

    def test_trw_prd_validate_includes_ceremony_status(self, tmp_path: Path) -> None:
        """trw_prd_validate response must contain 'ceremony_status' after nudge injection."""
        prd_content = """---
prd:
  id: PRD-CORE-099
  title: "Nudge Wiring Test"
  version: "1.0"
  status: draft
  priority: P1

confidence:
  implementation_feasibility: 0.8
  requirement_clarity: 0.8
  estimate_confidence: 0.7

traceability:
  implements: [KE-FRAME-001]
  depends_on: []
---

# PRD-CORE-099: Nudge Wiring Test

## 1. Problem Statement
We need to verify nudge wiring.

## 2. Goals & Non-Goals
Goals and non-goals.

## 3. User Stories
User stories here.

## 4. Functional Requirements
Requirements.

## 5. Non-Functional Requirements
NFRs.

## 6. Technical Approach
Approach.

## 7. Test Strategy
Testing.

## 8. Rollout Plan
Rollout.

## 9. Success Metrics
Metrics.

## 10. Dependencies & Risks
Risks.

## 11. Open Questions
Questions.

## 12. Traceability Matrix
Matrix.
"""
        prd_path = tmp_path / "nudge-test.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert "ceremony_status" in result, "trw_prd_validate did not inject ceremony_status — nudge wiring is broken"
        assert isinstance(result["ceremony_status"], str)


class TestPrdValidateIntegrityOutput:
    """PRD validation tool returns integrity diagnostics in the serialized payload."""

    def test_trw_prd_validate_surfaces_integrity_findings(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        prd_path = prds_dir / "PRD-OPENCODE-001.md"
        prd_path.write_text(
            """---
prd:
  id: PRD-OPENCODE-001
  title: "Broken integrity fixture"
  version: "1.0"
  status: draft
  priority: P1
  category: OPENCODE
confidence:
  implementation_feasibility: 0.8
  requirement_clarity: 0.8
  estimate_confidence: 0.7
traceability:
  implements: []
  depends_on: []
---

# PRD-OPENCODE-001: Broken integrity fixture

## 1. Problem Statement
This fixture cites `src/missing.py`.

## 2. Goals & Non-Goals
Keep the payload shape stable.

## 3. User Stories
One user story.

## 4. Functional Requirements
FR01 uses `src/missing.py`.

## 5. Non-Functional Requirements
Stay deterministic.

## 6. Technical Approach
Use `src/missing.py`.

## 7. Test Strategy
Test `src/missing.py`.

## 8. Rollout Plan
One rollout step.

## 9. Success Metrics
Integrity findings are serialized.

## 10. Dependencies & Risks
Missing path risk.

## 11. Open Questions
None.

## 12. Traceability Matrix
| Requirement | Implementation | Test |
|-------------|----------------|------|
| FR01 | `src/missing.py` | `tests/test_missing.py::test_case` |
""",
            encoding="utf-8",
        )

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))

        assert "status_drift_warnings" in result
        assert "integrity_warnings" in result
        assert any(failure["field"] == "category" for failure in result["failures"])
        assert any("Referenced repo path does not exist" in failure["message"] for failure in result["failures"])
