"""Tests for PRD-CORE-084 ceremony mode config and AGENTS.md rendering."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import LIGHT_MODE_RECALL_CAP

from tests._test_core084_ceremony_adaptation_support import _run_agents_md_sync


class TestCeremonyModeConfig:
    """FR04: ceremony_mode config field defaults to 'full' and accepts 'light'."""

    def test_default_ceremony_mode_is_full(self) -> None:
        config = TRWConfig()
        assert config.ceremony_mode == "full"

    def test_ceremony_mode_light_accepted(self) -> None:
        config = TRWConfig(ceremony_mode="light")
        assert config.ceremony_mode == "light"

    def test_ceremony_mode_full_accepted(self) -> None:
        config = TRWConfig(ceremony_mode="full")
        assert config.ceremony_mode == "full"

    def test_light_mode_recall_cap_is_10(self) -> None:
        assert LIGHT_MODE_RECALL_CAP == 10


class TestAgentsMdCeremonyModeRendering:
    """FR04: ceremony_mode controls AGENTS.md rendering path."""

    def test_full_mode_renders_full_agents_section(self, tmp_path: Path) -> None:
        """ceremony_mode=full uses render_agents_trw_section() for AGENTS.md."""
        _run_agents_md_sync(tmp_path, ceremony_mode="full")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()
        content = agents_md.read_text(encoding="utf-8")
        assert "## Workflow" in content
        assert "## TRW Tools" in content

    def test_light_mode_renders_minimal_protocol(self, tmp_path: Path) -> None:
        """ceremony_mode=light uses render_minimal_protocol() for AGENTS.md."""
        _run_agents_md_sync(tmp_path, ceremony_mode="light")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()
        content = agents_md.read_text(encoding="utf-8")
        assert "trw_session_start()" in content
        assert "trw_deliver()" in content
        assert "## Workflow" not in content
        assert "## TRW Tools" not in content

    def test_light_mode_agents_md_is_compact(self, tmp_path: Path) -> None:
        """ceremony_mode=light produces compact AGENTS.md (fewer lines than full)."""
        full_dir = tmp_path / "full_project"
        full_dir.mkdir()
        light_dir = tmp_path / "light_project"
        light_dir.mkdir()

        _run_agents_md_sync(full_dir, ceremony_mode="full")
        full_content = (full_dir / "AGENTS.md").read_text(encoding="utf-8")

        _run_agents_md_sync(light_dir, ceremony_mode="light")
        light_content = (light_dir / "AGENTS.md").read_text(encoding="utf-8")

        assert len(light_content) < len(full_content)
