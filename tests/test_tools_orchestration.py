"""Tests for orchestration tools — init, status, checkpoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync

import trw_mcp.tools.orchestration as orch_mod
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools.orchestration import register_orchestration_tools

# Single source of truth for the expected framework version in test assertions.
# Must match the default in TRWConfig.framework_version.
FRAMEWORK_VERSION = "v24.2_TRW"


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _make_orch_tools() -> dict[str, Any]:
    """Create a FastMCP server with orchestration tools and return the tools dict."""
    srv = FastMCP("test")
    register_orchestration_tools(srv)
    return get_tools_sync(srv)


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
        assert (run_path / "scratch" / "_orchestrator").exists()
        assert (run_path / "shards").exists()

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


class TestFrameworkDeployment:
    """Tests for framework deployment to .trw/ (PRD-CORE-002 Phase 2)."""

    def test_init_creates_frameworks_dir(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """trw_init creates .trw/frameworks/ directory."""
        orch_tools["trw_init"].fn(task_name="fw-deploy-task")
        assert (tmp_path / ".trw" / "frameworks").exists()

    @pytest.mark.parametrize("filename,task_name", [
        ("FRAMEWORK.md", "fw-content-task"),
        ("AARE-F-FRAMEWORK.md", "aaref-deploy-task"),
    ])
    def test_init_deploys_framework_files(
        self, tmp_path: Path, orch_tools: dict[str, Any],
        filename: str, task_name: str,
    ) -> None:
        """Framework markdown files deployed to .trw/frameworks/ with content."""
        orch_tools["trw_init"].fn(task_name=task_name)

        fw_path = tmp_path / ".trw" / "frameworks" / filename
        assert fw_path.exists()
        assert len(fw_path.read_text(encoding="utf-8")) > 100

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

    def _setup_upgrade(
        self, monkeypatch: pytest.MonkeyPatch, upgraded_version: str,
    ) -> None:
        """Run first init at default version, then patch config to trigger upgrade."""
        _make_orch_tools()["trw_init"].fn(task_name="upgrade-task1")
        monkeypatch.setattr(orch_mod, "_config", TRWConfig(framework_version=upgraded_version))

    def test_same_version_skips_rewrite(
        self, tmp_path: Path, orch_tools: dict[str, Any],
    ) -> None:
        """No file modification when versions match."""
        orch_tools["trw_init"].fn(task_name="skip-rewrite-task1")

        fw_path = tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md"
        stat_after_first = fw_path.stat()
        # Advance mtime so we can detect if a rewrite occurs
        past = stat_after_first.st_mtime - 10
        os.utime(fw_path, (past, past))

        orch_tools["trw_init"].fn(task_name="skip-rewrite-task2")

        # If version matched, init should NOT rewrite — mtime stays at past
        assert fw_path.stat().st_mtime == past

    def test_version_mismatch_triggers_upgrade(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Change config version, re-init, verify files updated."""
        upgraded_version = "v19.0_TRW"
        self._setup_upgrade(monkeypatch, upgraded_version)

        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        assert FileStateReader().read_yaml(version_path)["framework_version"] == FRAMEWORK_VERSION

        _make_orch_tools()["trw_init"].fn(task_name="upgrade-task2")

        updated = FileStateReader().read_yaml(version_path)
        assert updated["framework_version"] == upgraded_version

    def test_upgrade_logs_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Upgrade event appears in events log."""
        upgraded_version = "v19.0_TRW"
        self._setup_upgrade(monkeypatch, upgraded_version)

        _make_orch_tools()["trw_init"].fn(task_name="event-task2")

        events_path = tmp_path / ".trw" / "upgrade_events.jsonl"
        assert events_path.exists()
        events = FileStateReader().read_jsonl(events_path)
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


class TestTrwInitWaveManifest:
    """Tests for trw_init with wave_manifest parameter."""

    def test_init_without_wave_manifest_no_wave_keys(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """Without wave_manifest, result has no wave fields."""
        result = orch_tools["trw_init"].fn(task_name="no-wave-task")

        assert result["status"] == "initialized"
        assert "wave_plan_status" not in result
        assert "wave_count" not in result


class TestCeremonyScoring:
    """Tests for compute_ceremony_score() — direct and tool_invocation event formats."""

    def _score(self, events: list[dict[str, object]]) -> dict[str, object]:
        from trw_mcp.state.analytics_report import compute_ceremony_score
        return compute_ceremony_score(events)

    # --- Direct event format (original behavior) ---

    def test_direct_session_start_detected(self) -> None:
        result = self._score([{"event": "session_start"}])
        assert result["session_start"] is True
        assert result["score"] == 30

    def test_direct_deliver_via_reflection_complete(self) -> None:
        result = self._score([{"event": "reflection_complete"}])
        assert result["deliver"] is True
        assert result["score"] == 30

    def test_direct_deliver_via_claude_md_synced(self) -> None:
        result = self._score([{"event": "claude_md_synced"}])
        assert result["deliver"] is True

    def test_direct_checkpoint_counted(self) -> None:
        result = self._score([
            {"event": "checkpoint"},
            {"event": "checkpoint"},
        ])
        assert result["checkpoint_count"] == 2
        assert result["score"] == 20

    def test_direct_learn_counted(self) -> None:
        result = self._score([{"event": "learn_recorded"}])
        assert result["learn_count"] == 1
        assert result["score"] == 10

    def test_direct_build_check_complete(self) -> None:
        result = self._score([{"event": "build_check_complete", "tests_passed": "true"}])
        assert result["build_check"] is True
        assert result["build_passed"] is True
        assert result["score"] == 10

    # --- Tool invocation event format (new behavior) ---

    def test_tool_invocation_session_start_detected(self) -> None:
        result = self._score([
            {"event": "tool_invocation", "tool_name": "trw_session_start"},
        ])
        assert result["session_start"] is True
        assert result["score"] == 30

    def test_tool_invocation_deliver_via_trw_deliver(self) -> None:
        result = self._score([
            {"event": "tool_invocation", "tool_name": "trw_deliver"},
        ])
        assert result["deliver"] is True
        assert result["score"] == 30

    def test_tool_invocation_deliver_via_trw_reflect(self) -> None:
        result = self._score([
            {"event": "tool_invocation", "tool_name": "trw_reflect"},
        ])
        assert result["deliver"] is True

    def test_tool_invocation_checkpoint_counted(self) -> None:
        result = self._score([
            {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
            {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
            {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
        ])
        assert result["checkpoint_count"] == 3
        assert result["score"] == 20

    def test_tool_invocation_learn_counted(self) -> None:
        result = self._score([
            {"event": "tool_invocation", "tool_name": "trw_learn"},
            {"event": "tool_invocation", "tool_name": "trw_learn"},
        ])
        assert result["learn_count"] == 2
        assert result["score"] == 10

    def test_tool_invocation_build_check(self) -> None:
        result = self._score([
            {"event": "tool_invocation", "tool_name": "trw_build_check"},
        ])
        assert result["build_check"] is True
        assert result["score"] == 10

    # --- trw_deliver_complete event (hooks) ---

    def test_trw_deliver_complete_event_detected(self) -> None:
        result = self._score([{"event": "trw_deliver_complete"}])
        assert result["deliver"] is True
        assert result["score"] == 30

    # --- Mixed real-world scenario ---

    def test_mixed_formats_full_score(self) -> None:
        """Real-world mix: tool_invocation events produce full 100-point score."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_session_start"},
            {"event": "tool_invocation", "tool_name": "trw_learn"},
            {"event": "tool_invocation", "tool_name": "trw_learn"},
            {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
            {"event": "tool_invocation", "tool_name": "trw_build_check"},
            {"event": "tool_invocation", "tool_name": "trw_deliver"},
        ]
        result = self._score(events)
        assert result["session_start"] is True
        assert result["deliver"] is True
        assert result["checkpoint_count"] == 1
        assert result["learn_count"] == 2
        assert result["build_check"] is True
        assert result["score"] == 100

    def test_unrelated_tool_invocation_ignored(self) -> None:
        """tool_invocation with unrelated tool_name does not affect score."""
        result = self._score([
            {"event": "tool_invocation", "tool_name": "trw_status"},
        ])
        assert result["score"] == 0

    def test_empty_events_zero_score(self) -> None:
        result = self._score([])
        assert result["score"] == 0
        assert result["session_start"] is False
        assert result["deliver"] is False
        assert result["checkpoint_count"] == 0
        assert result["learn_count"] == 0
        assert result["build_check"] is False


