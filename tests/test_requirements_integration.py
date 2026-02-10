"""PRD-QUAL-008: Integration tests for requirements.py — coverage gap tests.

Covers the previously untested paths:
- trw_prd_status_update: state machine validation, force parameter, guard checks,
  _log_status_change_event, invalid status, identity transitions
- trw_traceability_check: scan all PRDs, untraced PRDs, FR requirements,
  findings registry integration, non-existent prd_file
- _resolve_prd_path: not found case
- _log_status_change_event: active run, no run, force_override
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.requirements as req_mod

    monkeypatch.setattr(req_mod, "_config", TRWConfig())

    # Reset template cache
    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_BODY", None)
    monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_VERSION", None)

    # Create .trw/
    (tmp_path / ".trw").mkdir()
    return tmp_path


def _get_tools() -> dict[str, object]:
    """Create fresh server and return tool map."""
    from fastmcp import FastMCP
    from trw_mcp.tools.requirements import register_requirements_tools

    srv = FastMCP("test-req-integration")
    register_requirements_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


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
# trw_prd_status_update — state machine and guard tests
# ---------------------------------------------------------------------------


class TestPrdStatusUpdate:
    """Tests for trw_prd_status_update tool."""

    def test_valid_draft_to_review(self, tmp_path: Path) -> None:
        """Valid transition: draft -> review succeeds."""
        # Create a PRD with substantive content to pass density guard
        body = "# PRD-CORE-001: Test\n\n" + "\n".join(
            f"## {i}. Section {i}\nSubstantive content for section {i}. "
            "This has enough words to pass the density check."
            for i in range(1, 13)
        )
        _create_prd_file(tmp_path, status="draft", content_body=body)

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="review",
        )
        assert result["previous_status"] == "draft"
        assert result["new_status"] == "review"
        assert result["transition_valid"] is True
        assert result["updated"] is True

    def test_invalid_transition_rejected(self, tmp_path: Path) -> None:
        """Invalid transition: draft -> approved is rejected."""
        _create_prd_file(tmp_path, status="draft")

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="approved",
        )
        assert result["transition_valid"] is False
        assert result["updated"] is False
        assert "Invalid transition" in str(result["reason"])

    def test_invalid_transition_draft_to_implemented(self, tmp_path: Path) -> None:
        """Invalid transition: draft -> implemented is rejected."""
        _create_prd_file(tmp_path, status="draft")

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="implemented",
        )
        assert result["transition_valid"] is False
        assert result["updated"] is False

    def test_identity_transition_noop(self, tmp_path: Path) -> None:
        """Identity transition: draft -> draft is valid but no-op."""
        _create_prd_file(tmp_path, status="draft")

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="draft",
        )
        assert result["transition_valid"] is True
        assert result["updated"] is False  # No change

    def test_invalid_target_status_raises(self, tmp_path: Path) -> None:
        """Invalid target status raises ValidationError."""
        _create_prd_file(tmp_path, status="draft")

        tools = _get_tools()
        with pytest.raises(ValidationError, match="Invalid target status"):
            tools["trw_prd_status_update"].fn(
                prd_id="PRD-CORE-001",
                target_status="INVALID_STATUS",
            )

    def test_prd_not_found_raises(self, tmp_path: Path) -> None:
        """Non-existent PRD raises StateError."""
        tools = _get_tools()
        with pytest.raises(StateError, match="PRD file not found"):
            tools["trw_prd_status_update"].fn(
                prd_id="PRD-CORE-999",
                target_status="review",
            )

    def test_force_bypasses_guard_checks(self, tmp_path: Path) -> None:
        """Force=True bypasses guard checks but requires reason."""
        # Create a PRD with minimal content (would fail density guard)
        _create_prd_file(tmp_path, status="draft")

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="review",
            force=True,
            reason="Admin override for testing",
        )
        assert result["force_used"] is True
        assert result["updated"] is True
        assert result["guard_passed"] is True
        assert "Guard bypassed" in str(result["reason"])

    def test_force_without_reason_raises(self, tmp_path: Path) -> None:
        """Force=True without reason raises ValidationError."""
        _create_prd_file(tmp_path, status="draft")

        tools = _get_tools()
        with pytest.raises(ValidationError, match="reason is required"):
            tools["trw_prd_status_update"].fn(
                prd_id="PRD-CORE-001",
                target_status="review",
                force=True,
                reason="",
            )

    def test_force_with_whitespace_only_reason_raises(self, tmp_path: Path) -> None:
        """Force=True with whitespace-only reason raises."""
        _create_prd_file(tmp_path, status="draft")

        tools = _get_tools()
        with pytest.raises(ValidationError, match="reason is required"):
            tools["trw_prd_status_update"].fn(
                prd_id="PRD-CORE-001",
                target_status="review",
                force=True,
                reason="   ",
            )

    def test_guard_density_check_fails(self, tmp_path: Path) -> None:
        """Draft -> review fails when content density is too low."""
        # Create a PRD that's mostly placeholders/empty sections
        sparse_body = """# PRD-CORE-001: Sparse

