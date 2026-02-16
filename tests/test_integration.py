"""Integration tests — end-to-end workflows across tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_all_tools() -> dict[str, object]:
    """Create server with all tools registered."""
    from fastmcp import FastMCP
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.requirements import register_requirements_tools

    srv = FastMCP("test")
    register_orchestration_tools(srv)
    register_learning_tools(srv)
    register_requirements_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


class TestFullWorkflow:
    """End-to-end: init -> work -> learn -> recall -> sync CLAUDE.md."""

    def test_init_learn_recall_sync(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Step 1: Init project
        init_result = tools["trw_init"].fn(
            task_name="e2e-test",
            objective="Integration test workflow",
        )
        assert init_result["status"] == "initialized"
        run_path = init_result["run_path"]

        # Step 2: Check status
        status = tools["trw_status"].fn(run_path=run_path)
        assert status["phase"] == "research"
        assert status["status"] == "active"

        # Step 3: Record learnings
        tools["trw_learn"].fn(
            summary="Integration test convention",
            detail="Always use tmp_path fixtures for file operations",
            tags=["testing", "convention"],
            impact=0.85,
        )
        tools["trw_learn"].fn(
            summary="Config override pattern",
            detail="Use monkeypatch.setenv for config testing",
            tags=["testing", "config"],
            impact=0.75,
        )

        # Step 4: Recall learnings
        recall_result = tools["trw_recall"].fn(query="testing")
        assert recall_result["total_matches"] >= 1

        # Step 5: Sync to CLAUDE.md
        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        assert sync_result["status"] == "synced"
        assert sync_result["learnings_promoted"] >= 1

        # Verify CLAUDE.md was created with content
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "Integration test convention" in content

    def test_init_checkpoint(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Init
        init_result = tools["trw_init"].fn(task_name="cp-test")
        run_path = init_result["run_path"]

        # Checkpoint
        cp_result = tools["trw_checkpoint"].fn(
            run_path=run_path,
            message="Before work",
        )
        assert cp_result["status"] == "checkpoint_created"

        # Verify checkpoint file exists
        cp_path = Path(run_path) / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()

    def test_prd_create_then_validate(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Init project first (creates .trw/)
        tools["trw_init"].fn(task_name="prd-test")

        # Create PRD
        create_result = tools["trw_prd_create"].fn(
            input_text="We need a caching layer for API responses to reduce latency",
            category="INFRA",
            priority="P1",
            title="API Response Cache",
        )
        assert create_result["prd_id"] == "PRD-INFRA-001"

        # Validate the created PRD
        if create_result["output_path"]:
            validate_result = tools["trw_prd_validate"].fn(
                prd_path=create_result["output_path"],
            )
            # The auto-generated PRD should have basic structure
            assert len(validate_result["sections_found"]) == 12


class TestMultiSessionSimulation:
    """Simulate multiple sessions to verify knowledge accumulation."""

    def test_knowledge_accumulates(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Session 1: Init and learn
        tools["trw_init"].fn(task_name="multi-session")
        tools["trw_learn"].fn(
            summary="Session 1 learning",
            detail="First session discovery",
            impact=0.8,
        )

        # Session 2: More learning (same .trw/ dir)
        tools["trw_learn"].fn(
            summary="Session 2 learning",
            detail="Second session discovery",
            impact=0.7,
        )

        # Verify accumulation
        recall = tools["trw_recall"].fn(query="session")
        assert recall["total_matches"] >= 2

        # Verify analytics
        reader = FileStateReader()
        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        if reader.exists(analytics_path):
            analytics = reader.read_yaml(analytics_path)
            assert int(analytics.get("total_learnings", 0)) >= 2
