"""Tests for requirements tools — prd_create, prd_validate, traceability_check."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.requirements as req_mod
    monkeypatch.setattr(req_mod, "_config", req_mod.TRWConfig())

    # Create .trw/ so prd_create knows it's a TRW project
    (tmp_path / ".trw").mkdir()
    return tmp_path


def _get_tools() -> dict[str, object]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP
    from trw_mcp.tools.requirements import register_requirements_tools

    srv = FastMCP("test")
    register_requirements_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


class TestTrwPrdCreate:
    """Tests for trw_prd_create tool."""

    def test_creates_prd(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Add user authentication with OAuth2 support",
            category="CORE",
            priority="P1",
            title="User Authentication",
        )
        assert result["prd_id"] == "PRD-CORE-001"
        assert result["title"] == "User Authentication"
        assert result["sections_generated"] == 12
        assert "content" in result

        # Verify content structure
        content = result["content"]
        assert "---" in content
        assert "Problem Statement" in content
        assert "Goals & Non-Goals" in content
        assert "Traceability Matrix" in content

    def test_auto_generates_title(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Implement caching layer for API responses",
            category="INFRA",
        )
        assert result["title"] == "Implement caching layer for API responses"

    def test_saves_to_disk(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Feature request",
            category="CORE",
            title="Test Feature",
        )
        assert result["output_path"] != ""
        assert Path(result["output_path"]).exists()

    def test_invalid_priority(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import ValidationError
        tools = _get_tools()
        with pytest.raises(ValidationError, match="Invalid priority"):
            tools["trw_prd_create"].fn(
                input_text="test",
                priority="P99",
            )


    def test_priority_affects_confidence(self, tmp_path: Path) -> None:
        """P0 → 0.9, P1 → 0.7, P2 → 0.6, P3 → 0.5 in both frontmatter and body."""
        tools = _get_tools()
        for priority, expected in [("P0", 0.9), ("P1", 0.7), ("P2", 0.6), ("P3", 0.5)]:
            result = tools["trw_prd_create"].fn(
                input_text=f"Test for {priority}",
                priority=priority,
                title=f"Confidence {priority}",
                sequence=int(priority[1]) + 10,  # avoid collisions
            )
            content = result["content"]
            assert f"**Implementation Confidence**: {expected}" in content
            assert f"**Priority**: {priority}" in content

    def test_auto_increments_sequence(self, tmp_path: Path) -> None:
        """When sequence=1 (default), auto-increment from existing PRDs."""
        tools = _get_tools()

        # Create first PRD
        r1 = tools["trw_prd_create"].fn(
            input_text="First PRD",
            category="CORE",
            title="First",
        )
        assert r1["prd_id"] == "PRD-CORE-001"

        # Create second PRD with default sequence — should auto-increment
        r2 = tools["trw_prd_create"].fn(
            input_text="Second PRD",
            category="CORE",
            title="Second",
        )
        assert r2["prd_id"] == "PRD-CORE-002"

    def test_explicit_sequence_not_overridden(self, tmp_path: Path) -> None:
        """When sequence > 1 is explicitly set, use it as-is."""
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Explicit sequence PRD",
            category="CORE",
            title="Explicit",
            sequence=42,
        )
        assert result["prd_id"] == "PRD-CORE-042"


class TestTrwPrdValidate:
    """Tests for trw_prd_validate tool."""

    def test_validates_good_prd(self, tmp_path: Path) -> None:
        # Create a well-formed PRD
        prd_content = """---
prd:
  id: PRD-CORE-001
  title: "Test PRD"
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

# PRD-CORE-001: Test PRD

## 1. Problem Statement
We need to solve X.

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
        prd_path = tmp_path / "test.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["valid"] is True
        assert len(result["sections_found"]) == 12

    def test_validates_incomplete_prd(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-002
  title: "Incomplete"
---

# Incomplete PRD

## 1. Problem Statement
Only one section.
"""
        prd_path = tmp_path / "incomplete.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["valid"] is False
        assert len(result["failures"]) > 0

    def test_detects_ambiguity(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-003
  title: "Ambiguous"
  version: "1.0"
  status: draft
  priority: P1

traceability:
  implements: [KE-001]
---

# PRD-CORE-003: Ambiguous PRD

## 1. Problem Statement
The system should be fast and user-friendly and robust.
It should be scalable and flexible and easy to use.

## 2. Goals & Non-Goals
## 3. User Stories
## 4. Functional Requirements
## 5. Non-Functional Requirements
## 6. Technical Approach
## 7. Test Strategy
## 8. Rollout Plan
## 9. Success Metrics
## 10. Dependencies & Risks
## 11. Open Questions
## 12. Traceability Matrix
"""
        prd_path = tmp_path / "ambiguous.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert len(result["ambiguous_terms"]) > 0
        assert "fast" in result["ambiguous_terms"]

    def test_file_not_found(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import StateError
        tools = _get_tools()
        with pytest.raises(StateError, match="not found"):
            tools["trw_prd_validate"].fn(prd_path=str(tmp_path / "nonexistent.md"))


class TestTrwTraceabilityCheck:
    """Tests for trw_traceability_check tool."""

    def test_no_prds(self, tmp_path: Path) -> None:
        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert result["total_requirements"] == 0

    def test_checks_specific_prd(self, tmp_path: Path) -> None:
        # Create a PRD with traceability
        prd_content = """---
prd:
  id: PRD-CORE-001
  title: "Traced PRD"

traceability:
  implements: [KE-FRAME-001]
---

# PRD-CORE-001: Traced PRD

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | KE-001 | `module.py:fn` | `test.py::test` | Impl |
"""
        prd_path = tmp_path / "traced.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn(prd_path=str(prd_path))
        assert result["total_requirements"] >= 1
        assert result["prd_files_analyzed"] == 1
