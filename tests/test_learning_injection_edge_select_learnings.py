"""Edge-case tests for learning_injection selection and ranking."""

from __future__ import annotations

from unittest.mock import patch

import pytest


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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.side_effect = RuntimeError("down")
            results = select_learnings_for_task(
                task_description="test",
                file_paths=["backend/routers/admin.py"],
                max_results=5,
                min_impact=0.0,
            )
        assert results == []
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = []
            select_learnings_for_task(
                task_description="test",
                file_paths=["backend/routers/admin.py"],
                max_results=5,
                min_impact=0.9,
            )
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test",
                file_paths=["unknown/path/file.xyz"],
                max_results=10,
                min_impact=0.0,
            )
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="admin endpoint",
                file_paths=["backend/routers/admin.py"],
                max_results=10,
                min_impact=0.0,
            )
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
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
            assert first_call.kwargs["max_results"] == 12

    def test_fallback_uses_2x_multiplier(self) -> None:
        """Fallback recall uses max_results * 2."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []
            return [
                {
                    "id": "L-fb",
                    "summary": "fallback",
                    "impact": 0.5,
                    "tags": [],
                    "status": "active",
                },
            ]

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.side_effect = side_effect
            select_learnings_for_task(
                task_description="test",
                file_paths=["backend/admin.py"],
                max_results=4,
                min_impact=0.0,
            )
            second_call = mock_recall.call_args_list[1]
            assert second_call.kwargs["max_results"] == 8

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

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.side_effect = side_effect
            results = select_learnings_for_task(
                task_description="test",
                file_paths=["backend/admin.py"],
                max_results=5,
                min_impact=0.0,
            )
        assert len(results) == 1
        assert results[0]["id"] == "L-recovered"
