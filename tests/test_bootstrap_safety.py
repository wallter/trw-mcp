"""Bootstrap/update safety hardening tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._ide_targets import write_target_renderer_coverage
from trw_mcp.models.config._client_profile import WriteTargets


@pytest.fixture()
def initialized_repo(tmp_path: Path) -> Path:
    """Create a minimal repo with TRW initialized."""
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path)
    assert not result["errors"]
    return tmp_path


def test_write_targets_booleans_have_renderer_coverage() -> None:
    """Every WriteTargets boolean has renderer coverage or an explicit unsupported marker."""
    coverage = write_target_renderer_coverage()
    boolean_flags = {
        name
        for name, field in WriteTargets.model_fields.items()
        if field.annotation is bool or str(field.annotation) == "bool"
    }

    assert boolean_flags <= coverage.keys()
    assert all(coverage[flag] for flag in boolean_flags)


@pytest.mark.parametrize(
    ("ide", "expected_path"),
    [
        ("claude-code", "CLAUDE.md"),
        ("opencode", ".opencode/INSTRUCTIONS.md"),
        ("cursor-cli", "AGENTS.md"),
        ("codex", ".codex/INSTRUCTIONS.md"),
        ("copilot", ".github/copilot-instructions.md"),
        ("gemini", "GEMINI.md"),
        ("antigravity-cli", "ANTIGRAVITY.md"),
    ],
)
def test_supported_profiles_generate_instruction_surface(
    initialized_repo: Path,
    ide: str,
    expected_path: str,
) -> None:
    """Supported active client profiles have golden coverage for instruction output surfaces."""
    result = update_project(initialized_repo, ide=ide)

    assert not result["errors"]
    instruction_path = initialized_repo / expected_path
    assert instruction_path.is_file()
    assert "TRW" in instruction_path.read_text(encoding="utf-8")


def test_update_project_rolls_back_directories_after_mid_write_failure(initialized_repo: Path) -> None:
    """A post-core write failure restores previously modified framework directories."""
    framework_path = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
    original = "custom framework content before failed update\n"
    framework_path.write_text(original, encoding="utf-8")

    with patch("trw_mcp.bootstrap._update_project._write_version_yaml", side_effect=OSError("disk full")):
        result = update_project(initialized_repo)

    assert any("update-project failed" in error for error in result["errors"])
    assert any("rolled back" in warning for warning in result["warnings"])
    assert framework_path.read_text(encoding="utf-8") == original
