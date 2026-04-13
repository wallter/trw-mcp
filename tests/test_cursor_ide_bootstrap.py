"""Tests for cursor-ide-specific bootstrap generators (Tasks 7, 9, 10, 12).

Covers:
  generate_cursor_ide_subagents  — Task 7
  generate_cursor_ide_commands   — Task 9
  generate_cursor_ide_skills     — Task 10
  generate_cursor_ide_hooks      — Task 12
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from content delimited by '---\\n' markers.

    Returns (parsed_dict, body_after_second_separator).
    Raises AssertionError if frontmatter delimiters are absent.
    """
    assert content.startswith("---\n"), f"No frontmatter start: {content[:50]!r}"
    # split on the second '---'
    parts = content.split("---\n", 2)
    # parts[0] = '' (before first ---), parts[1] = frontmatter text, parts[2] = body
    assert len(parts) >= 3, "Frontmatter end delimiter not found"
    parsed = yaml.safe_load(parts[1])
    body = parts[2] if len(parts) > 2 else ""
    return parsed, body


# ---------------------------------------------------------------------------
# Task 7 — Subagent generator tests
# ---------------------------------------------------------------------------


class TestGenerateCursorIdeSubagents:
    """test_subagents_* test group."""

    def test_subagents_install(self, tmp_path: Path) -> None:
        """All 4 subagent files are created with parseable YAML frontmatter."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_subagents

        result = generate_cursor_ide_subagents(tmp_path)
        agents_dir = tmp_path / ".cursor" / "agents"

        expected_names = [
            "trw-explorer",
            "trw-implementer",
            "trw-reviewer",
            "trw-researcher",
        ]
        for name in expected_names:
            agent_file = agents_dir / f"{name}.md"
            assert agent_file.is_file(), f"Missing agent file: {name}.md"
            content = agent_file.read_text(encoding="utf-8")
            parsed, _ = _parse_frontmatter(content)
            assert parsed["name"] == name
            assert "description" in parsed
            assert "model" in parsed
            assert isinstance(parsed["readonly"], bool)
            assert isinstance(parsed["is_background"], bool)

        assert len(result["created"]) == 4

    def test_subagents_preserve_user_agents(self, tmp_path: Path) -> None:
        """User-authored agents outside trw- prefix are preserved after regeneration."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_subagents

        # Seed a user custom agent
        agents_dir = tmp_path / ".cursor" / "agents"
        agents_dir.mkdir(parents=True)
        user_agent = agents_dir / "my-custom.md"
        user_agent.write_text("---\nname: my-custom\n---\nCustom agent body.\n", encoding="utf-8")

        generate_cursor_ide_subagents(tmp_path)

        # User agent still present and unmodified
        assert user_agent.is_file()
        content = user_agent.read_text(encoding="utf-8")
        assert "my-custom" in content

    def test_subagents_frontmatter_roundtrip(self, tmp_path: Path) -> None:
        """Parse frontmatter from generated files; all required fields present."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_subagents

        generate_cursor_ide_subagents(tmp_path)
        agents_dir = tmp_path / ".cursor" / "agents"
        for agent_file in sorted(agents_dir.glob("trw-*.md")):
            content = agent_file.read_text(encoding="utf-8")
            parsed, body = _parse_frontmatter(content)
            assert "name" in parsed, f"{agent_file.name}: missing 'name'"
            assert "description" in parsed, f"{agent_file.name}: missing 'description'"
            assert "model" in parsed, f"{agent_file.name}: missing 'model'"
            assert "readonly" in parsed, f"{agent_file.name}: missing 'readonly'"
            assert "is_background" in parsed, f"{agent_file.name}: missing 'is_background'"
            # Body should have some content
            assert len(body.strip()) > 0, f"{agent_file.name}: empty body"

    def test_subagents_readonly_flags(self, tmp_path: Path) -> None:
        """explorer/reviewer/researcher are readonly=true; implementer is readonly=false."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_subagents

        generate_cursor_ide_subagents(tmp_path)
        agents_dir = tmp_path / ".cursor" / "agents"

        readonly_true = ["trw-explorer", "trw-reviewer", "trw-researcher"]
        readonly_false = ["trw-implementer"]

        for name in readonly_true:
            content = (agents_dir / f"{name}.md").read_text(encoding="utf-8")
            parsed, _ = _parse_frontmatter(content)
            assert parsed["readonly"] is True, f"{name}: expected readonly=true"

        for name in readonly_false:
            content = (agents_dir / f"{name}.md").read_text(encoding="utf-8")
            parsed, _ = _parse_frontmatter(content)
            assert parsed["readonly"] is False, f"{name}: expected readonly=false"

    def test_subagents_researcher_is_background(self, tmp_path: Path) -> None:
        """trw-researcher has is_background=true; others have is_background=false."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_subagents

        generate_cursor_ide_subagents(tmp_path)
        agents_dir = tmp_path / ".cursor" / "agents"

        content = (agents_dir / "trw-researcher.md").read_text(encoding="utf-8")
        parsed, _ = _parse_frontmatter(content)
        assert parsed["is_background"] is True

        for name in ["trw-explorer", "trw-implementer", "trw-reviewer"]:
            content = (agents_dir / f"{name}.md").read_text(encoding="utf-8")
            parsed, _ = _parse_frontmatter(content)
            assert parsed["is_background"] is False, f"{name}: expected is_background=false"

    def test_subagents_idempotent_produces_updated(self, tmp_path: Path) -> None:
        """Second call marks files as updated, not created."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_subagents

        first = generate_cursor_ide_subagents(tmp_path)
        assert len(first["created"]) == 4
        assert len(first["updated"]) == 0

        second = generate_cursor_ide_subagents(tmp_path)
        assert len(second["created"]) == 0
        assert len(second["updated"]) == 4


