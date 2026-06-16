"""Cross-client parity for the /trw-reflect skill (PRD-CORE-187 FR01).

The end-of-session reflection skill must be distributable to every client that
ships a bundled slash-command skill subset. The generic ``.claude/skills/``
path is covered by ``test_bundled_skills.py::test_reflect_skill_matches_root_source``;
this module locks in parity for the curated **codex**, **copilot**,
**opencode**, and **copilot-plugin** bundled skill subsets, plus the
cursor-IDE curated list.

Beyond existence, every copy must preserve the skill's load-bearing contract:
the approval gate, the ``.trw/reflections/`` ledger path, the canon-protection
guardrail, and the four implementation routes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.models.skill_manifest import validate_skill_markdown

# Bundled source copies (one per client variant) that must each ship a valid
# trw-reflect skill carrying the full behavioral contract.
_BUNDLED_REFLECT_SOURCES: tuple[Path, ...] = (
    _DATA_DIR / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "codex" / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "copilot" / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "opencode" / "skills" / "trw-reflect" / "SKILL.md",
    _DATA_DIR / "copilot" / "plugin" / "skills" / "trw-reflect" / "SKILL.md",
)

# Contract strings every client copy must retain (behavior, not existence).
_CONTRACT_MARKERS: tuple[str, ...] = (
    ".trw/reflections/",  # FR05 ledger path
    "approval",  # FR04 approval gate
    "quick-fix",  # FR04 route 1
    "trw_learn",  # FR04 route 3
    "improvement-backlog.md",  # FR03 dedup + FR04 route 4
    "CONSTITUTION.md",  # FR06 canon-protection guardrail
    "recurred",  # FR05 recurrence escalation
)


@pytest.mark.parametrize("skill_md", _BUNDLED_REFLECT_SOURCES, ids=lambda p: str(p.relative_to(_DATA_DIR)))
def test_bundled_reflect_copy_is_valid_and_carries_contract(skill_md: Path) -> None:
    """Each bundled trw-reflect copy validates and keeps the behavioral contract."""
    assert skill_md.exists(), f"trw-reflect SKILL.md missing at {skill_md}"
    content = skill_md.read_text(encoding="utf-8")
    result = validate_skill_markdown(content, path=skill_md, mode="compat")
    assert result.ok, f"SKILL.md failed validation: {[e.reason for e in result.errors]}"
    assert result.manifest is not None
    assert result.manifest.name == "trw-reflect"
    for marker in _CONTRACT_MARKERS:
        assert marker in content, f"trw-reflect at {skill_md} lost contract marker {marker!r}"


def test_reflect_in_cursor_ide_curated_list() -> None:
    """The cursor-IDE curated skill list distributes trw-reflect."""
    assert "trw-reflect" in _IDE_CURATED_SKILLS


def test_reflect_native_command_surfaces() -> None:
    """Clients with a native command surface ship a trw-reflect command.

    OpenCode commands (.opencode/commands) and cursor-IDE commands
    (.cursor/commands via _TRW_COMMANDS + template) are the two command
    surfaces the bootstrap installs today; both must carry trw-reflect and
    point at the skill workflow rather than restating it.
    """
    from trw_mcp.bootstrap._cursor_ide import _TRW_COMMANDS

    opencode_cmd = _DATA_DIR / "opencode" / "commands" / "trw-reflect.md"
    assert opencode_cmd.exists(), "opencode trw-reflect command missing"
    assert "trw-reflect" in opencode_cmd.read_text(encoding="utf-8")

    cursor_template = _DATA_DIR / "cursor_ide" / "commands" / "trw-reflect.md"
    assert cursor_template.exists(), "cursor-IDE trw-reflect command template missing"
    assert "/trw-reflect" in cursor_template.read_text(encoding="utf-8")
    assert "trw-reflect" in {name for name, _ in _TRW_COMMANDS}


def test_client_variants_drop_claude_specific_tool_names() -> None:
    """Codex/copilot/opencode variants must not name Claude-only tools."""
    for client in ("codex", "copilot", "opencode"):
        content = (_DATA_DIR / client / "skills" / "trw-reflect" / "SKILL.md").read_text(encoding="utf-8")
        assert "AskUserQuestion" not in content, f"{client} variant names Claude-specific AskUserQuestion"
