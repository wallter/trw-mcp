"""Integration tests — end-to-end workflows across tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync
from trw_mcp.state.persistence import FileStateReader


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_all_tools() -> dict[str, Any]:
    """Create server with all tools registered."""
    from fastmcp import FastMCP

    from trw_mcp.tools.ceremony import register_ceremony_tools
    from trw_mcp.tools.learning import register_learning_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools
    from trw_mcp.tools.requirements import register_requirements_tools

    srv = FastMCP("test")
    register_orchestration_tools(srv)
    register_learning_tools(srv)
    register_requirements_tools(srv)
    register_ceremony_tools(srv)
    return get_tools_sync(srv)


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

        # Verify CLAUDE.md was created with auto-generated markers
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        # PRD-CORE-061: learnings are now delivered via trw_session_start
        # recall, not embedded in CLAUDE.md. Verify sync completed
        # successfully without requiring learning content in the output.

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
            assert len(validate_result["sections_found"]) == 9


class TestSessionLifecycle:
    """Full session lifecycle: start -> init -> checkpoint -> deliver."""

    def test_full_session_lifecycle(self, tmp_path: Path) -> None:
        """Complete lifecycle without errors: session_start -> init -> checkpoint -> deliver."""
        tools = _get_all_tools()

        # Step 1: Session start (empty project — no learnings yet)
        start_result = tools["trw_session_start"].fn()
        assert start_result["success"]
        assert start_result["learnings_count"] == 0

        # Step 2: Init run
        init_result = tools["trw_init"].fn(
            task_name="lifecycle-test",
            objective="Full lifecycle integration test",
        )
        assert init_result["status"] == "initialized"
        run_path = init_result["run_path"]

        # Step 3: Checkpoint
        cp_result = tools["trw_checkpoint"].fn(
            run_path=run_path,
            message="mid-lifecycle",
        )
        assert cp_result["status"] == "checkpoint_created"

        # Step 4: Deliver
        deliver_result = tools["trw_deliver"].fn(
            run_path=run_path,
            skip_index_sync=True,
        )
        assert deliver_result["success"]
        assert deliver_result["critical_steps_completed"] >= 3

    def test_fresh_project_session_start(self, tmp_path: Path) -> None:
        """session_start on empty .trw/ returns zero learnings, no active run."""
        tools = _get_all_tools()

        result = tools["trw_session_start"].fn()
        assert result["success"]
        assert result["learnings_count"] == 0
        assert isinstance(result["learnings"], list)
        assert len(result["learnings"]) == 0

        run_info = result["run"]
        assert run_info["active_run"] is None

    def test_learn_then_recall_roundtrip(self, tmp_path: Path) -> None:
        """Write a learning then recall it by keyword."""
        tools = _get_all_tools()

        # Init to bootstrap .trw/
        tools["trw_init"].fn(task_name="roundtrip-test")

        # Learn
        tools["trw_learn"].fn(
            summary="Roundtrip discovery for integration test",
            detail="This learning should be recallable by keyword",
            tags=["roundtrip"],
            impact=0.9,
        )

        # Recall by keyword
        recall_result = tools["trw_recall"].fn(query="roundtrip")
        assert recall_result["total_matches"] >= 1
        summaries = [
            entry["summary"] for entry in recall_result["learnings"] if "roundtrip" in entry["summary"].lower()
        ]
        assert len(summaries) >= 1


class TestMultiSessionSimulation:
    """Simulate multiple sessions to verify knowledge accumulation."""

    def test_knowledge_accumulates(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Session 1: Init and learn
        tools["trw_init"].fn(task_name="multi-session")
        tools["trw_learn"].fn(
            summary="Database connection pooling optimization",
            detail="Use pgbouncer for connection reuse",
            impact=0.8,
        )

        # Session 2: More learning (same .trw/ dir)
        tools["trw_learn"].fn(
            summary="Frontend CSS grid layout techniques",
            detail="Use minmax for responsive columns",
            impact=0.7,
        )

        # Verify accumulation
        recall = tools["trw_recall"].fn(query="*")
        assert recall["total_matches"] >= 2

        # Verify analytics
        reader = FileStateReader()
        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        if reader.exists(analytics_path):
            analytics = reader.read_yaml(analytics_path)
            assert int(analytics.get("total_learnings", 0)) >= 2
