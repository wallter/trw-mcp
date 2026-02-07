"""Tests for MCP resources — config, templates, run_state, learnings."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_resources() -> dict[str, object]:
    """Create server and return resource map."""
    from fastmcp import FastMCP
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.templates import register_template_resources
    from trw_mcp.resources.run_state import register_run_state_resources

    srv = FastMCP("test")
    register_config_resources(srv)
    register_template_resources(srv)
    register_run_state_resources(srv)
    # Keys are strings in _resources dict
    return dict(srv._resource_manager._resources)


class TestConfigResource:
    """Tests for trw://framework/config resource."""

    def test_returns_config(self, tmp_path: Path) -> None:
        resources = _get_resources()
        resource = resources["trw://framework/config"]
        result = resource.fn()
        assert "parallelism_max" in result
        assert "timebox_hours" in result

    def test_includes_overrides(self, tmp_path: Path) -> None:
        # Write project config override
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        writer = FileStateWriter()
        writer.write_yaml(trw_dir / "config.yaml", {"custom_key": "custom_value"})

        resources = _get_resources()
        result = resources["trw://framework/config"].fn()
        assert "custom_key" in result


class TestFrameworkVersionsResource:
    """Tests for trw://framework/versions resource."""

    def test_no_frameworks_deployed(self, tmp_path: Path) -> None:
        resources = _get_resources()
        result = resources["trw://framework/versions"].fn()
        assert "No frameworks deployed" in result

    def test_with_deployed_frameworks(self, tmp_path: Path) -> None:
        # Create VERSION.yaml
        fw_dir = tmp_path / ".trw" / "frameworks"
        fw_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(fw_dir / "VERSION.yaml", {
            "framework_version": "v17.1_TRW",
            "aaref_version": "v1.1.0",
            "trw_mcp_version": "0.2.0",
            "deployed_at": "2026-02-07T12:00:00+00:00",
        })

        resources = _get_resources()
        result = resources["trw://framework/versions"].fn()
        assert "v17.1_TRW" in result
        assert "v1.1.0" in result


class TestLearningsSummaryResource:
    """Tests for trw://learnings/summary resource."""

    def test_empty_learnings(self, tmp_path: Path) -> None:
        resources = _get_resources()
        result = resources["trw://learnings/summary"].fn()
        assert "TRW Learnings Summary" in result

    def test_with_learnings(self, tmp_path: Path) -> None:
        # Create some learnings
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "test-learning.yaml", {
            "id": "L-001",
            "summary": "Test learning summary",
            "detail": "Test detail",
            "impact": 0.9,
        })

        resources = _get_resources()
        result = resources["trw://learnings/summary"].fn()
        assert "Test learning summary" in result


class TestTemplateResources:
    """Tests for template resources."""

    def test_prd_template(self, tmp_path: Path) -> None:
        resources = _get_resources()
        result = resources["trw://templates/prd"].fn()
        assert "PRD" in result
        assert "CATEGORY" in result

    def test_shard_card_template(self, tmp_path: Path) -> None:
        resources = _get_resources()
        result = resources["trw://templates/shard-card"].fn()
        assert "shard-001" in result
        assert "output_contract" in result


class TestRunStateResource:
    """Tests for trw://run/state resource."""

    def test_no_active_run(self, tmp_path: Path) -> None:
        resources = _get_resources()
        result = resources["trw://run/state"].fn()
        assert "No active run" in result

    def test_with_active_run(self, tmp_path: Path) -> None:
        # Create a run directory
        run_dir = tmp_path / "docs" / "test" / "runs" / "run-001" / "meta"
        run_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(run_dir / "run.yaml", {
            "run_id": "run-001",
            "task": "test",
            "status": "active",
            "phase": "research",
        })

        resources = _get_resources()
        result = resources["trw://run/state"].fn()
        assert "run-001" in result
        assert "active" in result
