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

    def test_swebench_repo_paths_get_tags(self) -> None:
        """SWE-bench repo roots produce non-empty tag sets after the
        2026-04-27 iter-22 root-cause fix. Closes the relevance-ranker
        collapse documented in
        docs/research/trw-distill/ITER-22-NAIVE-INJECTION-INVESTIGATION-2026-04-27.md.
        """
        from trw_mcp.state.learning_injection import infer_domain_tags

        # iter-22 failure-concentration repos — each must produce tags
        # so the recall ranker has tag-overlap signal.
        assert infer_domain_tags(["sphinx/domains/python.py"]) == {
            "docs",
            "sphinx",
            "documentation",
        }
        assert infer_domain_tags(["pylint/checkers/refactoring.py"]) == {
            "pylint",
            "linting",
            "static-analysis",
        }
        assert infer_domain_tags(["astropy/io/registry/base.py"]) == {
            "astropy",
            "astronomy",
            "scientific",
        }
        # Other SWE-bench Verified repo roots — sample check.
        assert "sympy" in infer_domain_tags(["sympy/core/numbers.py"])
        assert "pytest" in infer_domain_tags(["pytest/_pytest/main.py"])
        assert "matplotlib" in infer_domain_tags(["matplotlib/axes/_base.py"])
        assert "flask" in infer_domain_tags(["flask/app.py"])
        assert "requests" in infer_domain_tags(["requests/sessions.py"])
        assert "scikit-learn" in infer_domain_tags(
            ["scikit-learn/sklearn/cluster/_kmeans.py"],
        )
        # sklearn alias also resolves.
        assert "scikit-learn" in infer_domain_tags(["sklearn/cluster/_kmeans.py"])

    def test_benchmark_corpus_repo_paths_get_tags(self) -> None:
        """Additional benchmark-corpus repos (pandas/transformers/mlflow/etc.)
        also resolve to non-empty tags so downstream eval problems benefit
        from the relevance ranker.
        """
        from trw_mcp.state.learning_injection import infer_domain_tags

        assert "pandas" in infer_domain_tags(["pandas/core/frame.py"])
        assert "transformers" in infer_domain_tags(
            ["transformers/models/bert/modeling_bert.py"],
        )
        assert "mlflow" in infer_domain_tags(["mlflow/tracking/client.py"])
        assert "numpy" in infer_domain_tags(["numpy/core/arrayprint.py"])
        # torch and pytorch both resolve to the same tag set.
        assert "pytorch" in infer_domain_tags(["torch/nn/modules/linear.py"])
        assert "pytorch" in infer_domain_tags(["pytorch/torch/optim/sgd.py"])
        assert "fastapi" in infer_domain_tags(["fastapi/routing.py"])


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

    def test_dispatches_through_injection_factory_with_active_status(self) -> None:
        """PRD-FIX-085 FR05: select_learnings_for_task routes through the named
        factory (recall_for_learning_injection), NOT an ad-hoc adapter call.

        Unlike the sibling tests that patch the ``recall_learnings`` shim, this
        test patches the factory itself (``recall_for_learning_injection``) so
        the dispatch branch inside the shim is exercised end-to-end from the
        public entry point. This catches a regression where
        ``select_learnings_for_task`` drops the ``status="active"`` filter (which
        would route to the unfiltered adapter path and surface
        resolved/obsolete learnings) or drifts the resolved min_impact.
        """
        from trw_mcp.models.config import get_config
        from trw_mcp.state.learning_injection import select_learnings_for_task

        cfg = get_config()
        mock_results = [
            {
                "id": "L-active-001",
                "summary": "active learning",
                "impact": 0.9,
                "tags": ["admin"],
                "status": "active",
            },
        ]

        # Patch the FACTORY (not the shim). The shim's active-status branch
        # must invoke this for the injection path; the factory's own pinned
        # status="active" filter is the active semantics this exercises.
        with patch(
            "trw_mcp.state.recall_factories.recall_for_learning_injection",
            return_value=mock_results,
        ) as mock_factory:
            results = select_learnings_for_task(
                task_description="implement admin endpoint",
                file_paths=["backend/routers/admin.py"],
                # max_results / min_impact omitted -> config-resolved defaults.
            )

        # (a) The factory IS reached from the public entry point.
        assert mock_factory.called, "Injection path must dispatch through the named factory"
        assert results and results[0]["id"] == "L-active-001"

        # (b) It receives active semantics + the config-resolved min_impact with
        #     no param drift. The factory pins status="active" internally, so the
        #     shim must route here (the active branch) — proven by the factory
        #     being called at all rather than the adapter. min_impact must equal
        #     the config default (sentinel None resolved), not a hardcoded value.
        first_call = mock_factory.call_args_list[0]
        assert first_call.kwargs["min_impact"] == cfg.agent_learning_min_impact
        # Over-fetch is max * 3 for re-ranking; proves max_results threaded through.
        assert first_call.kwargs["max_results"] == cfg.agent_learning_max * 3
        # The task description is forwarded as the positional query arg.
        assert first_call.args[1] == "implement admin endpoint"
        # Inferred domain tags (admin file path) reach the factory.
        assert "admin" in (first_call.kwargs["tags"] or [])

    def test_drops_to_query_only_fallback_still_via_factory(self) -> None:
        """When the tag-filtered factory call returns empty, the query-only
        fallback ALSO routes through the factory with status='active' preserved.

        Guards against a regression where the fallback branch bypasses the
        factory (losing the active-status filter) on the second attempt.
        """
        from trw_mcp.models.config import get_config
        from trw_mcp.state.learning_injection import select_learnings_for_task

        cfg = get_config()
        call_count = 0

        def factory_side_effect(*args: object, **kwargs: object) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return []  # tag-filtered call yields nothing
            return [
                {
                    "id": "L-fallback",
                    "summary": "fallback learning",
                    "impact": 0.7,
                    "tags": [],
                    "status": "active",
                },
            ]

        with patch(
            "trw_mcp.state.recall_factories.recall_for_learning_injection",
            side_effect=factory_side_effect,
        ) as mock_factory:
            results = select_learnings_for_task(
                task_description="some task",
                file_paths=["backend/routers/admin.py"],
            )

        assert call_count == 2, "Both the tag-filtered and fallback calls go via the factory"
        assert results and results[0]["id"] == "L-fallback"
        # Fallback call (second) must NOT pass tags but must keep the resolved
        # min_impact — proving no param drift on the fallback branch either.
        fallback_call = mock_factory.call_args_list[1]
        assert fallback_call.kwargs.get("tags") is None
        assert fallback_call.kwargs["min_impact"] == cfg.agent_learning_min_impact
        assert fallback_call.kwargs["max_results"] == cfg.agent_learning_max * 2


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
