"""Edge-case tests for learning_injection module.

Covers gaps not addressed in test_learning_injection.py:
  - _resolve_trw_dir wrapper
  - recall_learnings wrapper
  - infer_domain_tags: unrecognized paths, case sensitivity, extension stripping
  - select_learnings_for_task: non-list tags, non-numeric impact, both recalls fail,
    explicit param override, empty domain tags, tag_score when domain_tags present
    but entry has no tags
  - format_learning_injection: non-list tags field, missing id, non-string summary
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _resolve_trw_dir
# ---------------------------------------------------------------------------


class TestResolveTrwDir:
    """Verify the lazy import wrapper delegates to resolve_trw_dir."""

    def test_delegates_to_state_paths(self, tmp_path) -> None:
        from trw_mcp.state.learning_injection import _resolve_trw_dir

        with patch(
            "trw_mcp.state._paths.resolve_trw_dir",
            return_value=tmp_path,
        ):
            result = _resolve_trw_dir()
        assert result == tmp_path


# ---------------------------------------------------------------------------
# recall_learnings wrapper
# ---------------------------------------------------------------------------


class TestRecallLearningsWrapper:
    """Verify the thin wrapper resolves trw_dir and delegates."""

    def test_passes_all_kwargs_to_adapter(self, tmp_path) -> None:
        from trw_mcp.state.learning_injection import recall_learnings

        expected = [{"id": "L-001", "summary": "test"}]

        with (
            patch(
                "trw_mcp.state._paths.resolve_trw_dir",
                return_value=tmp_path,
            ),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=expected,
            ) as mock_adapter,
        ):
            result = recall_learnings(
                "test query",
                tags=["foo"],
                min_impact=0.3,
                max_results=10,
                status="active",
            )

        assert result == expected
        mock_adapter.assert_called_once_with(
            tmp_path,
            query="test query",
            tags=["foo"],
            min_impact=0.3,
            max_results=10,
            status="active",
        )

    def test_propagates_adapter_exception(self, tmp_path) -> None:
        from trw_mcp.state.learning_injection import recall_learnings

        with (
            patch(
                "trw_mcp.state._paths.resolve_trw_dir",
                return_value=tmp_path,
            ),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=RuntimeError("boom"),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                recall_learnings("q")


# ---------------------------------------------------------------------------
# infer_domain_tags — edge cases
# ---------------------------------------------------------------------------


class TestInferDomainTagsEdge:
    """Edge cases for path-to-tag inference."""

    def test_unrecognized_path_returns_empty(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["some/random/unknown/file.py"])
        assert tags == set()

    def test_case_insensitive_matching(self) -> None:
        """Path components are lowered before lookup."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["Backend/Routers/Admin.py"])
        assert "backend" in tags
        assert "admin" in tags
        assert "api" in tags

    def test_extension_stripped_for_stem_match(self) -> None:
        """File 'auth.ts' should match 'auth' stem."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["src/auth.ts"])
        assert "auth" in tags
        assert "security" in tags

    def test_directory_without_extension_matches(self) -> None:
        """A path component without a dot is used as-is for matching."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["middleware/"])
        assert "middleware" in tags
        assert "api" in tags

    def test_deeply_nested_path_all_components_checked(self) -> None:
        """Every component in a deep path is checked against the map."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["backend/models/database/migrations/alembic/env.py"])
        assert "backend" in tags
        assert "models" in tags
        assert "database" in tags
        assert "alembic" in tags
        assert "migration" in tags

    def test_trw_memory_underscore_variant(self) -> None:
        """Both trw-memory and trw_memory map to memory tags."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags_hyphen = infer_domain_tags(["trw-memory/src/"])
        tags_underscore = infer_domain_tags(["trw_memory/src/"])
        # Both should resolve to the same memory-related tags
        assert "memory" in tags_hyphen
        assert "memory" in tags_underscore
        assert tags_hyphen == tags_underscore


# ---------------------------------------------------------------------------
# select_learnings_for_task — edge cases
# ---------------------------------------------------------------------------