class TestTrwInitComplexity:
    """Tests for trw_init complexity classification wiring (FR04, FR08)."""

    def test_init_without_signals_backward_compat(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """NFR02: trw_init without complexity_signals preserves existing behavior."""
        result = orch_tools["trw_init"].fn(task_name="no-signals")
        assert result["status"] == "initialized"
        assert "complexity_class" not in result

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml.get("complexity_class") is None
        assert run_yaml.get("complexity_signals") is None
        assert run_yaml.get("phase_requirements") is None

    def test_init_with_minimal_signals(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """FR08: trw_init with simple signals classifies as MINIMAL."""
        result = orch_tools["trw_init"].fn(
            task_name="minimal-task",
            complexity_signals={"files_affected": 1},
        )
        assert result["complexity_class"] == "MINIMAL"

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_class"] == "MINIMAL"
        assert run_yaml["complexity_signals"]["files_affected"] == 1
        assert run_yaml["phase_requirements"]["mandatory"] == ["IMPLEMENT", "DELIVER"]
        assert "RESEARCH" in run_yaml["phase_requirements"]["skipped"]

    def test_init_with_comprehensive_signals(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """FR08: trw_init with complex signals classifies as COMPREHENSIVE."""
        result = orch_tools["trw_init"].fn(
            task_name="complex-task",
            complexity_signals={
                "files_affected": 5,
                "novel_patterns": True,
                "cross_cutting": True,
            },
        )
        assert result["complexity_class"] == "COMPREHENSIVE"

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_class"] == "COMPREHENSIVE"
        assert len(run_yaml["phase_requirements"]["mandatory"]) == 6

    def test_init_with_hard_override(
        self, orch_tools: dict[str, Any],
    ) -> None:
        """FR05/FR09: trw_init with 2 risk signals records override."""
        result = orch_tools["trw_init"].fn(
            task_name="secure-task",
            complexity_signals={
                "files_affected": 1,
                "security_change": True,
                "data_migration": True,
            },
        )
        assert result["complexity_class"] == "COMPREHENSIVE"

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_override"] is not None
        assert "hard override" in run_yaml["complexity_override"]["reason"]
        assert "security_change" in run_yaml["complexity_override"]["signals"]
