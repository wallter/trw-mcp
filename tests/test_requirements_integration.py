"""PRD-QUAL-008: Integration tests for requirements.py — prd_create, prd_validate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    from trw_mcp.tools._prd_template_helpers import reset_template_cache

    # Reset template cache
    reset_template_cache()

    # Create .trw/
    (tmp_path / ".trw").mkdir()
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP

    from trw_mcp.tools.requirements import register_requirements_tools

    srv = FastMCP("test-req-integration")
    register_requirements_tools(srv)
    return get_tools_sync(srv)


def _create_prd_file(
    tmp_path: Path,
    prd_id: str = "PRD-CORE-001",
    status: str = "draft",
    with_traceability: bool = False,
    with_fr: bool = False,
    with_matrix: bool = False,
    content_body: str = "",
) -> Path:
    """Create a PRD file with frontmatter for testing."""
    trace_section = ""
    if with_traceability:
        trace_section = """
traceability:
  implements: [KE-FRAME-001]"""

    body = content_body or f"# {prd_id}: Test PRD\n\n## 1. Problem Statement\nSome content.\n"

    if with_fr:
        body += f"""
### {prd_id}-FR01: First Requirement
Description of first requirement.

### {prd_id}-FR02: Second Requirement
Description of second requirement.
"""

    if with_matrix:
        body += """
## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | KE-001 | `module.py:fn` | `test.py::test` | Impl |
| FR02 | KE-002 | `handler.py:process` | `test_handler.py::test` | Impl |
"""

    prd_content = f"""---
prd:
  id: {prd_id}
  title: "Test PRD"
  version: "1.0"
  status: {status}{trace_section}
---

{body}
"""
    prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds_dir.mkdir(parents=True, exist_ok=True)
    prd_path = prds_dir / f"{prd_id}.md"
    prd_path.write_text(prd_content, encoding="utf-8")
    return prd_path


# ---------------------------------------------------------------------------
# prd_create — PRD-QUAL-121-FR02 identity collision gate
# ---------------------------------------------------------------------------


class TestPrdCreateIdentityCollisionGate:
    """FR02 acceptance: an existing active or archived identifier blocks creation
    BEFORE any write; the failure names both paths."""

    def test_explicit_sequence_collision_fails_with_both_paths_and_writes_nothing(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import ValidationError

        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        existing = prds_dir / "PRD-CORE-153-registry-hygiene.md"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("---\nprd:\n  id: PRD-CORE-153\n  title: Registry hygiene\n---\n")
        before = sorted(p.name for p in prds_dir.glob("*.md"))

        tools = _get_tools()
        with pytest.raises(ValidationError) as excinfo:
            tools["trw_prd_create"].fn(
                input_text="Conflicting thing",
                category="CORE",
                sequence=153,
                title="Conflicting thing",
            )

        message = str(excinfo.value)
        assert "PRD-CORE-153" in message
        assert "PRD-CORE-153-registry-hygiene.md" in message  # existing owner path
        assert str(prds_dir / "PRD-CORE-153.md") in message  # intended path
        # Nothing was written.
        assert sorted(p.name for p in prds_dir.glob("*.md")) == before

    def test_archived_identifier_blocks_creation(self, tmp_path: Path) -> None:
        from trw_mcp.exceptions import ValidationError

        archive = tmp_path / "docs" / "requirements-aare-f" / "archive" / "prds"
        archive.mkdir(parents=True)
        (archive / "PRD-CORE-042.md").write_text("---\nprd:\n  id: PRD-CORE-042\n---\n")

        tools = _get_tools()
        with pytest.raises(ValidationError, match="PRD-CORE-042"):
            tools["trw_prd_create"].fn(
                input_text="Reuse attempt",
                category="CORE",
                sequence=42,
                title="Reuse attempt",
            )

    def test_auto_allocation_skips_suffixed_owner_and_succeeds(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-153-registry-hygiene.md").write_text(
            "---\nprd:\n  id: PRD-CORE-153\n  title: Registry hygiene\n---\n"
        )

        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="New feature",
            category="CORE",
            title="New feature",
        )
        # The allocator must NOT re-issue 153 (suffixed stem owns it).
        assert result["prd_id"] == "PRD-CORE-154"
        assert (prds_dir / "PRD-CORE-154.md").exists()


# ---------------------------------------------------------------------------
# prd_create — edge cases not covered
# ---------------------------------------------------------------------------


class TestPrdCreateEdgeCases:
    """Edge-case tests for trw_prd_create."""

    def test_category_uppercased(self, tmp_path: Path) -> None:
        """Category is always uppercased."""
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Test feature",
            category="core",
            title="Case Test",
        )
        assert result["category"] == "CORE"

    def test_long_title_truncated(self, tmp_path: Path) -> None:
        """Auto-generated title is truncated to 60 chars."""
        tools = _get_tools()
        long_input = "A" * 100  # 100 char first line
        result = tools["trw_prd_create"].fn(
            input_text=long_input,
            category="CORE",
        )
        assert len(result["title"]) <= 60

    def test_different_categories(self, tmp_path: Path) -> None:
        """Different categories produce different PRD IDs."""
        tools = _get_tools()
        r1 = tools["trw_prd_create"].fn(
            input_text="Core feature",
            category="CORE",
            title="Core",
        )
        r2 = tools["trw_prd_create"].fn(
            input_text="Fix bug",
            category="FIX",
            title="Fix",
        )
        assert r1["prd_id"].startswith("PRD-CORE-")
        assert r2["prd_id"].startswith("PRD-FIX-")

    def test_p0_confidence_is_highest(self, tmp_path: Path) -> None:
        """P0 priority produces 0.9 confidence."""
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Urgent fix",
            category="FIX",
            priority="P0",
            title="P0 Fix",
        )
        assert "0.9" in result["content"]

    def test_p3_confidence_is_lowest(self, tmp_path: Path) -> None:
        """P3 priority produces 0.5 confidence."""
        tools = _get_tools()
        result = tools["trw_prd_create"].fn(
            input_text="Low priority",
            category="CORE",
            priority="P3",
            title="P3 Feature",
        )
        assert "0.5" in result["content"]


# ---------------------------------------------------------------------------
# prd_validate — V2 validation fields
# ---------------------------------------------------------------------------


class TestPrdValidateV2:
    """Tests for V2 semantic validation in trw_prd_validate."""

    def test_v2_fields_present(self, tmp_path: Path) -> None:
        """Validate result includes V2 fields."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001")

        tools = _get_tools()
        prd_path = tmp_path / "docs" / "requirements-aare-f" / "prds" / "PRD-CORE-001.md"
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert "total_score" in result
        assert "quality_tier" in result
        assert "grade" in result
        assert "dimensions" in result
        assert "improvement_suggestions" in result

    def test_skeleton_prd_low_score(self, tmp_path: Path) -> None:
        """Skeleton PRD gets low quality score."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001")

        tools = _get_tools()
        prd_path = tmp_path / "docs" / "requirements-aare-f" / "prds" / "PRD-CORE-001.md"
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        assert result["total_score"] < 50  # Skeleton should score low

    def test_traceability_dimension_exposes_quality_flywheel_details(self, tmp_path: Path) -> None:
        """Traceability dimension includes PRD-QUAL-056 coverage details in tool output."""
        body = """# PRD-CORE-001: Test PRD