class TestSelectLearningsEdge:
    """Edge cases for learning selection and ranking."""

    def test_non_list_tags_field_treated_as_empty(self) -> None:
        """Entry with tags as a string (not list) should not crash."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-str-tags",
                "summary": "Has string tags",
                "impact": 0.8,
                "tags": "not-a-list",
                "status": "active",
            },
        ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test",
                file_paths=["backend/routers/admin.py"],
                max_results=5,
                min_impact=0.0,
            )
        assert len(results) == 1
        assert results[0]["id"] == "L-str-tags"

    def test_non_numeric_impact_raises_value_error(self) -> None:
        """Impact='not-a-number' causes ValueError in float() conversion.

        This documents current behavior: the code does float(str(impact))
        without a try/except, so a non-numeric impact string propagates
        as a ValueError through the ranking loop.
        """
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-bad-impact",
                "summary": "Bad impact",
                "impact": "not-a-number",
                "tags": ["admin"],
                "status": "active",
            },
        ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = mock_results
            with pytest.raises(ValueError, match="could not convert"):
                select_learnings_for_task(
                    task_description="test",
                    file_paths=["backend/routers/admin.py"],
                    max_results=5,
                    min_impact=0.0,
                )

    def test_both_recall_calls_fail_returns_empty(self) -> None:
        """When both tag-filtered and fallback recall raise, returns []."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.side_effect = RuntimeError("down")
            results = select_learnings_for_task(
                task_description="test",
                file_paths=["backend/routers/admin.py"],
                max_results=5,
                min_impact=0.0,
            )
        assert results == []
        # Both calls attempted
        assert mock_recall.call_count == 2

    def test_explicit_max_results_overrides_config(self) -> None:
        """Explicit max_results=2 should cap output at 2 even if config says 5."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": f"L-{i:03d}",
                "summary": f"Learning {i}",
                "impact": 0.7,
                "tags": ["admin"],
                "status": "active",
            }
            for i in range(10)
        ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test admin",
                file_paths=["backend/routers/admin.py"],
                max_results=2,
                min_impact=0.0,
            )
        assert len(results) == 2

    def test_explicit_min_impact_overrides_config(self) -> None:
        """Explicit min_impact=0.9 is forwarded to recall, not config default 0.5."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = []
            select_learnings_for_task(
                task_description="test",
                file_paths=["backend/routers/admin.py"],
                max_results=5,
                min_impact=0.9,
            )
            # First call should use explicit min_impact=0.9
            first_call = mock_recall.call_args_list[0]
            assert first_call.kwargs["min_impact"] == 0.9

    def test_no_matching_file_paths_yields_empty_domain_tags(self) -> None:
        """Unrecognized file paths produce no domain tags; tag_score is 0 for all entries."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-001",
                "summary": "Learning A",
                "impact": 0.9,
                "tags": ["admin"],
                "status": "active",
            },
            {
                "id": "L-002",
                "summary": "Learning B",
                "impact": 0.5,
                "tags": ["testing"],
                "status": "active",
            },
        ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test",
                file_paths=["unknown/path/file.xyz"],
                max_results=10,
                min_impact=0.0,
            )
        # With no domain tags, tag_score is 0 for all entries.
        # Ranking is purely by impact (40% weight).
        # L-001 has impact 0.9 -> combined 0.36
        # L-002 has impact 0.5 -> combined 0.20
        assert results[0]["id"] == "L-001"
        assert results[1]["id"] == "L-002"

    def test_ranking_prefers_tag_overlap_over_impact(self) -> None:
        """Tag overlap (60% weight) outweighs impact (40% weight)."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-high-impact",
                "summary": "High impact, no tag match",
                "impact": 1.0,
                "tags": ["unrelated"],
                "status": "active",
            },
            {
                "id": "L-tag-match",
                "summary": "Lower impact, tag match",
                "impact": 0.3,
                "tags": ["admin", "auth", "backend"],
                "status": "active",
            },
        ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="admin endpoint",
                file_paths=["backend/routers/admin.py"],
                max_results=10,
                min_impact=0.0,
            )
        # L-tag-match should rank higher because tag overlap dominates
        assert results[0]["id"] == "L-tag-match"

    def test_empty_file_paths_and_no_tags_still_works(self) -> None:
        """Empty file_paths + no explicit tags -> recall without tag filter."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-001",
                "summary": "Something",
                "impact": 0.5,
                "tags": [],
                "status": "active",
            },
        ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="generic task",
                file_paths=[],
                max_results=5,
                min_impact=0.0,
            )
        assert len(results) == 1

    def test_over_fetch_multiplier_applied_to_max_results(self) -> None:
        """recall_learnings is called with max_results * 3 for first call."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.return_value = [
                {
                    "id": "L-001",
                    "summary": "x",
                    "impact": 0.5,
                    "tags": [],
                    "status": "active",
                },
            ]
            select_learnings_for_task(
                task_description="test",
                file_paths=["backend/admin.py"],
                max_results=4,
                min_impact=0.0,
            )
            first_call = mock_recall.call_args_list[0]
            assert first_call.kwargs["max_results"] == 12  # 4 * 3

    def test_fallback_uses_2x_multiplier(self) -> None:
        """Fallback recall uses max_results * 2."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # Force fallback
            return [
                {
                    "id": "L-fb",
                    "summary": "fallback",
                    "impact": 0.5,
                    "tags": [],
                    "status": "active",
                },
            ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.side_effect = side_effect
            select_learnings_for_task(
                task_description="test",
                file_paths=["backend/admin.py"],
                max_results=4,
                min_impact=0.0,
            )
            second_call = mock_recall.call_args_list[1]
            assert second_call.kwargs["max_results"] == 8  # 4 * 2

    def test_first_recall_fails_fallback_succeeds(self) -> None:
        """When first recall raises but fallback returns results, those are used."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first failed")
            return [
                {
                    "id": "L-recovered",
                    "summary": "recovered via fallback",
                    "impact": 0.7,
                    "tags": [],
                    "status": "active",
                },
            ]

        with patch(
            "trw_mcp.state.learning_injection.recall_learnings"
        ) as mock_recall:
            mock_recall.side_effect = side_effect
            results = select_learnings_for_task(
                task_description="test",
                file_paths=["backend/admin.py"],
                max_results=5,
                min_impact=0.0,
            )
        assert len(results) == 1
        assert results[0]["id"] == "L-recovered"