---

## 1. Problem Statement

---

## 2. Goals & Non-Goals

---

## 3. User Stories

---
"""
        _create_prd_file(tmp_path, status="draft", content_body=sparse_body)

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="review",
        )
        assert result["guard_passed"] is False
        assert result["updated"] is False
        assert "density" in str(result["reason"]).lower()

    def test_review_to_draft_valid(self, tmp_path: Path) -> None:
        """Review -> draft is a valid backward transition."""
        _create_prd_file(tmp_path, status="review")

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="draft",
        )
        assert result["transition_valid"] is True
        assert result["updated"] is True

    def test_review_to_approved_guard_check(self, tmp_path: Path) -> None:
        """Review -> approved runs V2 quality validation guard."""
        # Create a skeleton PRD (will fail quality guard)
        _create_prd_file(tmp_path, status="review")

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="approved",
        )
        # Skeleton PRD should fail the quality gate
        assert result["guard_passed"] is False
        assert result["updated"] is False

    def test_frontmatter_unknown_status_defaults_draft(self, tmp_path: Path) -> None:
        """PRD with invalid status in frontmatter defaults to draft."""
        prd_content = """---
prd:
  id: PRD-CORE-001
  title: "Test"
  version: "1.0"
  status: unknown_garbage
---

# PRD-CORE-001: Test
## 1. Problem Statement
Content.
"""
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True, exist_ok=True)
        (prds_dir / "PRD-CORE-001.md").write_text(prd_content, encoding="utf-8")

        tools = _get_tools()
        result = tools["trw_prd_status_update"].fn(
            prd_id="PRD-CORE-001",
            target_status="draft",
        )
        assert result["previous_status"] == "draft"


# ---------------------------------------------------------------------------
# _log_status_change_event — event logging paths
# ---------------------------------------------------------------------------


class TestLogStatusChangeEvent:
    """Tests for _log_status_change_event."""

    def test_logs_event_with_active_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Event is logged to events.jsonl when an active run exists."""
        from trw_mcp.tools.requirements import _log_status_change_event

        # Create a run directory
        run_dir = tmp_path / "docs" / "test" / "runs" / "20260209T000000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test\ntask: test\n", encoding="utf-8",
        )

        _log_status_change_event(
            prd_id="PRD-CORE-001",
            previous_status="draft",
            new_status="review",
            force_used=False,
            reason="",
        )
        events_path = meta / "events.jsonl"
        assert events_path.exists()
        content = events_path.read_text(encoding="utf-8")
        assert "prd_status_change" in content
        assert "PRD-CORE-001" in content

    def test_logs_force_override_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """force_override=True is included in event data."""
        from trw_mcp.tools.requirements import _log_status_change_event

        run_dir = tmp_path / "docs" / "test" / "runs" / "20260209T000000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test\ntask: test\n", encoding="utf-8",
        )

        _log_status_change_event(
            prd_id="PRD-CORE-001",
            previous_status="draft",
            new_status="approved",
            force_used=True,
            reason="Admin override",
            force_override=True,
        )
        events_path = meta / "events.jsonl"
        content = events_path.read_text(encoding="utf-8")
        assert "force_override" in content

    def test_no_active_run_graceful(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No active run logs debug-level message, does not raise."""
        from trw_mcp.tools.requirements import _log_status_change_event

        # tmp_path has no docs/*/runs/ structure
        # Should not raise — StateError is caught internally
        _log_status_change_event(
            prd_id="PRD-CORE-001",
            previous_status="draft",
            new_status="review",
            force_used=False,
            reason="",
        )
        # If we get here without an exception, the test passes


# ---------------------------------------------------------------------------
# trw_traceability_check — coverage gaps
# ---------------------------------------------------------------------------


class TestTraceabilityCheckIntegration:
    """Integration tests for trw_traceability_check."""

    def test_scan_all_prds_in_directory(self, tmp_path: Path) -> None:
        """Scanning without prd_path analyzes all PRDs in directory."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001", with_traceability=True)
        _create_prd_file(tmp_path, prd_id="PRD-CORE-002", with_traceability=False)

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert result["prd_files_analyzed"] == 2

    def test_untraced_prds_reported(self, tmp_path: Path) -> None:
        """PRDs without traceability.implements appear in untraced list."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001", with_traceability=False)

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert "PRD-CORE-001" in result["untraced_requirements"]

    def test_traced_prd_not_in_untraced(self, tmp_path: Path) -> None:
        """PRD with traceability.implements is not in untraced list."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001", with_traceability=True)

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert "PRD-CORE-001" not in result.get("untraced_requirements", [])
        assert result["traced_requirements"] >= 1

    def test_fr_requirements_counted(self, tmp_path: Path) -> None:
        """FR requirements in body are counted in total."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001", with_fr=True)

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        # 1 from frontmatter + 2 FRs from body = 3
        assert result["total_requirements"] >= 3

    def test_traceability_matrix_traced(self, tmp_path: Path) -> None:
        """Traceability matrix with impl refs counts as traced."""
        _create_prd_file(
            tmp_path, prd_id="PRD-CORE-001",
            with_fr=True, with_matrix=True,
        )

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert result["traced_requirements"] >= 1

    def test_template_file_excluded(self, tmp_path: Path) -> None:
        """TEMPLATE.md is excluded from analysis."""
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True, exist_ok=True)
        (prds_dir / "TEMPLATE.md").write_text("# Template\n", encoding="utf-8")
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001")

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert result["prd_files_analyzed"] == 1  # Only PRD-CORE-001

    def test_findings_registry_integration(self, tmp_path: Path) -> None:
        """Unlinked critical findings are flagged in traceability check."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001", with_traceability=True)

        # Create a findings registry with an unlinked critical finding
        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                {
                    "id": "F-W1-S1-001",
                    "summary": "Critical bug",
                    "severity": "critical",
                    "status": "open",
                    "target_prd": None,
                },
                {
                    "id": "F-W1-S2-001",
                    "summary": "Linked finding",
                    "severity": "high",
                    "status": "acknowledged",
                    "target_prd": "PRD-FIX-001",
                },
            ],
            "total_count": 2,
            "runs_indexed": ["run-1"],
        })

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert "F-W1-S1-001" in result["unlinked_findings"]
        assert "F-W1-S2-001" not in result["unlinked_findings"]
        assert result["unlinked_findings_count"] == 1

    def test_passes_gate_field(self, tmp_path: Path) -> None:
        """passes_gate reflects coverage vs threshold."""
        _create_prd_file(
            tmp_path, prd_id="PRD-CORE-001",
            with_traceability=True, with_fr=True, with_matrix=True,
        )

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        # passes_gate should be a boolean
        assert isinstance(result["passes_gate"], bool)
        assert "coverage_threshold" in result

    def test_specific_prd_path(self, tmp_path: Path) -> None:
        """Passing specific prd_path only analyzes that file."""
        prd_path = _create_prd_file(
            tmp_path, prd_id="PRD-CORE-001", with_traceability=True,
        )
        _create_prd_file(tmp_path, prd_id="PRD-CORE-002")

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn(prd_path=str(prd_path))
        assert result["prd_files_analyzed"] == 1


