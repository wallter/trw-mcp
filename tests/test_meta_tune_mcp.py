"""MCP tool registration and invocation tests for trw_meta_tune.

Sprint 84: Verify the meta-tune tool is registered and callable via FastMCP.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
class TestMetaTuneToolRegistration:
    """Verify trw_meta_tune is registered and importable."""

    def test_register_importable(self) -> None:
        from trw_mcp.tools.meta_tune import register_meta_tune_tools
        assert callable(register_meta_tune_tools)

    def test_tool_registered_on_server(self) -> None:
        from fastmcp import FastMCP
        from tests.conftest import get_tools_sync
        from trw_mcp.tools.meta_tune import register_meta_tune_tools

        server = FastMCP("test-meta-tune")
        register_meta_tune_tools(server)

        tools = get_tools_sync(server)
        assert "trw_meta_tune" in tools

    def test_tool_has_parameters(self) -> None:
        from fastmcp import FastMCP
        from tests.conftest import get_tools_sync
        from trw_mcp.tools.meta_tune import register_meta_tune_tools

        server = FastMCP("test-meta-tune-params")
        register_meta_tune_tools(server)

        tools = get_tools_sync(server)
        meta_tune_tool = tools["trw_meta_tune"]
        # Verify parameters via the tool's underlying function signature
        import inspect
        sig = inspect.signature(meta_tune_tool.fn)
        assert "steps" in sig.parameters
        assert "dry_run" in sig.parameters


@pytest.mark.unit
class TestExecuteMetaTuneDirectly:
    """Test execute_meta_tune function directly (no MCP server needed)."""

    def test_empty_learnings_all_steps(self, tmp_path: pytest.TempPathFactory) -> None:
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"  # type: ignore[union-attr]
        trw_dir.mkdir()
        (trw_dir / "meta").mkdir()
        (trw_dir / "memory").mkdir()

        report = execute_meta_tune(
            trw_dir,
            learnings=[],
            config={"model_family": "test", "trw_version": "0.0.1", "shadow_mode": True},
        )
        assert len(report.steps) == 9
        # All steps should complete (ok or skipped), none should error
        for step in report.steps:
            assert step.status in ("ok", "skipped"), f"Step {step.step} has status {step.status}: {step.details}"

    def test_shadow_default_from_config(self, tmp_path: pytest.TempPathFactory) -> None:
        from trw_mcp.tools.meta_tune import execute_meta_tune

        trw_dir = tmp_path / ".trw"  # type: ignore[union-attr]
        trw_dir.mkdir()
        (trw_dir / "meta").mkdir()
        (trw_dir / "memory").mkdir()

        entry = {
            "id": "L-shadow-cfg",
            "summary": "test",
            "detail": "detail",
            "status": "active",
            "type": "workaround",
            "expires": "2020-01-01",
            "protection_tier": "normal",
            "anchors": [],
            "anchor_validity": 1.0,
            "tags": [],
            "reviewed_at": "",
        }
        # shadow_mode=True in config, not passed explicitly
        report = execute_meta_tune(
            trw_dir,
            learnings=[entry],
            config={"shadow_mode": True},
        )
        # Entry should NOT be mutated because shadow_mode is True
        assert entry["protection_tier"] == "normal"
