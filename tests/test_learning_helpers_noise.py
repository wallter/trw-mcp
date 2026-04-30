"""Tests for learning helper noise summary filtering."""

from __future__ import annotations

import time
from pathlib import Path
from re import _parser as re_parser

import pytest

from tests._learning_helpers_test_support import set_project_root  # noqa: F401
from trw_mcp.exceptions import StateError
from trw_mcp.state.analytics.core import _NOISE_PATTERNS
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._learning_helpers import is_noise_summary


def _has_nested_repeat(parsed: re_parser.SubPattern) -> bool:
    for op, value in parsed.data:
        if op in {re_parser.MAX_REPEAT, re_parser.MIN_REPEAT}:
            _min, _max, inner = value
            if any(child_op in {re_parser.MAX_REPEAT, re_parser.MIN_REPEAT} for child_op, _ in inner.data):
                return True
            if _has_nested_repeat(inner):
                return True
            continue

        if op is re_parser.SUBPATTERN:
            _group, _add_flags, _del_flags, inner = value
            if _has_nested_repeat(inner):
                return True
            continue

        if op is re_parser.BRANCH:
            _none_value, branches = value
            if any(_has_nested_repeat(branch) for branch in branches):
                return True

    return False


class TestNoiseFilter:
    """PRD-QUAL-032-FR09: Reject auto-generated noise summaries."""

    @pytest.mark.unit
    def test_rejects_repeated_operation_prefix(self) -> None:
        assert is_noise_summary("Repeated operation: trw_checkpoint called 3 times") is True

    @pytest.mark.unit
    def test_rejects_success_prefix(self) -> None:
        assert is_noise_summary("Success: build passed") is True

    @pytest.mark.unit
    def test_accepts_normal_summary(self) -> None:
        assert is_noise_summary("Pydantic v2 requires use_enum_values=True") is False

    @pytest.mark.unit
    def test_accepts_empty_summary(self) -> None:
        assert is_noise_summary("") is False

    @pytest.mark.unit
    def test_does_not_match_substring(self) -> None:
        """Prefix match only — 'Success:' in the middle should pass."""
        assert is_noise_summary("The operation was a Success: tests passed") is False

    @pytest.mark.unit
    def test_rejects_file_read_confirmation(self) -> None:
        assert is_noise_summary("I read the file successfully") is True

    @pytest.mark.unit
    def test_rejects_file_read_variation(self) -> None:
        assert is_noise_summary("I read the configuration file") is True

    @pytest.mark.unit
    def test_rejects_test_pass_notification(self) -> None:
        assert is_noise_summary("The test passed") is True

    @pytest.mark.unit
    def test_rejects_tests_passing(self) -> None:
        assert is_noise_summary("The tests are passing now") is True

    @pytest.mark.unit
    def test_rejects_all_tests_passed(self) -> None:
        assert is_noise_summary("All tests passed") is True

    @pytest.mark.unit
    def test_rejects_edit_confirmation(self) -> None:
        assert is_noise_summary("I made the edit to the file") is True

    @pytest.mark.unit
    def test_rejects_edit_variation(self) -> None:
        assert is_noise_summary("I made the change successfully") is True

    @pytest.mark.unit
    def test_rejects_updated_the_file(self) -> None:
        assert is_noise_summary("Updated the file with the fix") is True

    @pytest.mark.unit
    def test_rejects_updated_the_code(self) -> None:
        assert is_noise_summary("Updated the code to handle the edge case") is True

    @pytest.mark.unit
    def test_rejects_status_acknowledgment(self) -> None:
        assert is_noise_summary("The build completed successfully") is True

    @pytest.mark.unit
    def test_rejects_task_completed(self) -> None:
        assert is_noise_summary("Task completed: updated the tests") is True

    @pytest.mark.unit
    def test_rejects_confirmed_prefix(self) -> None:
        assert is_noise_summary("Confirmed: the fix works") is True

    @pytest.mark.unit
    def test_rejects_done_prefix(self) -> None:
        assert is_noise_summary("Done: all changes applied") is True

    @pytest.mark.unit
    def test_rejects_completed_prefix(self) -> None:
        assert is_noise_summary("Completed: migration script updated") is True

    @pytest.mark.unit
    def test_accepts_read_pattern_learning(self) -> None:
        """A learning ABOUT reading should not be rejected."""
        assert is_noise_summary("File reads fail silently when path contains unicode") is False

    @pytest.mark.unit
    def test_accepts_test_pattern_learning(self) -> None:
        """A learning ABOUT tests should not be rejected."""
        assert is_noise_summary("Tests require reset_backend() fixture for SQLite isolation") is False

    @pytest.mark.unit
    def test_accepts_update_pattern_learning(self) -> None:
        """A learning ABOUT updates should not be rejected."""
        assert is_noise_summary("Updating config requires singleton reset between tests") is False

    @pytest.mark.unit
    def test_accepts_completion_learning(self) -> None:
        """A learning that mentions completion in a substantive way."""
        assert is_noise_summary("Completion handler must flush before process exit") is False

    @pytest.mark.unit
    def test_rejects_checked_prefix(self) -> None:
        """Action-report summaries starting with 'I checked' are rejected."""
        assert is_noise_summary("I checked the API docs") is True

    @pytest.mark.unit
    def test_rejects_vague_fixed_issue_summary(self) -> None:
        """Vague 'Fixed the issue' summaries are rejected as low-value noise."""
        assert is_noise_summary("Fixed the issue") is True

    @pytest.mark.unit
    def test_accepts_substantive_fixed_summary(self) -> None:
        """Detailed fix summaries remain valid despite starting with 'Fixed the'."""
        assert (
            is_noise_summary(
                "Fixed the OAuth callback vulnerability by adding state parameter validation",
            )
            is False
        )

    @pytest.mark.unit
    def test_noise_filters_use_precompiled_patterns(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Noise filtering must not compile regex from user input at call time."""
        import trw_mcp.state.analytics.core as analytics_core

        def _fail_compile(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("re.compile should not run during is_noise_summary")

        monkeypatch.setattr(analytics_core.re, "compile", _fail_compile)

        assert is_noise_summary("I read the file successfully") is True
        assert is_noise_summary("OAuth callbacks need explicit state validation") is False

    @pytest.mark.unit
    def test_is_noise_perf(self) -> None:
        """Expanded noise detection stays within the PRD budget for 10k 500-char inputs."""
        summary = ("OAuth callbacks require explicit state validation and replay guards. " * 8)[:500]

        start = time.perf_counter()
        for _ in range(10_000):
            is_noise_summary(summary)
        elapsed = time.perf_counter() - start

        assert elapsed < 10.0

    @pytest.mark.unit
    def test_noise_patterns_avoid_nested_repeat_redos_shapes(self) -> None:
        """Static regex analysis rejects nested-repeat shapes that commonly cause ReDoS."""
        for pattern in _NOISE_PATTERNS:
            parsed = re_parser.parse(pattern.pattern)
            assert not _has_nested_repeat(parsed), pattern.pattern

    def test_noise_patterns_preserve_live_high_impact_corpus(self) -> None:
        """Repo corpus validation should show zero false positives for high-impact learnings."""
        repo_root = Path(__file__).resolve().parents[2]
        entries_dir = repo_root / ".trw" / "learnings" / "entries"
        if not entries_dir.exists():
            pytest.skip("live learning corpus unavailable")

        reader = FileStateReader()
        candidates: list[tuple[Path, str]] = []
        for entry_file in entries_dir.glob("*.yaml"):
            try:
                data = reader.read_yaml(entry_file)
            except StateError:
                continue
            impact = float(str(data.get("impact", 0.5)))
            if impact < 0.5:
                continue
            candidates.append((entry_file, str(data.get("summary", ""))))

        assert candidates
        false_positives = [entry_file.name for entry_file, summary in candidates if is_noise_summary(summary)]
        assert false_positives == []