# ---------------------------------------------------------------------------
# _resolve_prd_path
# ---------------------------------------------------------------------------


class TestResolvePrdPath:
    """Tests for _resolve_prd_path helper."""

    def test_resolves_existing_prd(self, tmp_path: Path) -> None:
        """Returns path when PRD file exists."""
        from trw_mcp.tools.requirements import _resolve_prd_path

        _create_prd_file(tmp_path, prd_id="PRD-CORE-001")
        path = _resolve_prd_path("PRD-CORE-001")
        assert path.exists()
        assert path.name == "PRD-CORE-001.md"

    def test_raises_for_missing_prd(self, tmp_path: Path) -> None:
        """Raises StateError for non-existent PRD."""
        from trw_mcp.tools.requirements import _resolve_prd_path

        with pytest.raises(StateError, match="PRD file not found"):
            _resolve_prd_path("PRD-CORE-999")


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
            input_text="Core feature", category="CORE", title="Core",
        )
        r2 = tools["trw_prd_create"].fn(
            input_text="Fix bug", category="FIX", title="Fix",
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


# ---------------------------------------------------------------------------
# Edge cases for remaining uncovered lines
# ---------------------------------------------------------------------------


class TestTraceabilityEdgeCases:
    """Additional edge cases for traceability_check."""

    def test_nonexistent_prd_file_skipped(self, tmp_path: Path) -> None:
        """A file that was deleted after collection is skipped."""
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prd_path = _create_prd_file(tmp_path, prd_id="PRD-CORE-001")

        tools = _get_tools()
        # Delete the file after it would be collected by glob
        # We can't easily test this in the scan-all path, but we can
        # pass a non-existent path to trigger the `continue` branch
        import os
        os.remove(prd_path)

        result = tools["trw_traceability_check"].fn()
        # No files exist anymore
        assert result["total_requirements"] == 0

    def test_findings_registry_non_dict_entry_skipped(
        self, tmp_path: Path,
    ) -> None:
        """Non-dict entries in findings registry are skipped."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001", with_traceability=True)

        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(registry_dir / "registry.yaml", {
            "entries": [
                "not-a-dict-entry",
                42,
                {
                    "id": "F-W1-S1-001",
                    "summary": "Valid entry",
                    "severity": "critical",
                    "status": "open",
                    "target_prd": None,
                },
            ],
            "total_count": 3,
            "runs_indexed": [],
        })

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        # Only the dict entry should be processed
        assert "F-W1-S1-001" in result["unlinked_findings"]

    def test_findings_registry_corrupt_graceful(
        self, tmp_path: Path,
    ) -> None:
        """Corrupt findings registry does not crash traceability check."""
        _create_prd_file(tmp_path, prd_id="PRD-CORE-001", with_traceability=True)

        registry_dir = tmp_path / ".trw" / "findings"
        registry_dir.mkdir(parents=True)
        (registry_dir / "registry.yaml").write_text(
            "{{bad yaml!", encoding="utf-8",
        )

        tools = _get_tools()
        result = tools["trw_traceability_check"].fn()
        assert result["unlinked_findings_count"] == 0


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


class TestLogStatusChangeGeneralException:
    """Test the general exception handler in _log_status_change_event."""

    def test_general_exception_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """General exceptions (not StateError) are caught and logged."""
        from trw_mcp.tools.requirements import _log_status_change_event

        # Create run so resolve_run_path succeeds
        run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260209T000000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "test-run", "task": "test-task",
        })

        # Make events.jsonl a directory to trigger an OSError/IsADirectoryError
        events_path = meta / "events.jsonl"
        events_path.mkdir()

        # Should not raise
        _log_status_change_event(
            prd_id="PRD-CORE-001",
            previous_status="draft",
            new_status="review",
            force_used=False,
            reason="",
        )