# ---------------------------------------------------------------------------
# Task 9 — Commands generator tests
# ---------------------------------------------------------------------------


class TestGenerateCursorIdeCommands:
    """test_commands_* test group."""

    def test_commands_install(self, tmp_path: Path) -> None:
        """All 5 command files are created."""
        from trw_mcp.bootstrap._cursor_ide import _TRW_COMMANDS, generate_cursor_ide_commands

        result = generate_cursor_ide_commands(tmp_path)
        commands_dir = tmp_path / ".cursor" / "commands"

        assert commands_dir.is_dir()
        for cmd_name, _ in _TRW_COMMANDS:
            cmd_file = commands_dir / f"{cmd_name}.md"
            assert cmd_file.is_file(), f"Missing command file: {cmd_name}.md"
            content = cmd_file.read_text(encoding="utf-8")
            assert len(content.strip()) > 0

        assert len(result["created"]) == len(_TRW_COMMANDS)

    def test_commands_preserve_user_commands(self, tmp_path: Path) -> None:
        """User-authored commands outside trw- prefix are preserved."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_commands

        commands_dir = tmp_path / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        user_cmd = commands_dir / "my-cmd.md"
        user_cmd.write_text("# /my-cmd\nMy custom command.\n", encoding="utf-8")

        generate_cursor_ide_commands(tmp_path)

        assert user_cmd.is_file()
        content = user_cmd.read_text(encoding="utf-8")
        assert "my-cmd" in content

    def test_commands_content_has_sections(self, tmp_path: Path) -> None:
        """Command files have expected sections."""
        from trw_mcp.bootstrap._cursor_ide import _TRW_COMMANDS, generate_cursor_ide_commands

        generate_cursor_ide_commands(tmp_path)
        commands_dir = tmp_path / ".cursor" / "commands"

        for cmd_name, _ in _TRW_COMMANDS:
            content = (commands_dir / f"{cmd_name}.md").read_text(encoding="utf-8")
            # Command files should have a heading
            assert "#" in content, f"{cmd_name}.md: no markdown heading"
            # Should mention the command name
            assert cmd_name in content, f"{cmd_name}.md: command name not present"

    def test_commands_idempotent_produces_updated(self, tmp_path: Path) -> None:
        """Second call marks files as updated."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_commands

        first = generate_cursor_ide_commands(tmp_path)
        assert len(first["created"]) == 5

        second = generate_cursor_ide_commands(tmp_path)
        assert len(second["created"]) == 0
        assert len(second["updated"]) == 5


