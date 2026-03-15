"""Tests for context-aware learning injection (PRD-CORE-075).

Covers:
  FR-1: select_learnings_for_task — recall-based selection with tag ranking
  FR-2: format_learning_injection — markdown prompt section formatting
  FR-4: infer_domain_tags — domain tag extraction from file paths
  FR-5: Configuration integration via TRWConfig
"""

from __future__ import annotations

from unittest.mock import patch


# ---------------------------------------------------------------------------
# FR-4: infer_domain_tags
# ---------------------------------------------------------------------------
class TestInferDomainTags:
    """FR-4: Domain tag inference from file paths."""

    def test_backend_router_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["backend/routers/admin.py"])
        assert "admin" in tags
        assert "backend" in tags
        assert "auth" in tags
        # routers should map to api/endpoints
        assert "api" in tags

    def test_platform_component_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["platform/src/components/ui/"])
        assert "frontend" in tags
        assert "ui" in tags
        assert "components" in tags

    def test_mcp_state_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["trw-mcp/src/trw_mcp/state/"])
        assert "mcp" in tags
        assert "state" in tags

    def test_multiple_paths_merge_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(
            [
                "backend/routers/admin.py",
                "platform/src/components/dashboard/",
            ]
        )
        assert "backend" in tags
        assert "frontend" in tags
        assert "dashboard" in tags

    def test_empty_paths_returns_empty_set(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags([])
        assert len(tags) == 0

    def test_windows_path_separators(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["backend\\routers\\admin.py"])
        assert "admin" in tags
        assert "backend" in tags

    def test_database_model_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["backend/models/database.py"])
        assert "database" in tags
        assert "orm" in tags
        assert "models" in tags

    def test_memory_package_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["trw-memory/src/trw_memory/retrieval/"])
        assert "memory" in tags
        assert "retrieval" in tags

    def test_security_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["backend/services/security.py"])
        assert "security" in tags
        assert "auth" in tags

    def test_skills_agents_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["trw-mcp/src/trw_mcp/data/skills/trw-sprint-team/"])
        assert "skills" in tags

    def test_test_directory_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["trw-mcp/tests/test_tools_learning.py"])
        assert "testing" in tags

    def test_config_tags(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["trw-mcp/src/trw_mcp/models/config/_main.py"])
        assert "config" in tags
        assert "settings" in tags


# ---------------------------------------------------------------------------
# FR-1: select_learnings_for_task
# ---------------------------------------------------------------------------
class TestSelectLearningsForTask:
    """FR-1: Recall-based learning selection with tag-overlap ranking."""

    def test_returns_relevant_learnings_ranked_by_tag_overlap(self) -> None:
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-001",
                "summary": "Admin role must include owner",
                "impact": 0.9,
                "tags": ["admin", "auth"],
                "status": "active",
            },
            {
                "id": "L-002",
                "summary": "Pydantic v2 enum gotcha",
                "impact": 0.8,
                "tags": ["pydantic", "models"],
                "status": "active",
            },
        ]

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="implement admin endpoint",
                file_paths=["backend/routers/admin.py"],
                max_results=5,
                min_impact=0.5,
            )
            assert len(results) >= 1
            # Admin-tagged learning should rank higher for admin file paths
            assert results[0]["id"] == "L-001"

    def test_respects_max_results(self) -> None:
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": f"L-{i:03d}",
                "summary": f"Learning {i}",
                "impact": 0.7,
                "tags": ["test"],
                "status": "active",
            }
            for i in range(10)
        ]

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test task",
                file_paths=["tests/test_foo.py"],
                max_results=3,
            )
            assert len(results) <= 3

    def test_returns_empty_on_recall_failure(self) -> None:
        from trw_mcp.state.learning_injection import select_learnings_for_task

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.side_effect = RuntimeError("recall failed")
            results = select_learnings_for_task(
                task_description="test task",
                file_paths=["backend/routers/admin.py"],
            )
            assert results == []

    def test_merges_explicit_and_inferred_tags(self) -> None:
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-001",
                "summary": "Custom tag learning",
                "impact": 0.8,
                "tags": ["custom-tag"],
                "status": "active",
            },
        ]

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test task",
                file_paths=["backend/routers/admin.py"],
                tags=["custom-tag"],
            )
            # Should have been called — verify the call had merged tags
            call_args = mock_recall.call_args
            assert call_args is not None
            called_tags = call_args.kwargs.get("tags") or call_args[1].get("tags")
            # Should contain both inferred (admin, auth, etc) and explicit (custom-tag)
            assert "custom-tag" in called_tags

    def test_fallback_query_only_when_no_tag_results(self) -> None:
        """When first recall with tags returns empty, falls back to query-only."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        call_count = 0

        def side_effect(*args: object, **kwargs: object) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # First call with tags returns nothing
            return [
                {
                    "id": "L-fallback",
                    "summary": "Fallback learning",
                    "impact": 0.7,
                    "tags": [],
                    "status": "active",
                },
            ]

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.side_effect = side_effect
            results = select_learnings_for_task(
                task_description="test task",
                file_paths=["backend/routers/admin.py"],
            )
            assert call_count == 2
            assert len(results) == 1
            assert results[0]["id"] == "L-fallback"

    def test_handles_missing_tags_field_in_entries(self) -> None:
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": "L-notags",
                "summary": "No tags learning",
                "impact": 0.6,
                "status": "active",
                # no "tags" key
            },
        ]

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test task",
                file_paths=["backend/routers/admin.py"],
            )
            # Should not crash; should return the entry
            assert len(results) == 1

    def test_uses_config_defaults_when_not_specified(self) -> None:
        """When max_results/min_impact not passed, uses sentinel -> config defaults."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = []
            select_learnings_for_task(
                task_description="test task",
                file_paths=["backend/routers/admin.py"],
            )
            # Should complete without error using defaults
            assert mock_recall.called