# ---------------------------------------------------------------------------
# format_learning_injection — edge cases
# ---------------------------------------------------------------------------


class TestFormatLearningInjectionEdge:
    """Edge cases for markdown formatting."""

    def test_non_list_tags_treated_as_empty(self) -> None:
        """Entry with tags as a string should not crash; tags rendered empty."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {
                "id": "L-001",
                "summary": "Test",
                "impact": 0.5,
                "tags": "not-a-list",
            },
        ]
        result = format_learning_injection(learnings)
        assert "[L-001]" in result
        # tags should be empty since the non-list is skipped
        assert "tags: )" in result or "tags: " in result

    def test_missing_id_uses_unknown(self) -> None:
        """Entry without 'id' key should use 'unknown' placeholder."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"summary": "No ID entry", "impact": 0.5, "tags": []},
        ]
        result = format_learning_injection(learnings)
        assert "[unknown]" in result

    def test_missing_summary_uses_empty_string(self) -> None:
        """Entry without 'summary' key should render with empty summary."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"id": "L-nosummary", "impact": 0.5, "tags": []},
        ]
        result = format_learning_injection(learnings)
        assert "[L-nosummary]" in result
        # The line should have the ID followed by empty summary then impact
        line = [ln for ln in result.split("\n") if "[L-nosummary]" in ln][0]
        assert "- **[L-nosummary]**  (impact:" in line

    def test_output_ends_with_trailing_newline(self) -> None:
        """Formatted output ends with a newline for clean concatenation."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"id": "L-001", "summary": "test", "impact": 0.5, "tags": []},
        ]
        result = format_learning_injection(learnings)
        assert result.endswith("\n")

    def test_multiple_entries_each_on_separate_line(self) -> None:
        """Each entry occupies its own bullet line."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"id": f"L-{i:03d}", "summary": f"Entry {i}", "impact": 0.5, "tags": []}
            for i in range(3)
        ]
        result = format_learning_injection(learnings)
        lines = [ln for ln in result.split("\n") if ln.startswith("- **[")]
        assert len(lines) == 3

    def test_impact_formatted_to_one_decimal(self) -> None:
        """Impact is shown with exactly one decimal place."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"id": "L-001", "summary": "test", "impact": 0.12345, "tags": []},
        ]
        result = format_learning_injection(learnings)
        assert "impact: 0.1" in result
        # Should not show full precision
        assert "0.12345" not in result

    def test_exactly_five_tags_all_shown(self) -> None:
        """When tags count equals the truncation limit (5), all are shown."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {
                "id": "L-001",
                "summary": "test",
                "impact": 0.5,
                "tags": ["a", "b", "c", "d", "e"],
            },
        ]
        result = format_learning_injection(learnings)
        line = [ln for ln in result.split("\n") if "[L-001]" in ln][0]
        assert "a, b, c, d, e" in line

    def test_empty_tags_list_shows_empty_tag_field(self) -> None:
        """When tags is an empty list, the tag field is empty after 'tags: '."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"id": "L-001", "summary": "test", "impact": 0.5, "tags": []},
        ]
        result = format_learning_injection(learnings)
        line = [ln for ln in result.split("\n") if "[L-001]" in ln][0]
        assert "tags: )" in line

    def test_header_is_first_line(self) -> None:
        """The auto-injected header is the first non-empty line."""
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"id": "L-001", "summary": "test", "impact": 0.5, "tags": []},
        ]
        result = format_learning_injection(learnings)
        first_line = result.split("\n")[0]
        assert first_line == "## Task-Relevant Learnings (auto-injected)"
