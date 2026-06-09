"""Framework deployment, version tracking, and related orchestration tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests._tools_orchestration_support import (
    FRAMEWORK_VERSION,
    _make_orch_tools,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from ._tools_orchestration_support import orch_tools  # noqa: F401

from ._tools_orchestration_support import orch_tools  # noqa: F401

from ._tools_orchestration_support import orch_tools  # noqa: F401


class TestFrameworkDeployment:
    """Tests for framework deployment to .trw/ (PRD-CORE-002 Phase 2)."""

    def test_init_creates_frameworks_dir(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_init creates .trw/frameworks/ directory."""
        orch_tools["trw_init"].fn(task_name="fw-deploy-task")
        assert (tmp_path / ".trw" / "frameworks").exists()

    @pytest.mark.parametrize(
        "filename,task_name",
        [
            ("FRAMEWORK.md", "fw-content-task"),
            ("AARE-F-FRAMEWORK.md", "aaref-deploy-task"),
        ],
    )
    def test_init_deploys_framework_files(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
        filename: str,
        task_name: str,
    ) -> None:
        """Framework markdown files deployed to .trw/frameworks/ with content."""
        orch_tools["trw_init"].fn(task_name=task_name)

        fw_path = tmp_path / ".trw" / "frameworks" / filename
        assert fw_path.exists()
        assert len(fw_path.read_text(encoding="utf-8")) > 100

    def test_init_creates_version_yaml(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
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
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """.trw/templates/claude_md.md exists after init."""
        orch_tools["trw_init"].fn(task_name="template-deploy-task")

        template_path = tmp_path / ".trw" / "templates" / "claude_md.md"
        assert template_path.exists()
        content = template_path.read_text(encoding="utf-8")
        assert "{{imperative_opener}}" in content

    def test_repeated_init_preserves_custom_template(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
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
        self,
        monkeypatch: pytest.MonkeyPatch,
        upgraded_version: str,
    ) -> None:
        """Run first init at default version, then patch config to trigger upgrade."""
        _make_orch_tools()["trw_init"].fn(task_name="upgrade-task1")
        monkeypatch.setattr(
            "trw_mcp.tools._orchestration_helpers.get_config",
            lambda: TRWConfig(framework_version=upgraded_version),
        )

    def test_same_version_skips_rewrite(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """No file modification when versions match."""
        orch_tools["trw_init"].fn(task_name="skip-rewrite-task1")

        fw_path = tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md"
        stat_after_first = fw_path.stat()
        past = stat_after_first.st_mtime - 10
        os.utime(fw_path, (past, past))

        orch_tools["trw_init"].fn(task_name="skip-rewrite-task2")

        assert fw_path.stat().st_mtime == past

    def test_version_mismatch_triggers_upgrade(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """Without wave_manifest, result has no wave fields."""
        result = orch_tools["trw_init"].fn(task_name="no-wave-task")

        assert result["status"] == "initialized"
        assert "wave_plan_status" not in result
        assert "wave_count" not in result
