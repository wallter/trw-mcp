"""Tests for cursor-ide-specific bootstrap generators (Tasks 7, 9, 10, 12).

Covers:
  generate_cursor_ide_subagents  — Task 7
  generate_cursor_ide_commands   — Task 9
  generate_cursor_ide_skills     — Task 10
  generate_cursor_ide_hooks      — Task 12
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

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


class TestCursorIdeSubagents:
    """Cursor IDE subagent installation, after PRD-CORE-252-FR04.

    ``generate_cursor_ide_subagents``, ``cursor_ide_agent_contents`` and the
    ``_TRW_SUBAGENTS`` description list are retired: four hand-written stubs
    became the whole bundled specialist set, translated into Cursor's format.
    ``TestGenerateCursorIdeSubagents`` and
    ``TestFallbackTemplateBehavior::test_subagents_fallback_when_template_missing``
    were deleted with them — the latter tested a fallback for a bundled template
    directory that no longer exists. The properties worth keeping are asserted
    here on the real path; the format contract itself lives in
    ``tests/test_agent_materialization_per_client.py``.
    """

    def _install(self, tmp_path: Path) -> dict[str, list[str]]:
        from trw_mcp.bootstrap._init_project_skills import _install_agents

        result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
        _install_agents(tmp_path, force=False, result=result, clients=["cursor-ide"])
        return result

    def test_subagents_install_with_parseable_frontmatter(self, tmp_path: Path) -> None:
        import yaml

        result = self._install(tmp_path)
        assert not result["errors"], result["errors"]

        agents_dir = tmp_path / ".cursor" / "agents"
        installed = sorted(agents_dir.glob("*.md"))
        assert installed, "no cursor agents installed"
        for path in installed:
            text = path.read_text(encoding="utf-8")
            assert text.startswith("---\n")
            block = text[4:].split("\n---\n", 1)[0]
            parsed = yaml.safe_load(block)
            assert parsed["name"] == path.stem
            assert parsed["model"] == "inherit", "cursor agents inherit the active model"
            assert isinstance(parsed["readonly"], bool)

    def test_subagents_carry_no_claude_code_tool_namespace(self, tmp_path: Path) -> None:
        """US-1: no file may contain the ``mcp__trw__`` prefix."""
        self._install(tmp_path)
        for path in sorted((tmp_path / ".cursor" / "agents").glob("*.md")):
            assert "mcp__trw__" not in path.read_text(encoding="utf-8"), path.name

    def test_subagents_preserve_user_agents(self, tmp_path: Path) -> None:
        """A user-authored neighbour in `.cursor/agents/` is never touched."""
        self._install(tmp_path)
        mine = tmp_path / ".cursor" / "agents" / "my-agent.md"
        mine.write_text("# mine\n", encoding="utf-8")

        self._install(tmp_path)

        assert mine.read_text(encoding="utf-8") == "# mine\n"

    def test_subagents_idempotent(self, tmp_path: Path) -> None:
        first = self._install(tmp_path)
        assert first["created"]
        second = self._install(tmp_path)
        assert second["created"] == []
        assert len(second["skipped"]) == len(first["created"])


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
        from trw_mcp.bootstrap._cursor_ide import _TRW_COMMANDS, generate_cursor_ide_commands

        first = generate_cursor_ide_commands(tmp_path)
        assert len(first["created"]) == len(_TRW_COMMANDS)

        second = generate_cursor_ide_commands(tmp_path)
        assert len(second["created"]) == 0
        assert len(second["updated"]) == len(_TRW_COMMANDS)


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
            "hooks": {"stop": [{"command": ".cursor/hooks/my-own-stop.sh", "type": "command", "timeout": 5}]},
        }
        (cursor_dir / "hooks.json").write_text(json.dumps(user_hooks, indent=2) + "\n", encoding="utf-8")

        generate_cursor_ide_hooks(tmp_path)

        hooks_data = json.loads((cursor_dir / "hooks.json").read_text(encoding="utf-8"))
        stop_handlers = hooks_data["hooks"]["stop"]
        commands = [h["command"] for h in stop_handlers]
        assert any("my-own-stop.sh" in cmd for cmd in commands), "User hook was removed"

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
            if script_path.suffix == ".py":
                # Python helpers ship alongside the shell hooks; syntax-check them
                # with the interpreter the hooks invoke them with.
                cmd = [sys.executable, "-m", "py_compile", str(script_path)]
            else:
                cmd = ["bash", "-n", str(script_path)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            assert proc.returncode == 0, f"syntax check failed on {script_name}: {proc.stderr}"

    def test_every_helper_a_hook_invokes_is_deployed(self, tmp_path: Path) -> None:
        """Deployment closure: a helper a shipped hook executes must itself ship.

        Regression for the 2026-09-03 adapter diagnostic (F1): trw-session-start.sh,
        trw-pre-compact.sh and trw-stop.sh run ``python3 "${_SCRIPT_DIR}/_nudge_gate.py"``
        but the helper was absent from ``_IDE_HOOK_SCRIPTS``, so every installed hook
        died with FileNotFoundError under ``set -euo pipefail``.
        """
        import re

        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_hooks

        generate_cursor_ide_hooks(tmp_path)
        hooks_dir = tmp_path / ".cursor" / "hooks"
        invoked = re.compile(r'"\$\{_SCRIPT_DIR\}/([A-Za-z0-9_.-]+)"')
        for script_path in hooks_dir.glob("*.sh"):
            for helper in invoked.findall(script_path.read_text(encoding="utf-8")):
                assert (hooks_dir / helper).is_file(), (
                    f"{script_path.name} invokes {helper}, which the installer did not deploy"
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

    def test_hooks_beforeMCPExecution_fail_closed_false(self, tmp_path: Path) -> None:
        """beforeMCPExecution handler has failClosed=False (advisory, not blocking)."""
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_hooks

        generate_cursor_ide_hooks(tmp_path)

        hooks_file = tmp_path / ".cursor" / "hooks.json"
        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
        before_mcp = hooks_data["hooks"].get("beforeMCPExecution", [])
        assert len(before_mcp) > 0, "beforeMCPExecution has no handlers"
        # Verify failClosed is explicitly false — this is a critical contract
        assert before_mcp[0].get("failClosed") is False, (
            "beforeMCPExecution must have failClosed=False (advisory, not blocking)"
        )


# ---------------------------------------------------------------------------
# Fallback template coverage
# ---------------------------------------------------------------------------


class TestFallbackTemplateBehavior:
    """Verify fallback behavior when bundled templates are absent.

    ``test_subagents_fallback_when_template_missing`` was deleted by
    PRD-CORE-252-FR04: it covered a fallback body for the retired
    ``data/cursor_ide/agents`` template directory, which no longer exists.
    Cursor's agents come from the shared bundle, whose absence is handled by
    ``_install_agents``' per-agent error isolation and asserted in
    ``tests/test_install_agents_destinations.py::test_per_agent_and_per_client_failures_are_isolated``.
    """

    def test_commands_fallback_when_template_missing(self, tmp_path: Path) -> None:
        """generate_cursor_ide_commands falls back to inline body when template absent."""
        from unittest.mock import patch

        from trw_mcp.bootstrap._cursor_ide import _TRW_COMMANDS, generate_cursor_ide_commands

        class _FakeTraversable:
            def joinpath(self, name: str) -> _FakeTraversable:
                return self

            def read_text(self, encoding: str = "utf-8") -> str:
                raise FileNotFoundError("bundled template missing")

        with patch("trw_mcp.bootstrap._cursor_ide._pkg_files", return_value=_FakeTraversable()):
            result = generate_cursor_ide_commands(tmp_path)

        commands_dir = tmp_path / ".cursor" / "commands"
        assert len(result["created"]) == len(_TRW_COMMANDS)
        for cmd_name, _ in _TRW_COMMANDS:
            cmd_file = commands_dir / f"{cmd_name}.md"
            assert cmd_file.is_file(), f"Command file missing: {cmd_name}.md"
            content = cmd_file.read_text(encoding="utf-8")
            # Inline fallback body should contain the command name and a heading
            assert "#" in content
            assert cmd_name in content
