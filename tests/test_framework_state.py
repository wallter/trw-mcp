"""Tests for framework overlay loading and assembly (PRD-CORE-017 Step 2.3).

Validates load_core, load_overlay, assemble_framework, and monolithic fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.framework import (
    assemble_framework,
    load_core,
    load_overlay,
)


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create a .trw directory with core + overlay framework files."""
    trw = tmp_path / ".trw"
    frameworks = trw / "frameworks"
    overlays = frameworks / "overlays"
    overlays.mkdir(parents=True)

    (frameworks / "trw-core.md").write_text("# CORE\nShared content\n")
    (overlays / "trw-research.md").write_text("## RESEARCH OVERLAY\nResearch content\n")
    (overlays / "trw-implement.md").write_text("## IMPLEMENT OVERLAY\nImplement content\n")

    return trw


class TestLoadCore:
    """load_core returns core text or None."""

    def test_loads_existing_core(self, trw_dir: Path) -> None:
        result = load_core(trw_dir)
        assert result is not None
        assert "# CORE" in result

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        trw = tmp_path / ".trw"
        trw.mkdir()
        assert load_core(trw) is None


class TestLoadOverlay:
    """load_overlay returns overlay text or None."""

    def test_loads_existing_overlay(self, trw_dir: Path) -> None:
        result = load_overlay(trw_dir, "research")
        assert result is not None
        assert "RESEARCH OVERLAY" in result

    def test_returns_none_for_missing_overlay(self, trw_dir: Path) -> None:
        assert load_overlay(trw_dir, "deliver") is None

    def test_returns_none_when_no_overlays_dir(self, tmp_path: Path) -> None:
        trw = tmp_path / ".trw"
        trw.mkdir()
        assert load_overlay(trw, "research") is None


class TestAssembleFramework:
    """assemble_framework concatenates core + overlay with fallback."""

    def test_assembles_core_plus_overlay(self, trw_dir: Path) -> None:
        result = assemble_framework(trw_dir, "research")
        assert "# CORE" in result
        assert "RESEARCH OVERLAY" in result
        assert "---" in result  # separator between core and overlay

    def test_core_only_when_no_overlay(self, trw_dir: Path) -> None:
        result = assemble_framework(trw_dir, "deliver")
        assert "# CORE" in result
        assert "DELIVER" not in result

    def test_monolithic_fallback(self, tmp_path: Path) -> None:
        trw = tmp_path / ".trw"
        frameworks = trw / "frameworks"
        frameworks.mkdir(parents=True)
        (frameworks / "FRAMEWORK.md").write_text("# MONOLITHIC\nFull content\n")

        result = assemble_framework(trw, "research")
        assert "# MONOLITHIC" in result

    def test_raises_when_nothing_found(self, tmp_path: Path) -> None:
        trw = tmp_path / ".trw"
        (trw / "frameworks").mkdir(parents=True)

        with pytest.raises(FileNotFoundError, match="No framework found"):
            assemble_framework(trw, "research")


class TestBundledOverlays:
    """Verify bundled overlay data files exist and are well-formed."""

    @pytest.fixture()
    def data_dir(self) -> Path:
        return Path(__file__).parent.parent / "src" / "trw_mcp" / "data"

    def test_core_file_exists(self, data_dir: Path) -> None:
        core = data_dir / "trw-core.md"
        assert core.exists()
        content = core.read_text()
        assert "v18.1_TRW" in content
        assert content.count("\n") > 100

    @pytest.mark.parametrize(
        "phase",
        ["research", "plan", "implement", "validate", "review", "deliver"],
    )
    def test_overlay_file_exists(self, data_dir: Path, phase: str) -> None:
        overlay = data_dir / "overlays" / f"trw-{phase}.md"
        assert overlay.exists(), f"Missing overlay: trw-{phase}.md"
        content = overlay.read_text()
        assert "OVERLAY" in content
        assert content.count("\n") > 10
