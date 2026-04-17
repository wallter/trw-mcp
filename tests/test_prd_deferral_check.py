"""Tests for R-03: PRD done status must detect sprint doc deferrals.

Covers:
  - done PRD with sprint doc deferral language emits warning
  - done PRD without deferral language emits no warning
  - draft PRD skips deferral check entirely
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_tree(tmp_path: Path) -> Path:
    """Create a minimal project tree with PRD and sprint doc directories."""
    # Create the PRD directory structure
    prd_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prd_dir.mkdir(parents=True)

    # Create sprint doc directories (both active and archive)
    sprint_dir = tmp_path / "docs" / "requirements-aare-f" / "sprints"
    sprint_dir.mkdir(parents=True)
    archive_dir = tmp_path / "docs" / "requirements-aare-f" / "archive" / "sprints"
    archive_dir.mkdir(parents=True)

    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDonePrdWithSprintDeferral:
    """done PRD + sprint doc deferral language -> warning emitted."""

    def test_deferred_keyword(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-042"}

        # Write a sprint doc that mentions deferral
        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-55.md"
        sprint_doc.write_text(
            "# Sprint 55\n\n- PRD-CORE-042: FR03 deferred to Phase 2\n- PRD-CORE-043: complete\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert len(warnings) == 1
        assert "sprint-55.md" in warnings[0]
        assert "PRD-CORE-042" in warnings[0]
        assert "deferral" in warnings[0].lower() or "deferred" in warnings[0].lower()

    def test_not_in_scope_keyword(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-FIX-010"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-60.md"
        sprint_doc.write_text(
            "# Sprint 60\n\nPRD-FIX-010 FR02 explicitly NOT in scope for this sprint\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert len(warnings) >= 1
        assert "sprint-60.md" in warnings[0]

    def test_out_of_scope_keyword(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-QUAL-015"}

        archive_dir = project_tree / "docs" / "requirements-aare-f" / "archive" / "sprints"
        sprint_doc = archive_dir / "sprint-42.md"
        sprint_doc.write_text(
            "# Sprint 42\n\n| PRD-QUAL-015 | out of scope | moved to Phase 3 |\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert len(warnings) >= 1
        assert "sprint-42.md" in warnings[0]


class TestDonePrdWithoutDeferral:
    """done PRD + no deferral language -> no warning."""

    def test_no_sprint_docs(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-099"}

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_sprint_doc_mentions_prd_without_deferral(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-050"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-70.md"
        sprint_doc.write_text(
            "# Sprint 70\n\n- PRD-CORE-050: all FRs implemented and verified\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_sprint_doc_mentions_different_prd_deferred(self, project_tree: Path) -> None:
        """Deferral language for a DIFFERENT PRD should not trigger a warning."""
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-050"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-70.md"
        sprint_doc.write_text(
            "# Sprint 70\n\n- PRD-CORE-051: deferred to Phase 2\n- PRD-CORE-050: complete\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []


class TestDraftPrdSkipsDeferralCheck:
    """Non-done PRDs skip the deferral check entirely."""

    def test_draft_status(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "draft", "id": "PRD-CORE-042"}

        # Even with deferral language present, draft PRDs should not trigger
        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-55.md"
        sprint_doc.write_text(
            "# Sprint 55\n\n- PRD-CORE-042: deferred to Phase 2\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_active_status(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "active", "id": "PRD-CORE-042"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-55.md"
        sprint_doc.write_text(
            "# Sprint 55\n\n- PRD-CORE-042: deferred to Phase 2\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_missing_status(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"id": "PRD-CORE-042"}

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []


class TestDeferralCheckFailOpen:
    """Deferral check must never raise — always fail-open."""

    def test_missing_project_root(self) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-042"}
        nonexistent = Path("/nonexistent/path/that/does/not/exist")

        # Must not raise — returns empty list
        warnings = _check_sprint_deferral(frontmatter, project_root=nonexistent)
        assert warnings == []

    def test_missing_id_field(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done"}

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []
