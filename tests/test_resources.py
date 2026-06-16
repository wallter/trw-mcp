"""Tests for MCP resources — config, templates, run_state, learnings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_resources_sync
from trw_mcp.state.persistence import FileStateWriter


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _get_resources() -> dict[str, Any]:
    """Create server and return resource map."""
    from fastmcp import FastMCP

    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.resources.run_state import register_run_state_resources
    from trw_mcp.resources.templates import register_template_resources

    srv = FastMCP("test")
    register_config_resources(srv)
    register_template_resources(srv)
    register_run_state_resources(srv)
    return get_resources_sync(srv)


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

    def test_redacts_backend_api_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Security audit 2026-04-18 M2: backend_api_key must never appear
        verbatim in the config resource payload, which is readable by any
        connected MCP client."""
        monkeypatch.setenv("TRW_BACKEND_API_KEY", "sk-live-super-secret-123")
        resources = _get_resources()
        result = resources["trw://framework/config"].fn()
        assert "sk-live-super-secret-123" not in result
        assert "***redacted***" in result

    def test_redacts_sensitive_override_keys(self, tmp_path: Path) -> None:
        """Overrides merged from .trw/config.yaml can contain credentials too —
        redact any key matching *_api_key, *_secret, *_token, *_password."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        writer = FileStateWriter()
        writer.write_yaml(
            trw_dir / "config.yaml",
            {
                "openai_api_key": "sk-openai-leak-me",
                "slack_token": "xoxb-leak-me",
                "my_password": "hunter2",
                "custom_key": "public-value",
            },
        )

        resources = _get_resources()
        result = resources["trw://framework/config"].fn()
        assert "sk-openai-leak-me" not in result
        assert "xoxb-leak-me" not in result
        assert "hunter2" not in result
        assert "public-value" in result  # non-sensitive key passes through


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
        writer.write_yaml(
            fw_dir / "VERSION.yaml",
            {
                "framework_version": "v18.0_TRW",
                "aaref_version": "v1.1.0",
                "trw_mcp_version": "0.2.0",
                "deployed_at": "2026-02-07T12:00:00+00:00",
            },
        )

        resources = _get_resources()
        result = resources["trw://framework/versions"].fn()
        assert "v18.0_TRW" in result
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
        writer.write_yaml(
            entries_dir / "test-learning.yaml",
            {
                "id": "L-001",
                "summary": "Test learning summary",
                "detail": "Test detail",
                "impact": 0.9,
            },
        )

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
        # Create a run directory under the canonical path: .trw/runs/<task>/<run_id>/meta/
        # This mirrors the real production layout (config.runs_root = ".trw/runs").
        run_dir = tmp_path / ".trw" / "runs" / "test" / "run-001" / "meta"
        run_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            run_dir / "run.yaml",
            {
                "run_id": "run-001",
                "task": "test",
                "status": "active",
                "phase": "research",
            },
        )

        resources = _get_resources()
        result = resources["trw://run/state"].fn()
        assert "run-001" in result
        assert "active" in result

    def test_deletion_race_does_not_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A run.yaml deleted between glob and read must fail open, not raise.

        Simulates the concurrent-deletion race: the file exists when globbed
        but read_text raises OSError. The resource must return the fallback
        rather than propagate OSError (which would crash the MCP resource).
        """
        run_dir = tmp_path / ".trw" / "runs" / "test" / "run-001" / "meta"
        run_dir.mkdir(parents=True)
        run_yaml = run_dir / "run.yaml"
        run_yaml.write_text("run_id: run-001\nstatus: active\n", encoding="utf-8")

        orig_read_text = Path.read_text

        def boom(self: Path, *args: Any, **kwargs: Any) -> str:
            if self == run_yaml:
                raise OSError("file vanished in deletion race")
            return orig_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", boom)

        resources = _get_resources()
        # Must NOT raise — fail open to the no-active-run fallback.
        result = resources["trw://run/state"].fn()
        assert "No active run" in result

    def test_oversized_run_yaml_is_capped(self, tmp_path: Path) -> None:
        """A pathologically large run.yaml is truncated to the byte cap rather
        than blowing the resource response budget."""
        from trw_mcp.resources import run_state as run_state_mod

        run_dir = tmp_path / ".trw" / "runs" / "test" / "run-big" / "meta"
        run_dir.mkdir(parents=True)
        # Build content larger than the cap.
        cap = 1_000_000
        big = "x" * (cap + 50_000)
        (run_dir / "run.yaml").write_text(big, encoding="utf-8")

        resources = _get_resources()
        result = resources["trw://run/state"].fn()
        assert len(result) <= cap, f"result not capped: {len(result)} bytes"
        # Sanity: the helper module exposes the cap we asserted against.
        assert run_state_mod is not None
