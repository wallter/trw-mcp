"""Tests for PRD-CORE-084 ceremony mode config and AGENTS.md rendering."""

from __future__ import annotations

from pathlib import Path

from tests._test_core084_ceremony_adaptation_support import _run_agents_md_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import LIGHT_MODE_RECALL_CAP
from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH, LINK_BODY


def _instructions(root: Path) -> str:
    """The TRW-owned file that holds the rendered protocol body (PRD-CORE-341)."""
    return (root / INSTRUCTIONS_RELPATH).read_text(encoding="utf-8")


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
    """FR04: ceremony_mode controls the rendering path of the body AGENTS.md links (``.trw/INSTRUCTIONS.md``)."""

    def test_full_mode_renders_full_agents_section(self, tmp_path: Path) -> None:
        """ceremony_mode=full renders render_agents_trw_section() into the instructions file."""
        _run_agents_md_sync(tmp_path, ceremony_mode="full")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()
        assert LINK_BODY in agents_md.read_text(encoding="utf-8")
        content = _instructions(tmp_path)
        assert "## Workflow" in content
        # PRD-CORE-301-FR13: the tool list became a pointer to the live surface.
        assert 'trw_status(detail="surface")' in content

    def test_light_mode_renders_minimal_protocol(self, tmp_path: Path) -> None:
        """ceremony_mode=light renders render_minimal_protocol() into the instructions file."""
        _run_agents_md_sync(tmp_path, ceremony_mode="light")

        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()
        assert LINK_BODY in agents_md.read_text(encoding="utf-8")
        content = _instructions(tmp_path)
        assert "trw_session_start()" in content
        assert "trw_deliver()" in content
        assert "## Workflow" not in content
        assert "## TRW Tools" not in content

    def test_light_mode_agents_md_is_compact(self, tmp_path: Path) -> None:
        """ceremony_mode=light produces a compact instructions file (fewer lines than full)."""
        full_dir = tmp_path / "full_project"
        full_dir.mkdir()
        light_dir = tmp_path / "light_project"
        light_dir.mkdir()

        _run_agents_md_sync(full_dir, ceremony_mode="full")
        full_content = _instructions(full_dir)

        _run_agents_md_sync(light_dir, ceremony_mode="light")
        light_content = _instructions(light_dir)

        # PRD-CORE-301-FR13: the full block lost its tool list and delegation guide, so
        # both bodies now carry the same per-turn rules. Light may exceed full only by
        # the one rule full states elsewhere: its "Verify" bullet.
        verify = (
            "- **Verify**: Run project-native checks after meaningful changes \u2014 fix failures before moving on.\n"
        )
        assert verify in light_content and "## Workflow" not in light_content
        assert len(light_content) <= len(full_content) + len(verify)
