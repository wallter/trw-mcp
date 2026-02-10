"""Tests for overlay integration with orchestration (PRD-CORE-017 Step 2.4).

Validates that trw_init deploys overlays, assembles phase-specific snapshots,
and falls back to monolithic when overlays are absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from trw_mcp.state.persistence import FileStateReader

ALL_PHASES = ["research", "plan", "implement", "validate", "review", "deliver"]


@pytest.fixture(autouse=True)
def _set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point TRW_PROJECT_ROOT at tmp_path for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    import trw_mcp.tools.orchestration as orch_mod
    monkeypatch.setattr(orch_mod, "_config", orch_mod.TRWConfig())


@pytest.fixture
def run_init(tmp_path: Path) -> Callable[..., dict[str, Any]]:
    """Return a callable that invokes trw_init with given kwargs."""
    from fastmcp import FastMCP
    from trw_mcp.tools.orchestration import register_orchestration_tools

    srv = FastMCP("test")
    register_orchestration_tools(srv)
    tools = {t.name: t for t in srv._tool_manager._tools.values()}
    return tools["trw_init"].fn


class TestOverlayDeployment:
    """trw_init deploys overlay files alongside monolithic."""

    def test_init_deploys_core_and_overlays(
        self, tmp_path: Path, run_init: Callable[..., dict[str, Any]],
    ) -> None:
        """After init, .trw/frameworks/ contains core + all phase overlays."""
        run_init(task_name="overlay-test")

        frameworks_dir = tmp_path / ".trw" / "frameworks"
        assert (frameworks_dir / "trw-core.md").exists()
        assert (frameworks_dir / "overlays").is_dir()

        for phase in ALL_PHASES:
            overlay_path = frameworks_dir / "overlays" / f"trw-{phase}.md"
            assert overlay_path.exists(), f"Missing overlay: trw-{phase}.md"

    def test_version_yaml_includes_overlays(
        self, tmp_path: Path, run_init: Callable[..., dict[str, Any]],
    ) -> None:
        """VERSION.yaml lists deployed overlay phases."""
        run_init(task_name="version-test")

        version_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
        data = FileStateReader().read_yaml(version_path)
        assert "overlays_deployed" in data
        deployed = data["overlays_deployed"]
        assert isinstance(deployed, list)
        assert len(deployed) == 6


class TestSnapshotAssembly:
    """trw_init assembles phase-specific FRAMEWORK_SNAPSHOT."""

    def test_snapshot_uses_assembled_framework(
        self, run_init: Callable[..., dict[str, Any]],
    ) -> None:
        """FRAMEWORK_SNAPSHOT.md contains core + research overlay content."""
        result = run_init(task_name="snapshot-test")

        snapshot_path = Path(result["run_path"]) / "meta" / "FRAMEWORK_SNAPSHOT.md"
        assert snapshot_path.exists()
        content = snapshot_path.read_text()
        assert "v18.1_TRW" in content
        assert "Research Reactor" in content or "EXECUTION MODEL" in content

    def test_snapshot_fallback_to_monolithic(
        self,
        run_init: Callable[..., dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Falls back to monolithic if assemble_framework fails."""
        import trw_mcp.state.framework as fw_mod

        def mock_assemble(*args: object, **kwargs: object) -> str:
            raise FileNotFoundError("mocked")

        monkeypatch.setattr(fw_mod, "assemble_framework", mock_assemble)

        result = run_init(task_name="fallback-test")

        snapshot_path = Path(result["run_path"]) / "meta" / "FRAMEWORK_SNAPSHOT.md"
        assert snapshot_path.exists()
        content = snapshot_path.read_text()
        assert "v18.1_TRW" in content