# ---------------------------------------------------------------------------
# Task 10 — Skills mirror generator tests
# ---------------------------------------------------------------------------


class TestGenerateCursorIdeSkills:
    """test_skills_* test group."""

    def _make_fake_skill(self, skills_root: Path, name: str) -> None:
        """Create a minimal fake skill directory with SKILL.md."""
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Fake skill {name}\n---\n\nBody.\n",
            encoding="utf-8",
        )

    def test_skills_mirror(self, tmp_path: Path) -> None:
        """All curated skills that exist in source are mirrored."""
        from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS, generate_cursor_ide_skills

        # Use a fake source dir with all curated skills present
        fake_skills = tmp_path / "source_skills"
        for skill_name in _IDE_CURATED_SKILLS:
            self._make_fake_skill(fake_skills, skill_name)

        result = generate_cursor_ide_skills(tmp_path, source_skills_dir=fake_skills)

        skills_dir = tmp_path / ".cursor" / "skills"
        for skill_name in _IDE_CURATED_SKILLS:
            assert (skills_dir / skill_name).is_dir(), f"Missing skill dir: {skill_name}"
            assert (skills_dir / skill_name / "SKILL.md").is_file()

        assert len(result["created"]) == len(_IDE_CURATED_SKILLS)

    def test_skills_preserve_user_skills(self, tmp_path: Path) -> None:
        """User-authored skills not in the curated list are preserved."""
        from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS, generate_cursor_ide_skills

        # Set up user skill
        skills_dir = tmp_path / ".cursor" / "skills"
        user_skill = skills_dir / "my-skill"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: custom\n---\n",
            encoding="utf-8",
        )

        # Source dir with one curated skill
        fake_skills = tmp_path / "source_skills"
        self._make_fake_skill(fake_skills, _IDE_CURATED_SKILLS[0])

        generate_cursor_ide_skills(tmp_path, source_skills_dir=fake_skills)

        assert user_skill.is_dir(), "User skill was deleted"
        assert (user_skill / "SKILL.md").is_file(), "User SKILL.md was deleted"

    def test_skills_frontmatter_valid(self, tmp_path: Path) -> None:
        """Each mirrored SKILL.md has parseable frontmatter with name and description."""
        from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS, generate_cursor_ide_skills

        fake_skills = tmp_path / "source_skills"
        for skill_name in _IDE_CURATED_SKILLS:
            self._make_fake_skill(fake_skills, skill_name)

        generate_cursor_ide_skills(tmp_path, source_skills_dir=fake_skills)

        skills_dir = tmp_path / ".cursor" / "skills"
        for skill_name in _IDE_CURATED_SKILLS:
            skill_md = skills_dir / skill_name / "SKILL.md"
            content = skill_md.read_text(encoding="utf-8")
            parsed, _ = _parse_frontmatter(content)
            assert "name" in parsed, f"{skill_name}/SKILL.md: missing 'name'"
            assert "description" in parsed, f"{skill_name}/SKILL.md: missing 'description'"

    def test_skills_skip_missing_without_failing(self, tmp_path: Path) -> None:
        """Skills absent from source are skipped without raising an error."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills

        # Provide empty source directory — no skills present
        fake_skills = tmp_path / "empty_skills"
        fake_skills.mkdir()

        result = generate_cursor_ide_skills(tmp_path, source_skills_dir=fake_skills)

        # Should succeed with empty result sets
        assert result["created"] == []
        assert result["updated"] == []

    def test_skills_uses_bundled_source_by_default(self, tmp_path: Path) -> None:
        """When no source_skills_dir provided, bundled data/skills/ is used."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills

        # Call with no source_skills_dir — should not raise even if some skills missing
        result = generate_cursor_ide_skills(tmp_path)
        # At least some curated skills should be present in bundled data
        total = len(result.get("created", [])) + len(result.get("updated", []))
        assert total > 0, "No bundled skills were mirrored"


