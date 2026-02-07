"""Tests for orchestration tools — init, status, phase_check, wave_validate, resume, checkpoint, event."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    # Force reimport to pick up new env
    import trw_mcp.tools.orchestration as orch_mod
    monkeypatch.setattr(orch_mod, "_config", orch_mod.TRWConfig())
    return tmp_path


@pytest.fixture
def server() -> "FastMCP":
    """Create a fresh FastMCP server with orchestration tools registered."""
    from fastmcp import FastMCP
    from trw_mcp.tools.orchestration import register_orchestration_tools

    srv = FastMCP("test-trw")
    register_orchestration_tools(srv)
    return srv


class TestTrwInit:
    """Tests for trw_init tool."""

    def test_creates_trw_dir(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)

        # Get the tool function directly
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        init_tool = tools["trw_init"]

        result = init_tool.fn(task_name="test-task", objective="Test objective")

        assert "run_id" in result
        assert "run_path" in result
        assert result["status"] == "initialized"

        # Check .trw/ structure
        trw_dir = tmp_path / ".trw"
        assert trw_dir.exists()
        assert (trw_dir / "config.yaml").exists()
        assert (trw_dir / "learnings" / "entries").exists()
        assert (trw_dir / "reflections").exists()
        assert (trw_dir / "scripts").exists()
        assert (trw_dir / "patterns").exists()
        assert (trw_dir / "context").exists()
        assert (trw_dir / ".gitignore").exists()

    def test_creates_run_dirs(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        result = tools["trw_init"].fn(task_name="my-task")

        run_path = Path(result["run_path"])
        assert (run_path / "meta" / "run.yaml").exists()
        assert (run_path / "meta" / "events.jsonl").exists()
        assert (run_path / "reports").exists()
        assert (run_path / "artifacts").exists()
        assert (run_path / "scratch").exists()
        assert (run_path / "shards").exists()
        assert (run_path / "validation").exists()

    def test_run_yaml_content(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        result = tools["trw_init"].fn(task_name="check-task")

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["task"] == "check-task"
        assert run_yaml["framework"] == "v17.1_TRW"
        assert run_yaml["status"] == "active"
        assert run_yaml["phase"] == "research"


class TestTrwStatus:
    """Tests for trw_status tool."""

    def test_reads_run_state(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # Init first
        init_result = tools["trw_init"].fn(task_name="status-task")
        run_path = init_result["run_path"]

        # Then check status
        status = tools["trw_status"].fn(run_path=run_path)
        assert status["task"] == "status-task"
        assert status["phase"] == "research"
        assert status["status"] == "active"
        assert status["event_count"] >= 1


class TestTrwPhaseCheck:
    """Tests for trw_phase_check tool."""

    def test_valid_phase(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="phase-task")

        result = tools["trw_phase_check"].fn(
            phase_name="research",
            run_path=init_result["run_path"],
        )
        assert "valid" in result
        assert result["phase"] == "research"

    def test_invalid_phase(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from trw_mcp.exceptions import ValidationError
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="phase-task2")

        with pytest.raises(ValidationError, match="Invalid phase"):
            tools["trw_phase_check"].fn(
                phase_name="nonexistent",
                run_path=init_result["run_path"],
            )


class TestTrwCheckpoint:
    """Tests for trw_checkpoint tool."""

    def test_creates_checkpoint(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="cp-task")

        result = tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="Test checkpoint",
        )
        assert result["status"] == "checkpoint_created"
        assert result["message"] == "Test checkpoint"

        # Verify checkpoint file exists
        cp_path = Path(init_result["run_path"]) / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()


class TestTrwEvent:
    """Tests for trw_event tool."""

    def test_logs_event(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="event-task")

        result = tools["trw_event"].fn(
            event_type="custom_event",
            run_path=init_result["run_path"],
            data={"custom_key": "custom_value"},
        )
        assert result["status"] == "event_logged"
        assert result["event_type"] == "custom_event"

        # Verify event in log
        reader = FileStateReader()
        events = reader.read_jsonl(Path(init_result["run_path"]) / "meta" / "events.jsonl")
        custom_events = [e for e in events if e.get("event") == "custom_event"]
        assert len(custom_events) == 1
        assert custom_events[0]["custom_key"] == "custom_value"


class TestTrwWaveValidate:
    """Tests for trw_wave_validate tool."""

    def test_no_manifest(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="wave-task")

        result = tools["trw_wave_validate"].fn(
            wave_number=1,
            run_path=init_result["run_path"],
        )
        assert result["valid"] is False
        assert "No wave_manifest" in result["error"]

    def test_with_manifest(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="wave-task2")
        run_path = Path(init_result["run_path"])

        # Create wave manifest
        writer = FileStateWriter()
        writer.write_yaml(run_path / "meta" / "wave_manifest.yaml", {
            "waves": [
                {"wave": 1, "shards": ["shard-001"], "status": "active"},
            ],
        })

        # Create shard manifest
        writer.write_yaml(run_path / "shards" / "manifest.yaml", {
            "shards": [
                {
                    "id": "shard-001",
                    "title": "Test shard",
                    "wave": 1,
                    "status": "complete",
                    "output_contract": {
                        "file": "scratch/shard-001/result.yaml",
                        "keys": ["summary"],
                        "required": True,
                    },
                },
            ],
        })

        # Create output file
        result_dir = run_path / "scratch" / "shard-001"
        result_dir.mkdir(parents=True, exist_ok=True)
        writer.write_yaml(result_dir / "result.yaml", {"summary": "done"})

        result = tools["trw_wave_validate"].fn(
            wave_number=1,
            run_path=str(run_path),
        )
        assert result["valid"] is True
        assert result["shards_checked"] == 1


class TestFrameworkDeployment:
    """Tests for framework deployment to .trw/ (PRD-CORE-002 Phase 2)."""

    def test_init_creates_frameworks_dir(self, tmp_path: Path) -> None:
        """trw_init creates .trw/frameworks/ directory."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        tools["trw_init"].fn(task_name="fw-deploy-task")

        assert (tmp_path / ".trw" / "frameworks").exists()

    def test_init_deploys_framework_md(self, tmp_path: Path) -> None:
        """FRAMEWORK.md deployed to .trw/frameworks/ with content."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        tools["trw_init"].fn(task_name="fw-content-task")

        fw_path = tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert fw_path.exists()
        content = fw_path.read_text(encoding="utf-8")
        assert len(content) > 100  # Non-trivial content

    def test_init_deploys_aaref_md(self, tmp_path: Path) -> None:
        """AARE-F-FRAMEWORK.md deployed to .trw/frameworks/."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        tools["trw_init"].fn(task_name="aaref-deploy-task")

        aaref_path = tmp_path / ".trw" / "frameworks" / "AARE-F-FRAMEWORK.md"
        assert aaref_path.exists()
        content = aaref_path.read_text(encoding="utf-8")
        assert len(content) > 100

    def test_init_creates_version_yaml(self, tmp_path: Path) -> None:
        """VERSION.yaml created with expected fields."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        tools["trw_init"].fn(task_name="version-task")

        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        assert version_path.exists()

        reader = FileStateReader()
        data = reader.read_yaml(version_path)
        assert "framework_version" in data
        assert "aaref_version" in data
        assert "trw_mcp_version" in data
        assert "deployed_at" in data
        assert data["framework_version"] == "v17.1_TRW"

    def test_init_deploys_claude_md_template(self, tmp_path: Path) -> None:
        """.trw/templates/claude_md.md exists after init."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}
        tools["trw_init"].fn(task_name="template-deploy-task")

        template_path = tmp_path / ".trw" / "templates" / "claude_md.md"
        assert template_path.exists()
        content = template_path.read_text(encoding="utf-8")
        assert "{{categorized_learnings}}" in content

    def test_repeated_init_preserves_custom_template(self, tmp_path: Path) -> None:
        """Custom template not overwritten by re-init."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # First init — deploys bundled template
        tools["trw_init"].fn(task_name="preserve-task1")

        # Replace with custom template
        template_path = tmp_path / ".trw" / "templates" / "claude_md.md"
        custom_content = "## My Custom Template\n{{categorized_learnings}}"
        template_path.write_text(custom_content, encoding="utf-8")

        # Second init — should NOT overwrite
        tools["trw_init"].fn(task_name="preserve-task2")

        content = template_path.read_text(encoding="utf-8")
        assert content == custom_content


class TestVersionTracking:
    """Tests for framework version tracking (PRD-CORE-002 Phase 3)."""

    def test_same_version_skips_rewrite(self, tmp_path: Path) -> None:
        """No file modification when versions match."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # First init
        tools["trw_init"].fn(task_name="skip-rewrite-task1")

        fw_path = tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md"
        mtime_after_first = fw_path.stat().st_mtime

        # Touch the file to verify it's NOT rewritten
        import time
        time.sleep(0.05)

        # Second init — should skip rewrite
        tools["trw_init"].fn(task_name="skip-rewrite-task2")

        mtime_after_second = fw_path.stat().st_mtime
        assert mtime_after_first == mtime_after_second

    def test_version_mismatch_triggers_upgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Change config version, re-init, verify files updated."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        import trw_mcp.tools.orchestration as orch_mod
        from trw_mcp.models.config import TRWConfig
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # First init with default version
        tools["trw_init"].fn(task_name="upgrade-task1")

        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        reader = FileStateReader()
        data = reader.read_yaml(version_path)
        assert data["framework_version"] == "v17.1_TRW"

        # Change framework version in config
        new_config = TRWConfig(framework_version="v18.0_TRW")
        monkeypatch.setattr(orch_mod, "_config", new_config)

        # Re-init with new version
        srv2 = FastMCP("test2")
        register_orchestration_tools(srv2)
        tools2 = {t.name: t for t in srv2._tool_manager._tools.values()}
        tools2["trw_init"].fn(task_name="upgrade-task2")

        # Verify version was updated
        updated = reader.read_yaml(version_path)
        assert updated["framework_version"] == "v18.0_TRW"

    def test_upgrade_logs_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Upgrade event appears in events log."""
        from trw_mcp.tools.orchestration import register_orchestration_tools
        import trw_mcp.tools.orchestration as orch_mod
        from trw_mcp.models.config import TRWConfig
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # First init
        tools["trw_init"].fn(task_name="event-task1")

        # Change version
        new_config = TRWConfig(framework_version="v19.0_TRW")
        monkeypatch.setattr(orch_mod, "_config", new_config)

        # Second init triggers upgrade
        srv2 = FastMCP("test2")
        register_orchestration_tools(srv2)
        tools2 = {t.name: t for t in srv2._tool_manager._tools.values()}
        tools2["trw_init"].fn(task_name="event-task2")

        # Check upgrade event was logged
        events_path = tmp_path / ".trw" / "upgrade_events.jsonl"
        assert events_path.exists()
        reader = FileStateReader()
        events = reader.read_jsonl(events_path)
        upgrade_events = [e for e in events if e.get("event") == "framework_upgrade"]
        assert len(upgrade_events) >= 1
        assert upgrade_events[0]["old_framework"] == "v17.1_TRW"
        assert upgrade_events[0]["new_framework"] == "v19.0_TRW"


class TestTrwAutoDetect:
    """Tests for auto-detection of run path."""

    def test_auto_detects_latest_run(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        # Create a run
        init_result = tools["trw_init"].fn(task_name="auto-task")

        # Status without explicit path should auto-detect
        status = tools["trw_status"].fn()
        assert status["task"] == "auto-task"


class TestTrwResume:
    """Tests for trw_resume tool."""

    def test_empty_run(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="resume-task")

        result = tools["trw_resume"].fn(run_path=init_result["run_path"])
        assert "recovery_plan" in result
        assert "shards" in result

    def test_with_shard_findings(self, tmp_path: Path) -> None:
        from trw_mcp.tools.orchestration import register_orchestration_tools
        from fastmcp import FastMCP

        srv = FastMCP("test")
        register_orchestration_tools(srv)
        tools = {t.name: t for t in srv._tool_manager._tools.values()}

        init_result = tools["trw_init"].fn(task_name="resume-task2")
        run_path = Path(init_result["run_path"])

        # Create shard findings
        writer = FileStateWriter()
        shard_dir = run_path / "scratch" / "shard-001"
        shard_dir.mkdir(parents=True)
        writer.write_yaml(shard_dir / "findings.yaml", {
            "shard_id": "shard-001",
            "status": "complete",
            "summary": "Done",
        })
        shard_dir2 = run_path / "scratch" / "shard-002"
        shard_dir2.mkdir(parents=True)
        writer.write_yaml(shard_dir2 / "findings.yaml", {
            "shard_id": "shard-002",
            "status": "failed",
            "summary": "Error",
        })

        result = tools["trw_resume"].fn(run_path=str(run_path))
        assert "shard-001" in result["shards"]["complete"]
        assert "shard-002" in result["shards"]["failed"]
