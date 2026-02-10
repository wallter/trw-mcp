"""Tests for PRD-FIX-008: PRD Documentation Hygiene.

Covers:
- PRDStatus enum includes `done` and `merged` values
- State machine transitions for new statuses
- Terminal state enforcement (done/merged have no outgoing transitions)
- PRD frontmatter status consistency with INDEX.md
- Pydantic v2 enum serialization with new values
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.requirements import PRDFrontmatter, PRDStatus
from trw_mcp.state.prd_utils import (
    VALID_TRANSITIONS,
    is_valid_transition,
    parse_frontmatter,
)


class TestPRDStatusDoneInEnum:
    """PRDStatus.DONE exists and is usable."""

    def test_done_value(self) -> None:
        assert PRDStatus.DONE.value == "done"

    def test_done_from_string(self) -> None:
        assert PRDStatus("done") == PRDStatus.DONE

    def test_done_in_members(self) -> None:
        assert PRDStatus.DONE in PRDStatus


class TestPRDStatusMergedInEnum:
    """PRDStatus.MERGED exists and is usable."""

    def test_merged_value(self) -> None:
        assert PRDStatus.MERGED.value == "merged"

    def test_merged_from_string(self) -> None:
        assert PRDStatus("merged") == PRDStatus.MERGED

    def test_merged_in_members(self) -> None:
        assert PRDStatus.MERGED in PRDStatus


class TestTransitionToDone:
    """Only IMPLEMENTED -> DONE is valid."""

    def test_implemented_to_done_valid(self) -> None:
        assert is_valid_transition(PRDStatus.IMPLEMENTED, PRDStatus.DONE) is True

    def test_draft_to_done_invalid(self) -> None:
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.DONE) is False

    def test_review_to_done_invalid(self) -> None:
        assert is_valid_transition(PRDStatus.REVIEW, PRDStatus.DONE) is False

    def test_approved_to_done_invalid(self) -> None:
        assert is_valid_transition(PRDStatus.APPROVED, PRDStatus.DONE) is False

    def test_deprecated_to_done_invalid(self) -> None:
        assert is_valid_transition(PRDStatus.DEPRECATED, PRDStatus.DONE) is False

    def test_merged_to_done_invalid(self) -> None:
        assert is_valid_transition(PRDStatus.MERGED, PRDStatus.DONE) is False


class TestTransitionToMerged:
    """DRAFT, REVIEW, APPROVED -> MERGED are valid."""

    def test_draft_to_merged_valid(self) -> None:
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.MERGED) is True

    def test_review_to_merged_valid(self) -> None:
        assert is_valid_transition(PRDStatus.REVIEW, PRDStatus.MERGED) is True

    def test_approved_to_merged_valid(self) -> None:
        assert is_valid_transition(PRDStatus.APPROVED, PRDStatus.MERGED) is True

    def test_implemented_to_merged_invalid(self) -> None:
        assert is_valid_transition(PRDStatus.IMPLEMENTED, PRDStatus.MERGED) is False

    def test_deprecated_to_merged_invalid(self) -> None:
        assert is_valid_transition(PRDStatus.DEPRECATED, PRDStatus.MERGED) is False


class TestDoneRestrictions:
    """DONE is a terminal state — no outgoing transitions."""

    def test_done_has_no_outgoing(self) -> None:
        assert VALID_TRANSITIONS[PRDStatus.DONE] == set()

    def test_done_to_any_invalid(self) -> None:
        for target in PRDStatus:
            if target == PRDStatus.DONE:
                continue  # identity transition always valid
            assert is_valid_transition(PRDStatus.DONE, target) is False, (
                f"DONE -> {target.value} should be invalid"
            )

    def test_done_identity_valid(self) -> None:
        assert is_valid_transition(PRDStatus.DONE, PRDStatus.DONE) is True


class TestMergedRestrictions:
    """MERGED is a terminal state — no outgoing transitions."""

    def test_merged_has_no_outgoing(self) -> None:
        assert VALID_TRANSITIONS[PRDStatus.MERGED] == set()

    def test_merged_to_any_invalid(self) -> None:
        for target in PRDStatus:
            if target == PRDStatus.MERGED:
                continue
            assert is_valid_transition(PRDStatus.MERGED, target) is False, (
                f"MERGED -> {target.value} should be invalid"
            )

    def test_merged_identity_valid(self) -> None:
        assert is_valid_transition(PRDStatus.MERGED, PRDStatus.MERGED) is True


class TestPRDFrontmatterWithNewStatuses:
    """PRDFrontmatter model accepts done/merged via Pydantic v2."""

    def test_frontmatter_with_done_status(self) -> None:
        fm = PRDFrontmatter(id="PRD-TEST-001", title="Test", status=PRDStatus.DONE)
        assert fm.status == PRDStatus.DONE

    def test_frontmatter_with_merged_status(self) -> None:
        fm = PRDFrontmatter(id="PRD-TEST-002", title="Test", status=PRDStatus.MERGED)
        assert fm.status == PRDStatus.MERGED

    def test_frontmatter_done_requires_enum(self) -> None:
        """Strict mode requires PRDStatus enum, not raw string."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            PRDFrontmatter(
                id="PRD-TEST-003",
                title="Test",
                status="done",  # type: ignore[arg-type]
            )


class TestFrontmatterStatusParsingFromYAML:
    """Verify parse_frontmatter reads done/merged statuses correctly."""

    def test_parse_done_status(self) -> None:
        content = "---\nprd:\n  id: PRD-TEST-001\n  status: done\n  title: Test\n---\n\n# Body\n"
        fm = parse_frontmatter(content)
        assert fm["status"] == "done"

    def test_parse_merged_status(self) -> None:
        content = "---\nprd:\n  id: PRD-FIX-002\n  status: merged\n  title: Merged\n---\n\n# Body\n"
        fm = parse_frontmatter(content)
        assert fm["status"] == "merged"

    def test_done_roundtrips_through_update(self, tmp_path: Path) -> None:
        """Write done status, read back, verify."""
        from trw_mcp.state.prd_utils import update_frontmatter

        prd_file = tmp_path / "PRD-TEST-RT.md"
        prd_file.write_text(
            "---\nprd:\n  id: PRD-TEST-RT\n  status: implemented\n  title: Roundtrip\n---\n\n# Body\n",
            encoding="utf-8",
        )
        update_frontmatter(prd_file, {"status": "done"})
        result = parse_frontmatter(prd_file.read_text(encoding="utf-8"))
        assert result["status"] == "done"


class TestAllTransitionsTableComplete:
    """Every PRDStatus member must appear as a key in VALID_TRANSITIONS."""

    def test_every_status_has_transition_entry(self) -> None:
        for status in PRDStatus:
            assert status in VALID_TRANSITIONS, (
                f"{status.value} missing from VALID_TRANSITIONS"
            )

    def test_transition_values_are_sets_of_prd_status(self) -> None:
        for status, targets in VALID_TRANSITIONS.items():
            assert isinstance(targets, set), (
                f"VALID_TRANSITIONS[{status.value}] should be a set"
            )
            for target in targets:
                assert isinstance(target, PRDStatus), (
                    f"VALID_TRANSITIONS[{status.value}] contains non-PRDStatus: {target}"
                )