## 4. Functional Requirements

### PRD-CORE-001-FR01: Toggle persistence
Implementation: src/service.py
Test: test_service.py::test_toggle_persists
```assertions
grep_present: "toggle"
```

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | `src/service.py` | `test_service.py::test_toggle_persists` | Impl |
"""
        prd_path = _create_prd_file(tmp_path, prd_id="PRD-CORE-001", content_body=body)

        tools = _get_tools()
        result = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        traceability = next(d for d in result["dimensions"] if d["name"] == "traceability")

        assert "details" in traceability
        assert traceability["details"]["file_path_coverage"] == 1.0
        assert traceability["details"]["assertion_coverage"] == 1.0


# ---------------------------------------------------------------------------
# _extract_prefill — edge cases
# ---------------------------------------------------------------------------


class TestExtractPrefillEdgeCases:
    """Tests for _extract_prefill error handling."""

    def test_none_input_handled(self) -> None:
        """_extract_prefill handles None input gracefully."""
        from trw_mcp.tools.requirements import _extract_prefill

        result = _extract_prefill(None)  # type: ignore[arg-type]
        assert result["file_refs"] == []
        assert result["prd_deps"] == []
        assert result["goals"] == []
        assert result["slos"] == []

    def test_integer_input_handled(self) -> None:
        """_extract_prefill handles non-string input."""
        from trw_mcp.tools.requirements import _extract_prefill

        result = _extract_prefill(123)  # type: ignore[arg-type]
        assert result["file_refs"] == []


# ---------------------------------------------------------------------------
# PRD-QUAL-114-NFR05 — cache/verification hardening preserves public contract
# ---------------------------------------------------------------------------


class TestCacheHardeningBackwardCompatibility:
    """The public trw_prd_validate response stays compatible; new keys are additive."""

    def test_prd_validate_cache_hardening_preserves_public_contract(self, tmp_path: Path) -> None:
        prd_path = _create_prd_file(
            tmp_path,
            prd_id="PRD-CORE-114",
            status="draft",
            with_traceability=True,
            with_fr=True,
            with_matrix=True,
        )
        tools = _get_tools()
        first = tools["trw_prd_validate"].fn(prd_path=str(prd_path))
        second = tools["trw_prd_validate"].fn(prd_path=str(prd_path))

        # Prior public response keys remain present.
        for key in (
            "path",
            "valid",
            "completeness_score",
            "traceability_coverage",
            "measured_traceability_coverage",
            "total_score",
            "quality_tier",
            "grade",
            "failures",
            "cache",
        ):
            assert key in first, f"missing public response key: {key}"

        # Validity and severity are stable across a cache miss then hit.
        assert first["valid"] == second["valid"]
        assert first["total_score"] == second["total_score"]
        assert {f["severity"] for f in first["failures"]} == {f["severity"] for f in second["failures"]}
        assert first["cache"]["hit"] is False
        assert second["cache"]["hit"] is True

        # New cache diagnostics are additive and do not weaken the result.
        assert second["cache"]["storage_version"] == 2
        assert second["cache"]["miss_reason"] == ""
        assert second["cache"]["degraded"] is False
        # Token-bloat W5: the deprecated ``implementation_test_link_coverage``
        # alias (an exact duplicate of measured_traceability_coverage) is no
        # longer emitted on the wire; the canonical metric remains.
        assert isinstance(first["measured_traceability_coverage"], float)
        assert "implementation_test_link_coverage" not in first