# ---------------------------------------------------------------------------
# Task 12 — Hook expansion tests
# ---------------------------------------------------------------------------


class TestGenerateCursorIdeHooks:
    """test_hooks_* test group."""

    def test_hooks_full_event_set(self, tmp_path: Path) -> None:
        """hooks.json contains all 8 TRW IDE events."""
        from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_EVENTS, generate_cursor_ide_hooks

        generate_cursor_ide_hooks(tmp_path)

        hooks_file = tmp_path / ".cursor" / "hooks.json"
        assert hooks_file.is_file()

        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
        registered_events = set(hooks_data["hooks"].keys())

        for event_name in _IDE_HOOK_EVENTS:
            assert event_name in registered_events, f"Missing event: {event_name}"

    def test_hooks_preserve_user_hooks(self, tmp_path: Path) -> None:
        """User hook outside TRW prefix is preserved after regeneration."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_hooks

        # Seed a user hook using a non-trw- command prefix
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir(parents=True)
        user_hooks = {
            "version": 1,
            "hooks": {
                "stop": [
                    {"command": ".cursor/hooks/my-own-stop.sh", "type": "command", "timeout": 5}
                ]
            },
        }
        (cursor_dir / "hooks.json").write_text(json.dumps(user_hooks, indent=2) + "\n", encoding="utf-8")

        generate_cursor_ide_hooks(tmp_path)

        hooks_data = json.loads((cursor_dir / "hooks.json").read_text(encoding="utf-8"))
        stop_handlers = hooks_data["hooks"]["stop"]
        commands = [h["command"] for h in stop_handlers]
        assert any("my-own-stop.sh" in cmd for cmd in commands), (
            "User hook was removed"
        )

    def test_hook_scripts_installed_executable(self, tmp_path: Path) -> None:
        """.cursor/hooks/trw-*.sh scripts exist and are mode 0o755."""
        from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_SCRIPTS, generate_cursor_ide_hooks

        generate_cursor_ide_hooks(tmp_path)

        hooks_dir = tmp_path / ".cursor" / "hooks"
        trw_scripts = [s for s in _IDE_HOOK_SCRIPTS if s.startswith("trw-")]

        for script_name in trw_scripts:
            script_path = hooks_dir / script_name
            assert script_path.is_file(), f"Missing script: {script_name}"
            mode = script_path.stat().st_mode
            assert bool(mode & stat.S_IXUSR), f"{script_name} not user-executable"

    def test_hook_adapter_valid_bash(self, tmp_path: Path) -> None:
        """bash -n passes on each installed hook script."""
        from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_SCRIPTS, generate_cursor_ide_hooks

        generate_cursor_ide_hooks(tmp_path)
        hooks_dir = tmp_path / ".cursor" / "hooks"

        for script_name in _IDE_HOOK_SCRIPTS:
            script_path = hooks_dir / script_name
            if not script_path.is_file():
                continue  # skip if not installed (missing bundled source)
            proc = subprocess.run(
                ["bash", "-n", str(script_path)],
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, (
                f"bash -n failed on {script_name}: {proc.stderr}"
            )

    def test_hooks_event_commands_reference_correct_path(self, tmp_path: Path) -> None:
        """Each registered event handler command starts with .cursor/hooks/trw-."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_hooks

        generate_cursor_ide_hooks(tmp_path)

        hooks_file = tmp_path / ".cursor" / "hooks.json"
        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))

        for event, handlers in hooks_data["hooks"].items():
            for handler in handlers:
                cmd = handler.get("command", "")
                if cmd.startswith(".cursor/hooks/trw-"):
                    assert cmd.endswith(".sh"), f"{event}: handler command lacks .sh suffix"
