"""Edge-case tests for learning_injection formatting."""

from __future__ import annotations


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
            {"id": f"L-{i:03d}", "summary": f"Entry {i}", "impact": 0.5, "tags": []} for i in range(3)
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