# ---------------------------------------------------------------------------
# FR-2: format_learning_injection
# ---------------------------------------------------------------------------
class TestFormatLearningInjection:
    """FR-2: Prompt injection formatting."""

    def test_formats_learnings_as_markdown(self) -> None:
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {
                "id": "L-042",
                "summary": "require_org_admin must accept both admin and owner roles",
                "impact": 0.9,
                "tags": ["auth", "admin"],
            },
            {
                "id": "L-089",
                "summary": "Pydantic v2: use_enum_values=True breaks comparison",
                "impact": 0.8,
                "tags": ["pydantic"],
            },
        ]
        result = format_learning_injection(learnings)
        assert "## Task-Relevant Learnings (auto-injected)" in result
        assert "[L-042]" in result
        assert "[L-089]" in result
        assert "impact: 0.9" in result
        assert "auth, admin" in result

    def test_empty_learnings_returns_empty_string(self) -> None:
        from trw_mcp.state.learning_injection import format_learning_injection

        result = format_learning_injection([])
        assert result == ""

    def test_handles_missing_fields_gracefully(self) -> None:
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [{"id": "L-001"}]
        result = format_learning_injection(learnings)
        assert "[L-001]" in result
        assert "impact: 0.0" in result

    def test_truncates_tag_list_to_five(self) -> None:
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {
                "id": "L-001",
                "summary": "Test",
                "impact": 0.5,
                "tags": ["a", "b", "c", "d", "e", "f", "g"],
            },
        ]
        result = format_learning_injection(learnings)
        # Only first 5 tags shown; f and g should not appear
        entry_line = [l for l in result.split("\n") if "[L-001]" in l][0]
        assert "a, b, c, d, e" in entry_line
        assert ", f" not in entry_line

    def test_includes_high_priority_preamble(self) -> None:
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {"id": "L-001", "summary": "test", "impact": 0.5, "tags": []},
        ]
        result = format_learning_injection(learnings)
        assert "high-priority constraints" in result

    def test_single_learning_format(self) -> None:
        from trw_mcp.state.learning_injection import format_learning_injection

        learnings: list[dict[str, object]] = [
            {
                "id": "L-100",
                "summary": "Always validate input",
                "impact": 1.0,
                "tags": ["validation"],
            },
        ]
        result = format_learning_injection(learnings)
        assert "- **[L-100]** Always validate input (impact: 1.0, tags: validation)" in result


# ---------------------------------------------------------------------------
# FR-5: Configuration integration
# ---------------------------------------------------------------------------
class TestConfigIntegration:
    """FR-5: Configuration fields exist in TRWConfig."""

    def test_config_has_injection_toggle(self) -> None:
        from trw_mcp.models.config import get_config

        config = get_config()
        assert hasattr(config, "agent_learning_injection")
        assert isinstance(config.agent_learning_injection, bool)

    def test_config_has_max_entries(self) -> None:
        from trw_mcp.models.config import get_config

        config = get_config()
        assert hasattr(config, "agent_learning_max")
        assert isinstance(config.agent_learning_max, int)
        assert config.agent_learning_max > 0

    def test_config_has_min_impact(self) -> None:
        from trw_mcp.models.config import get_config

        config = get_config()
        assert hasattr(config, "agent_learning_min_impact")
        assert isinstance(config.agent_learning_min_impact, float)
        assert 0.0 <= config.agent_learning_min_impact <= 1.0

    def test_config_defaults(self) -> None:
        from trw_mcp.models.config import get_config

        config = get_config()
        assert config.agent_learning_injection is True
        assert config.agent_learning_max == 5
        assert config.agent_learning_min_impact == 0.5

    def test_select_uses_config_max_when_sentinel(self) -> None:
        """select_learnings_for_task uses config max_results when None is passed."""
        from trw_mcp.state.learning_injection import select_learnings_for_task

        mock_results = [
            {
                "id": f"L-{i:03d}",
                "summary": f"Learning {i}",
                "impact": 0.7,
                "tags": [],
                "status": "active",
            }
            for i in range(20)
        ]

        with patch("trw_mcp.state.learning_injection.recall_learnings") as mock_recall:
            mock_recall.return_value = mock_results
            results = select_learnings_for_task(
                task_description="test task",
                file_paths=["backend/routers/admin.py"],
                # max_results not passed — should use config default (5)
            )
            # Default config agent_learning_max is 5
            assert len(results) <= 5
