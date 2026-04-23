"""PRD-CORE-149 FR06: non-registry 'Claude Code' literals are gone.

Also validates ``ClientProfile.config_dir`` derives sensibly from
``write_targets.instruction_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config._client_profile import ClientProfile, WriteTargets
from trw_mcp.models.config._profiles import resolve_client_profile

pytestmark = pytest.mark.unit

_PROFILES_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "models" / "config" / "_profiles.py"


def test_profiles_has_exactly_one_claude_code_literal() -> None:
    """FR06: only the registry entry (display_name='Claude Code') may hardcode the literal."""
    content = _PROFILES_SRC.read_text(encoding="utf-8")
    # count occurrences of the bare literal
    assert content.count("Claude Code") == 1, (
        "Expected exactly one 'Claude Code' literal in _profiles.py (the "
        "registry display_name). Found different count -- see FR06."
    )


def test_config_dir_defaults_to_instruction_path_parent() -> None:
    profile = ClientProfile(
        client_id="c",
        display_name="C",
        write_targets=WriteTargets(instruction_path=".claude/INSTRUCTIONS.md"),
    )
    assert profile.config_dir == ".claude"


def test_config_dir_for_opencode_profile() -> None:
    profile = resolve_client_profile("opencode")
    assert profile.config_dir == ".opencode"


def test_config_dir_for_cursor_ide_profile() -> None:
    profile = resolve_client_profile("cursor-ide")
    # .cursor/rules/trw-ceremony.mdc -> parent '.cursor/rules'
    assert profile.config_dir == ".cursor/rules"


def test_config_dir_falls_back_to_trw_when_no_path() -> None:
    profile = ClientProfile(
        client_id="c",
        display_name="C",
        write_targets=WriteTargets(instruction_path=""),
    )
    assert profile.config_dir == ".trw"


def test_config_dir_falls_back_when_file_has_no_parent() -> None:
    """AGENTS.md (cursor-cli) has no parent directory -> falls back to .trw."""
    profile = resolve_client_profile("cursor-cli")
    assert profile.config_dir == ".trw"
