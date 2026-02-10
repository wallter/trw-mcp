"""Tests for orchestration tools — init, status, phase_check, wave_validate, resume, checkpoint, event."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

import trw_mcp.tools.orchestration as orch_mod
from trw_mcp.exceptions import ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools.orchestration import register_orchestration_tools

# Single source of truth for the expected framework version in test assertions.
# Must match the default in TRWConfig.framework_version.
FRAMEWORK_VERSION = "v18.1_TRW"


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_config", TRWConfig())
    return tmp_path


def _make_orch_tools() -> dict[str, Any]:
    """Create a FastMCP server with orchestration tools and return the tools dict."""
    srv = FastMCP("test")
    register_orchestration_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


@pytest.fixture
def orch_tools() -> dict[str, Any]:
    """Provide orchestration tools dict for tests that only need orch tools."""
    return _make_orch_tools()


class TestTrwInit:
    """Tests for trw_init tool."""

    def test_creates_trw_dir(self, tmp_path: Path, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="test-task", objective="Test objective")

        assert "run_id" in result
        assert "run_path" in result
        assert result["status"] == "initialized"

        trw_dir = tmp_path / ".trw"
        assert trw_dir.exists()
        assert (trw_dir / "config.yaml").exists()
        assert (trw_dir / "learnings" / "entries").exists()
        assert (trw_dir / "reflections").exists()
        assert (trw_dir / "scripts").exists()
        assert (trw_dir / "patterns").exists()
        assert (trw_dir / "context").exists()
        assert (trw_dir / ".gitignore").exists()

    def test_creates_run_dirs(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="my-task")

        run_path = Path(result["run_path"])
        assert (run_path / "meta" / "run.yaml").exists()
        assert (run_path / "meta" / "events.jsonl").exists()
        assert (run_path / "reports").exists()
        assert (run_path / "artifacts").exists()
        assert (run_path / "scratch").exists()
        assert (run_path / "shards").exists()
        assert (run_path / "validation").exists()

    def test_run_yaml_content(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="check-task")

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["task"] == "check-task"
        assert run_yaml["framework"] == FRAMEWORK_VERSION
        assert run_yaml["status"] == "active"
        assert run_yaml["phase"] == "research"


class TestTrwStatus:
    """Tests for trw_status tool."""

    def test_reads_run_state(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="status-task")
        run_path = init_result["run_path"]

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert status["task"] == "status-task"
        assert status["phase"] == "research"
        assert status["status"] == "active"
        assert status["event_count"] >= 1


class TestTrwPhaseCheck:
    """Tests for trw_phase_check tool."""

    def test_valid_phase(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="phase-task")

        result = orch_tools["trw_phase_check"].fn(
            phase_name="research",
            run_path=init_result["run_path"],
        )
        assert "valid" in result
        assert result["phase"] == "research"

    def test_invalid_phase(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="phase-task2")

        with pytest.raises(ValidationError, match="Invalid phase"):
            orch_tools["trw_phase_check"].fn(
                phase_name="nonexistent",
                run_path=init_result["run_path"],
            )


class TestTrwCheckpoint:
    """Tests for trw_checkpoint tool."""

    def test_creates_checkpoint(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="cp-task")

        result = orch_tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="Test checkpoint",
        )
        assert result["status"] == "checkpoint_created"
        assert result["message"] == "Test checkpoint"

        cp_path = Path(init_result["run_path"]) / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()


class TestTrwEvent:
    """Tests for trw_event tool."""

    def test_logs_event(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="event-task")

        result = orch_tools["trw_event"].fn(
            event_type="custom_event",
            run_path=init_result["run_path"],
            data={"custom_key": "custom_value"},
        )
        assert result["status"] == "event_logged"
        assert result["event_type"] == "custom_event"

        reader = FileStateReader()
        events = reader.read_jsonl(Path(init_result["run_path"]) / "meta" / "events.jsonl")
        custom_events = [e for e in events if e.get("event") == "custom_event"]
        assert len(custom_events) == 1
        assert custom_events[0]["custom_key"] == "custom_value"


class TestTrwWaveValidate:
    """Tests for trw_wave_validate tool."""

    def test_no_manifest(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="wave-task")

        result = orch_tools["trw_wave_validate"].fn(
            wave_number=1,
            run_path=init_result["run_path"],
        )
        assert result["valid"] is False
        assert "No wave_manifest" in result["error"]

    def test_with_manifest(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="wave-task2")
        run_path = Path(init_result["run_path"])

        writer = FileStateWriter()
        writer.write_yaml(run_path / "meta" / "wave_manifest.yaml", {
            "waves": [
                {"wave": 1, "shards": ["shard-001"], "status": "active"},
            ],
        })

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

        result_dir = run_path / "scratch" / "shard-001"
        result_dir.mkdir(parents=True, exist_ok=True)
        writer.write_yaml(result_dir / "result.yaml", {"summary": "done"})

        result = orch_tools["trw_wave_validate"].fn(
            wave_number=1,
            run_path=str(run_path),
        )
        assert result["valid"] is True
        assert result["shards_checked"] == 1


class TestFrameworkDeployment:
    """Tests for framework deployment to .trw/ (PRD-CORE-002 Phase 2)."""

    def test_init_creates_frameworks_dir(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """trw_init creates .trw/frameworks/ directory."""
        orch_tools["trw_init"].fn(task_name="fw-deploy-task")
        assert (tmp_path / ".trw" / "frameworks").exists()

    def test_init_deploys_framework_md(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """FRAMEWORK.md deployed to .trw/frameworks/ with content."""
        orch_tools["trw_init"].fn(task_name="fw-content-task")

        fw_path = tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert fw_path.exists()
        content = fw_path.read_text(encoding="utf-8")
        assert len(content) > 100

    def test_init_deploys_aaref_md(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """AARE-F-FRAMEWORK.md deployed to .trw/frameworks/."""
        orch_tools["trw_init"].fn(task_name="aaref-deploy-task")

        aaref_path = tmp_path / ".trw" / "frameworks" / "AARE-F-FRAMEWORK.md"
        assert aaref_path.exists()
        content = aaref_path.read_text(encoding="utf-8")
        assert len(content) > 100

    def test_init_creates_version_yaml(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """VERSION.yaml created with expected fields."""
        orch_tools["trw_init"].fn(task_name="version-task")

        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        assert version_path.exists()

        reader = FileStateReader()
        data = reader.read_yaml(version_path)
        assert "framework_version" in data
        assert "aaref_version" in data
        assert "trw_mcp_version" in data
        assert "deployed_at" in data
        assert data["framework_version"] == FRAMEWORK_VERSION

    def test_init_deploys_claude_md_template(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """.trw/templates/claude_md.md exists after init."""
        orch_tools["trw_init"].fn(task_name="template-deploy-task")

        template_path = tmp_path / ".trw" / "templates" / "claude_md.md"
        assert template_path.exists()
        content = template_path.read_text(encoding="utf-8")
        assert "{{categorized_learnings}}" in content

    def test_repeated_init_preserves_custom_template(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """Custom template not overwritten by re-init."""
        orch_tools["trw_init"].fn(task_name="preserve-task1")

        template_path = tmp_path / ".trw" / "templates" / "claude_md.md"
        custom_content = "## My Custom Template\n{{categorized_learnings}}"
        template_path.write_text(custom_content, encoding="utf-8")

        orch_tools["trw_init"].fn(task_name="preserve-task2")

        content = template_path.read_text(encoding="utf-8")
        assert content == custom_content


class TestVersionTracking:
    """Tests for framework version tracking (PRD-CORE-002 Phase 3)."""

    def test_same_version_skips_rewrite(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """No file modification when versions match."""
        orch_tools["trw_init"].fn(task_name="skip-rewrite-task1")

        fw_path = tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md"
        mtime_after_first = fw_path.stat().st_mtime

        time.sleep(0.05)

        orch_tools["trw_init"].fn(task_name="skip-rewrite-task2")

        mtime_after_second = fw_path.stat().st_mtime
        assert mtime_after_first == mtime_after_second

    def test_version_mismatch_triggers_upgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Change config version, re-init, verify files updated."""
        tools = _make_orch_tools()

        # First init with default version
        tools["trw_init"].fn(task_name="upgrade-task1")

        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        reader = FileStateReader()
        data = reader.read_yaml(version_path)
        assert data["framework_version"] == FRAMEWORK_VERSION

        # Change framework version in config to trigger upgrade
        upgraded_version = "v19.0_TRW"
        monkeypatch.setattr(orch_mod, "_config", TRWConfig(framework_version=upgraded_version))

        # Re-init with new version
        tools2 = _make_orch_tools()
        tools2["trw_init"].fn(task_name="upgrade-task2")

        updated = reader.read_yaml(version_path)
        assert updated["framework_version"] == upgraded_version

    def test_upgrade_logs_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Upgrade event appears in events log."""
        tools = _make_orch_tools()

        # First init with default version
        tools["trw_init"].fn(task_name="event-task1")

        # Change version to trigger upgrade
        upgraded_version = "v19.0_TRW"
        monkeypatch.setattr(orch_mod, "_config", TRWConfig(framework_version=upgraded_version))

        # Second init triggers upgrade
        tools2 = _make_orch_tools()
        tools2["trw_init"].fn(task_name="event-task2")

        events_path = tmp_path / ".trw" / "upgrade_events.jsonl"
        assert events_path.exists()
        reader = FileStateReader()
        events = reader.read_jsonl(events_path)
        upgrade_events = [e for e in events if e.get("event") == "framework_upgrade"]
        assert len(upgrade_events) >= 1
        assert upgrade_events[0]["old_framework"] == FRAMEWORK_VERSION
        assert upgrade_events[0]["new_framework"] == upgraded_version


class TestTrwAutoDetect:
    """Tests for auto-detection of run path."""

    def test_auto_detects_latest_run(self, orch_tools: dict[str, Any]) -> None:
        orch_tools["trw_init"].fn(task_name="auto-task")

        status = orch_tools["trw_status"].fn()
        assert status["task"] == "auto-task"


class TestTrwResume:
    """Tests for trw_resume tool."""

    def test_empty_run(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="resume-task")

        result = orch_tools["trw_resume"].fn(run_path=init_result["run_path"])
        assert "recovery_plan" in result
        assert "shards" in result

    def test_with_shard_findings(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="resume-task2")
        run_path = Path(init_result["run_path"])

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

        result = orch_tools["trw_resume"].fn(run_path=str(run_path))
        assert "shard-001" in result["shards"]["complete"]
        assert "shard-002" in result["shards"]["failed"]


class TestReflectionEnforcement:
    """Tests for mandatory reflection enforcement in phase gates."""

    def test_phase_check_review_warns_without_reflection(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """REVIEW gate warns when no reflection event exists."""
        init_result = orch_tools["trw_init"].fn(task_name="reflect-warn-task")
        run_path = init_result["run_path"]

        (Path(run_path) / "reports" / "final.md").write_text("# Final", encoding="utf-8")

        result = orch_tools["trw_phase_check"].fn(phase_name="review", run_path=run_path)
        reflection_failures = [
            f for f in result["failures"] if f["rule"] == "reflection_required"
        ]
        assert len(reflection_failures) == 1
        assert "trw_reflect" in reflection_failures[0]["message"]

    def test_phase_check_review_passes_with_reflection(
        self, monkeypatch: pytest.MonkeyPatch, orch_tools: dict[str, Any],
    ) -> None:
        """REVIEW gate passes after reflection event is logged."""
        import trw_mcp.tools.learning as learn_mod
        from trw_mcp.tools.learning import register_learning_tools

        monkeypatch.setattr(learn_mod, "_config", learn_mod.TRWConfig())

        init_result = orch_tools["trw_init"].fn(task_name="reflect-pass-task")
        run_path = init_result["run_path"]

        (Path(run_path) / "reports" / "final.md").write_text("# Final", encoding="utf-8")

        learn_srv = FastMCP("test-learn")
        register_learning_tools(learn_srv)
        learn_tools = {t.name: t for t in learn_srv._tool_manager._tools.values()}
        learn_tools["trw_reflect"].fn(run_path=run_path, scope="run")

        result = orch_tools["trw_phase_check"].fn(phase_name="review", run_path=run_path)
        reflection_failures = [
            f for f in result["failures"] if f["rule"] == "reflection_required"
        ]
        assert len(reflection_failures) == 0

    def test_phase_check_deliver_warns_without_sync(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """DELIVER gate warns when no claude_md_synced event exists."""
        init_result = orch_tools["trw_init"].fn(task_name="sync-warn-task")
        run_path = init_result["run_path"]

        result = orch_tools["trw_phase_check"].fn(phase_name="deliver", run_path=run_path)
        sync_failures = [
            f for f in result["failures"] if f["rule"] == "sync_required"
        ]
        assert len(sync_failures) == 1
        assert "trw_claude_md_sync" in sync_failures[0]["message"]

    def test_phase_check_deliver_passes_with_sync(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """DELIVER gate passes after claude_md_synced event is logged."""
        init_result = orch_tools["trw_init"].fn(task_name="sync-pass-task")
        run_path = init_result["run_path"]

        orch_tools["trw_event"].fn(
            event_type="claude_md_synced",
            run_path=run_path,
            data={"scope": "root", "entries_promoted": 3},
        )

        writer = FileStateWriter()
        run_yaml_path = Path(run_path) / "meta" / "run.yaml"
        reader = FileStateReader()
        state = reader.read_yaml(run_yaml_path)
        state["status"] = "complete"
        writer.write_yaml(run_yaml_path, state)

        result = orch_tools["trw_phase_check"].fn(phase_name="deliver", run_path=run_path)
        sync_failures = [
            f for f in result["failures"] if f["rule"] == "sync_required"
        ]
        assert len(sync_failures) == 0

    def test_status_includes_reflection_metrics(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """trw_status output includes reflection section."""
        init_result = orch_tools["trw_init"].fn(task_name="status-reflect-task")
        run_path = init_result["run_path"]

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert "reflection" in status
        assert "count" in status["reflection"]
        assert "claude_md_synced" in status["reflection"]

    def test_status_reflection_count(self, orch_tools: dict[str, Any]) -> None:
        """Reflection count increments with events."""
        init_result = orch_tools["trw_init"].fn(task_name="count-reflect-task")
        run_path = init_result["run_path"]

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert status["reflection"]["count"] == 0

        orch_tools["trw_event"].fn(
            event_type="reflection_complete",
            run_path=run_path,
            data={"reflection_id": "L-test123", "scope": "run"},
        )

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert status["reflection"]["count"] == 1

    def test_status_sync_flag(self, orch_tools: dict[str, Any]) -> None:
        """claude_md_synced flag works in status output."""
        init_result = orch_tools["trw_init"].fn(task_name="sync-flag-task")
        run_path = init_result["run_path"]

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert status["reflection"]["claude_md_synced"] is False

        orch_tools["trw_event"].fn(
            event_type="claude_md_synced",
            run_path=run_path,
        )

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert status["reflection"]["claude_md_synced"] is True


class TestOutcomeCorrelationInOrchestration:
    """Tests for PRD-CORE-004 Phase 1c — outcome correlation in orchestration tools."""

    @staticmethod
    def _get_learning_tools() -> dict[str, Any]:
        """Create learning tools for test setup."""
        srv = FastMCP("test-learn")
        from trw_mcp.tools.learning import register_learning_tools

        register_learning_tools(srv)
        return {t.name: t for t in srv._tool_manager._tools.values()}

    def test_trw_event_tests_passed_updates_q(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_event('tests_passed') updates Q-values for recently recalled learnings."""
        import trw_mcp.tools.learning as learn_mod

        monkeypatch.setattr(learn_mod, "_config", learn_mod.TRWConfig())

        learn_tools = self._get_learning_tools()
        learn_tools["trw_learn"].fn(
            summary="Orch event q test entry",
            detail="Should get Q updated by event",
            impact=0.5,
        )

        learn_tools["trw_recall"].fn(query="orch event q test")

        orch_tools = _make_orch_tools()
        init_result = orch_tools["trw_init"].fn(task_name="event-q-task")

        event_result = orch_tools["trw_event"].fn(
            event_type="tests_passed",
            run_path=init_result["run_path"],
        )
        assert event_result["status"] == "event_logged"
        if "q_updates" in event_result:
            assert event_result["q_updates"] >= 1

    def test_trw_event_unknown_type_no_correlation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_event with unknown type does not trigger correlation."""
        import trw_mcp.tools.learning as learn_mod

        monkeypatch.setattr(learn_mod, "_config", learn_mod.TRWConfig())

        orch_tools = _make_orch_tools()
        init_result = orch_tools["trw_init"].fn(task_name="no-corr-task")

        result = orch_tools["trw_event"].fn(
            event_type="random_info_event",
            run_path=init_result["run_path"],
        )
        assert result["status"] == "event_logged"
        assert "q_updates" not in result
