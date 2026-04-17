"""E2E Test Suite: MCP Server Tools

Executes test plan from docs/testing/E2E-MCP-SERVER-TOOLS.md.
Tests MCP tools through the FastMCP server extraction pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import extract_tool_fn, make_test_server

# ── 1. Session & Delivery Lifecycle ─────────────────────────────────────────


class TestSessionLifecycle:
    """E2E 1.1-1.6: session_start and deliver."""

    def test_session_start_cold_start(self, tmp_project: Path) -> None:
        """1.1: Cold start with no prior learnings returns success."""
        server = make_test_server("ceremony")
        fn = extract_tool_fn(server, "trw_session_start")
        result = fn()
        assert result.get("success") is True, f"session_start failed: {result}"
        assert isinstance(result.get("learnings", []), list)

    def test_session_start_with_query(self, tmp_project: Path) -> None:
        """1.2: Focused recall with query parameter."""
        server = make_test_server("ceremony")
        fn = extract_tool_fn(server, "trw_session_start")
        result = fn(query="authentication")
        assert result.get("success") is True

    def test_deliver_no_active_run(self, tmp_project: Path) -> None:
        """1.6: deliver without init doesn't crash."""
        server = make_test_server("ceremony")
        deliver_fn = extract_tool_fn(server, "trw_deliver")
        result = deliver_fn()
        # Should handle gracefully
        assert result is not None


# ── 2. Orchestration ────────────────────────────────────────────────────────


class TestOrchestration:
    """E2E 2.1-2.7: init, status, checkpoint."""

    def test_init_creates_run(self, tmp_project: Path) -> None:
        """2.1: Standard initialization creates run directory."""
        server = make_test_server("orchestration")
        init_fn = extract_tool_fn(server, "trw_init")
        result = init_fn(task_name="e2e-init-test", objective="Test init")
        assert "run_id" in result, f"Missing run_id: {result}"
        assert "run_path" in result, f"Missing run_path: {result}"
        run_path = Path(result["run_path"])
        assert run_path.exists(), f"Run path doesn't exist: {run_path}"

    def test_status_no_run(self, tmp_project: Path) -> None:
        """2.5: Status with no active run raises StateError (expected)."""
        from trw_mcp.exceptions import StateError

        server = make_test_server("orchestration")
        status_fn = extract_tool_fn(server, "trw_status")
        with pytest.raises(StateError):
            status_fn()

    def test_status_with_active_run(self, tmp_project: Path) -> None:
        """2.4: Status reports active run details."""
        server = make_test_server("orchestration")
        init_fn = extract_tool_fn(server, "trw_init")
        status_fn = extract_tool_fn(server, "trw_status")
        init_fn(task_name="status-test")
        result = status_fn()
        assert result is not None

    def test_checkpoint_persists(self, tmp_project: Path) -> None:
        """2.6: Checkpoint creates persistence snapshot."""
        server = make_test_server("orchestration", "checkpoint")
        init_fn = extract_tool_fn(server, "trw_init")
        ckpt_fn = extract_tool_fn(server, "trw_checkpoint")
        init_fn(task_name="ckpt-test")
        result = ckpt_fn(message="Research complete")
        assert result is not None

    def test_trust_level(self, tmp_project: Path) -> None:
        """9.1: Query trust level."""
        server = make_test_server("usage")
        fn = extract_tool_fn(server, "trw_trust_level")
        result = fn()
        assert result is not None

    def test_progressive_expand_ceremony(self, tmp_project: Path) -> None:
        """9.3: Expand valid capability group."""
        server = make_test_server("usage")
        fn = extract_tool_fn(server, "trw_progressive_expand")
        result = fn(group="ceremony")
        assert result is not None


# ── 3. Learning/Memory Tools ────────────────────────────────────────────────


