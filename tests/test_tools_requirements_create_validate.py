"""PRD create and validate tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_tools_requirements_support import _get_tools, set_project_root  # noqa: F401


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
                sequence=int(priority[1]) + 10,
            )
            content = result["content"]
            assert f"**Implementation Confidence**: {expected}" in content
            assert f"**Priority**: {priority}" in content

    def test_auto_increments_sequence(self, tmp_path: Path) -> None:
        """When sequence=1 (default), auto-increment from existing PRDs."""
        tools = _get_tools()

        r1 = tools["trw_prd_create"].fn(
            input_text="First PRD",
            category="CORE",
            title="First",
        )
        assert r1["prd_id"] == "PRD-CORE-001"

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

    def test_accepts_project_extra_category_when_config_singleton_is_stale(self, tmp_path: Path) -> None:
        """Repo-local extra categories work even if TRWConfig was cached before config read."""
        from trw_mcp.models.config import TRWConfig, reload_config

        reload_config(TRWConfig(extra_prd_categories=[]))
        (tmp_path / ".trw" / "config.yaml").write_text("extra_prd_categories:\n- CONTENT\n")

        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Content PRD",
            category="CONTENT",
            title="Content Category",
        )

        assert result["prd_id"] == "PRD-CONTENT-001"
        assert result["category"] == "CONTENT"
        reload_config(None)


class TestTrwPrdValidate:
    """Tests for trw_prd_validate tool."""

    def test_validates_good_prd(self, tmp_path: Path) -> None:
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

    def test_detects_low_density(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-003
  title: "Sparse"
  version: "1.0"
  status: draft
  priority: P1

traceability:
  implements: [KE-001]
---

# PRD-CORE-003: Sparse PRD

## 1. Problem Statement
The system should be fast.

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
        prd_path = tmp_path / "sparse.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["total_score"] < 80.0

    def test_validation_cache_hits_for_unchanged_content(self, tmp_path: Path) -> None:
        prd_content = """---
prd:
  id: PRD-CORE-086
  title: "Cache"
---

# PRD-CORE-086: Cache

## 1. Problem Statement
Cache validation results by content hash.
"""
        prd_path = tmp_path / "cache.md"
        prd_path.write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        first = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        second = tools["trw_prd_validate"].fn(prd_path=str(prd_path))

        assert first["cache"]["hit"] is False
        assert second["cache"]["hit"] is True
        assert first["cache"]["key"] == second["cache"]["key"]

    def test_file_not_found(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import StateError

        tools = _get_tools()
        with pytest.raises(StateError, match="not found"):
            tools["trw_prd_validate"].fn(prd_path=str(tmp_path / "nonexistent.md"))
