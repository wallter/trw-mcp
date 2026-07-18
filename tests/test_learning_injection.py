"""Tests for learning-injection domain inference (PRD-CORE-075).

Covers:
  FR-4: infer_domain_tags — domain tag extraction from file paths
"""

from __future__ import annotations


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