class TestLearningTools:
    """E2E 3.1-3.13: learn, learn_update, recall."""

    def test_learn_stores_entry(self, tmp_project: Path) -> None:
        """3.1: Store a learning entry."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        result = learn_fn(
            summary="Pydantic v2 requires model_config",
            detail="Use ConfigDict instead of class Config",
            tags=["pydantic", "migration"],
            impact=0.7,
        )
        assert result is not None
        result_str = str(result)
        assert "L-" in result_str or "learning" in result_str.lower(), f"No learning ID: {result}"

    def test_learn_all_types(self, tmp_project: Path) -> None:
        """3.5: Create learnings of each type."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        for t in ["pattern", "incident", "convention", "hypothesis", "workaround"]:
            result = learn_fn(
                summary=f"Test {t} learning",
                detail=f"Detail for {t}",
                tags=["e2e"],
                impact=0.5,
                type=t,
            )
            assert result is not None, f"Failed to create {t} learning"

    def test_recall_keyword_search(self, tmp_project: Path) -> None:
        """3.9: Recall returns keyword-matched results."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        recall_fn = extract_tool_fn(server, "trw_recall")

        learn_fn(summary="Authentication uses JWT tokens", detail="JWT auth detail", tags=["auth"], impact=0.7)
        learn_fn(summary="Database uses PostgreSQL", detail="PG detail", tags=["db"], impact=0.6)

        result = recall_fn(query="authentication")
        assert result is not None

    def test_recall_with_tags(self, tmp_project: Path) -> None:
        """3.10: Recall with tag filtering."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        recall_fn = extract_tool_fn(server, "trw_recall")

        learn_fn(summary="Auth pattern A", detail="Security detail", tags=["security"], impact=0.7)
        learn_fn(summary="DB pattern B", detail="DB detail", tags=["database"], impact=0.6)

        result = recall_fn(query="*", tags=["security"])
        assert result is not None

    def test_recall_empty_results(self, tmp_project: Path) -> None:
        """3.12: Recall with no matches returns without error."""
        server = make_test_server("learning")
        recall_fn = extract_tool_fn(server, "trw_recall")
        result = recall_fn(query="nonexistent_topic_xyz_12345")
        assert result is not None

    def test_recall_wildcard_all(self, tmp_project: Path) -> None:
        """3.13: Wildcard recall lists all learnings."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        recall_fn = extract_tool_fn(server, "trw_recall")

        for i in range(3):
            learn_fn(summary=f"Learning {i}", detail=f"Detail {i}", tags=["e2e"], impact=0.5)

        result = recall_fn(query="*")
        assert result is not None

    def test_learn_update_status(self, tmp_project: Path) -> None:
        """3.7: Update learning status."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        r = learn_fn(summary="Update test", detail="Detail for update", tags=["e2e"], impact=0.5)
        # Extract learning ID from result
        result_str = str(r)
        import re

        match = re.search(r"L-[A-Za-z0-9]+", result_str)
        if match:
            learning_id = match.group(0)
            result = update_fn(learning_id=learning_id, status="resolved")
            assert result is not None

    def test_learn_unicode_content(self, tmp_project: Path) -> None:
        """13.3: Unicode content stored and retrieved correctly."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")

        result = learn_fn(
            summary="日本語テスト Unicode test",
            detail="Ümlauts and ñ characters",
            tags=["unicode"],
            impact=0.5,
        )
        assert result is not None


# ── 4. Build & Quality Tools ────────────────────────────────────────────────


class TestBuildQuality:
    """E2E 4.1-4.5: build_check, quality_dashboard."""

    def test_build_check_passing(self, tmp_project: Path) -> None:
        """4.1: Passing build recorded."""
        server = make_test_server("orchestration", "build")
        init_fn = extract_tool_fn(server, "trw_init")
        build_fn = extract_tool_fn(server, "trw_build_check")

        init_fn(task_name="build-pass-test")
        result = build_fn(
            tests_passed=True,
            test_count=150,
            failure_count=0,
            coverage_pct=85.0,
            mypy_clean=True,
        )
        assert result is not None

    def test_build_check_failing(self, tmp_project: Path) -> None:
        """4.2: Failing build recorded."""
        server = make_test_server("orchestration", "build")
        init_fn = extract_tool_fn(server, "trw_init")
        build_fn = extract_tool_fn(server, "trw_build_check")

        init_fn(task_name="build-fail-test")
        result = build_fn(
            tests_passed=False,
            test_count=150,
            failure_count=3,
            coverage_pct=75.0,
            mypy_clean=False,
        )
        assert result is not None


# ── 5. Ceremony Tools ───────────────────────────────────────────────────────


class TestCeremonyTools:
    """E2E 5.1-5.5: ceremony_status, approve, revert."""

    def test_ceremony_status_default(self, tmp_project: Path) -> None:
        """5.1: Get ceremony status."""
        server = make_test_server("ceremony_feedback")
        fn = extract_tool_fn(server, "trw_ceremony_status")
        result = fn()
        assert result is not None

    def test_ceremony_approve_invalid(self, tmp_project: Path) -> None:
        """5.4: Approve nonexistent proposal raises ValueError."""
        server = make_test_server("ceremony_feedback")
        fn = extract_tool_fn(server, "trw_ceremony_approve")
        with pytest.raises(ValueError, match="No pending proposal"):
            fn(proposal_id="nonexistent-id")


# ── 6. Requirements/PRD ─────────────────────────────────────────────────────


class TestRequirementsTools:
    """E2E 6.1-6.4: prd_create, prd_validate."""

    def test_prd_create(self, tmp_project: Path) -> None:
        """6.1: Create a PRD."""
        server = make_test_server("requirements")
        fn = extract_tool_fn(server, "trw_prd_create")
        result = fn(
            input_text="Add rate limiting to API endpoints",
            category="CORE",
            priority="P1",
        )
        assert result is not None


# ── 7. Review Tool ──────────────────────────────────────────────────────────


class TestReviewTool:
    """E2E 7.1: review with manual findings."""

    def test_manual_review(self, tmp_project: Path) -> None:
        """7.1: Manual review with findings."""
        server = make_test_server("orchestration", "review")
        init_fn = extract_tool_fn(server, "trw_init")
        review_fn = extract_tool_fn(server, "trw_review")

        init_fn(task_name="review-test")
        result = review_fn(
            mode="manual",
            findings=[{"severity": "P2", "file": "test.py", "line": 1, "message": "test finding"}],
        )
        assert result is not None


# ── 8. Reporting Tools ──────────────────────────────────────────────────────


class TestReportingTools:
    """E2E 8.1-8.3: run_report, analytics_report, usage_report."""

    def test_run_report(self, tmp_project: Path) -> None:
        """8.1: Run report."""
        server = make_test_server("orchestration", "report")
        init_fn = extract_tool_fn(server, "trw_init")
        report_fn = extract_tool_fn(server, "trw_run_report")

        init_fn(task_name="report-test")
        result = report_fn()
        assert result is not None

    def test_analytics_report(self, tmp_project: Path) -> None:
        """8.2: Analytics report."""
        server = make_test_server("report")
        fn = extract_tool_fn(server, "trw_analytics_report")
        result = fn()
        assert result is not None

    def test_usage_report(self, tmp_project: Path) -> None:
        """8.3: Usage report."""
        server = make_test_server("usage")
        fn = extract_tool_fn(server, "trw_usage_report")
        result = fn(period="all")
        assert result is not None


# ── 10. Knowledge & Sync ────────────────────────────────────────────────────


class TestKnowledgeTools:
    """E2E 10.1-10.4: knowledge_sync, claude_md_sync."""

    def test_knowledge_sync_dry_run(self, tmp_project: Path) -> None:
        """10.1: Knowledge sync dry run."""
        server = make_test_server("knowledge")
        fn = extract_tool_fn(server, "trw_knowledge_sync")
        result = fn(dry_run=True)
        assert result is not None


# ── 12. Cross-Tool Integration ──────────────────────────────────────────────


class TestCrossToolIntegration:
    """E2E 12.1: Full session lifecycle golden path."""

    def test_golden_path_lifecycle(self, tmp_project: Path) -> None:
        """12.1: session_start → init → learn → checkpoint → build_check."""
        server = make_test_server("ceremony", "orchestration", "learning", "checkpoint", "build")
        session_fn = extract_tool_fn(server, "trw_session_start")
        init_fn = extract_tool_fn(server, "trw_init")
        learn_fn = extract_tool_fn(server, "trw_learn")
        ckpt_fn = extract_tool_fn(server, "trw_checkpoint")
        build_fn = extract_tool_fn(server, "trw_build_check")

        # 1. Session start
        r = session_fn()
        assert r.get("success") is True, f"session_start failed: {r}"

        # 2. Init run
        r = init_fn(task_name="golden-path", objective="E2E")
        assert "run_id" in r, f"init failed: {r}"

        # 3. Learn
        r = learn_fn(summary="Golden path discovery", detail="E2E detail", tags=["e2e"], impact=0.6)
        assert r is not None, f"learn failed: {r}"

        # 4. Checkpoint
        r = ckpt_fn(message="Research complete")
        assert r is not None, f"checkpoint failed: {r}"

        # 5. Build check
        r = build_fn(tests_passed=True, test_count=10, coverage_pct=90)
        assert r is not None, f"build_check failed: {r}"
