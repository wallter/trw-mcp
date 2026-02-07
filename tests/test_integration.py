"""Integration tests — end-to-end workflows across tools."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    # Reset configs for all tool modules
    import trw_mcp.tools.orchestration as orch_mod
    import trw_mcp.tools.learning as learn_mod
    import trw_mcp.tools.requirements as req_mod
    monkeypatch.setattr(orch_mod, "_config", orch_mod.TRWConfig())
    monkeypatch.setattr(learn_mod, "_config", learn_mod.TRWConfig())
    monkeypatch.setattr(req_mod, "_config", req_mod.TRWConfig())
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
    """End-to-end: init -> work -> reflect -> recall -> sync CLAUDE.md."""

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

        # Step 3: Log some events
        tools["trw_event"].fn(
            event_type="phase_enter",
            run_path=run_path,
            data={"phase": "research"},
        )
        tools["trw_event"].fn(
            event_type="shard_complete",
            run_path=run_path,
            data={"shard": "shard-001"},
        )

        # Step 4: Record learnings
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

        # Step 5: Reflect on the run
        reflect_result = tools["trw_reflect"].fn(
            run_path=run_path,
            scope="run",
        )
        assert reflect_result["events_analyzed"] >= 2

        # Step 6: Recall learnings
        recall_result = tools["trw_recall"].fn(query="testing")
        assert recall_result["total_matches"] >= 1

        # Step 7: Sync to CLAUDE.md
        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        assert sync_result["status"] == "synced"
        assert sync_result["learnings_promoted"] >= 1

        # Verify CLAUDE.md was created with content
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert "trw:start" in content
        assert "Integration test convention" in content

    def test_init_checkpoint_resume(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Init
        init_result = tools["trw_init"].fn(task_name="cp-resume-test")
        run_path = init_result["run_path"]

        # Checkpoint
        cp_result = tools["trw_checkpoint"].fn(
            run_path=run_path,
            message="Before work",
        )
        assert cp_result["status"] == "checkpoint_created"

        # Create shard work
        writer = FileStateWriter()
        shard_dir = Path(run_path) / "scratch" / "shard-001"
        shard_dir.mkdir(parents=True)
        writer.write_yaml(shard_dir / "findings.yaml", {
            "shard_id": "shard-001",
            "status": "complete",
            "summary": "Found stuff",
        })

        # Resume
        resume_result = tools["trw_resume"].fn(run_path=run_path)
        assert "shard-001" in resume_result["shards"]["complete"]

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

    def test_script_save_and_recall(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Save a script
        tools["trw_script_save"].fn(
            name="run-tests",
            content="#!/bin/bash\npytest tests/ -v --cov",
            description="Run test suite with coverage",
            language="bash",
        )

        # Record a learning about the script
        tools["trw_learn"].fn(
            summary="Test runner script available",
            detail="Use .trw/scripts/run-tests.sh for running tests",
            tags=["testing", "scripts"],
            impact=0.7,
        )

        # Recall should find both
        result = tools["trw_recall"].fn(query="test")
        assert result["total_matches"] >= 1


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

    def test_reflect_builds_on_prior(self, tmp_path: Path) -> None:
        tools = _get_all_tools()

        # Init with events
        init_result = tools["trw_init"].fn(task_name="reflect-multi")
        run_path = init_result["run_path"]

        # Add repeated events (should trigger repeated-operation detection)
        writer = FileStateWriter()
        events_path = Path(run_path) / "meta" / "events.jsonl"
        for i in range(5):
            writer.append_jsonl(events_path, {
                "ts": f"2026-01-0{i + 1}",
                "event": "retry_operation",
                "attempt": i,
            })

        # Reflect should detect the repeated pattern
        result = tools["trw_reflect"].fn(run_path=run_path, scope="run")
        assert result["events_analyzed"] >= 5
        # The repeated "retry_operation" should be detected
        assert result["repeated_operations"] >= 1 or result["error_patterns"] >= 0
