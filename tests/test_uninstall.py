"""Tests for the ``trw-mcp uninstall`` CLI subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.server._subcommands import _run_uninstall


@pytest.mark.unit
class TestUninstall:
    """Unit tests for _run_uninstall handler."""

    def test_dry_run_lists_files(self, tmp_path: Path) -> None:
        """Dry run lists TRW files without deleting."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("test: true")
        (tmp_path / ".mcp.json").write_text("{}")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=True, yes=False)
        _run_uninstall(args)

        assert (tmp_path / ".trw").exists()  # Not deleted
        assert (tmp_path / ".mcp.json").exists()

    def test_yes_removes_files(self, tmp_path: Path) -> None:
        """With --yes, removes files without prompting.

        ``.mcp.json`` is a merged server map, so uninstall withdraws TRW's
        entry rather than deleting the file — it belongs to the user's client
        and may hold servers TRW never wrote. This assertion previously
        expected deletion, which is what made the data-loss defect invisible.
        """
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".mcp.json").write_text("{}")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (tmp_path / ".trw").exists()
        assert (tmp_path / ".mcp.json").exists()

    def test_no_trw_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty project prints no-files message."""
        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=False)
        _run_uninstall(args)
        assert "No TRW files found" in capsys.readouterr().out

    def test_partial_removal(self, tmp_path: Path) -> None:
        """Only removes files that exist."""
        (tmp_path / ".trw").mkdir()  # Only .trw, no .mcp.json

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (tmp_path / ".trw").exists()

    def test_claude_subdirs_kept_without_a_manifest_recording_them(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR09 C3: with no manifest, nothing under .claude is guessed at.

        This project has ``.claude/skills``, ``.claude/agents`` and
        ``.claude/hooks`` on disk but never went through ``init_project`` --
        there is no ``.trw/managed-artifacts.yaml`` recording any of it as
        TRW's own unedited write. Rule 3 keeps every such directory whole
        rather than wholesale-deleting it on the assumption that a
        ``.claude/`` client surface must be TRW's; the positive
        "removed when the manifest actually covers it" path is
        ``tests/test_uninstall_ide_manifest.py``.
        """
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "skills").mkdir()
        (claude_dir / "skills" / "trw-review-pr").mkdir(parents=True)
        (claude_dir / "skills" / "trw-review-pr" / "SKILL.md").write_text("# Skill")
        (claude_dir / "agents").mkdir()
        (claude_dir / "agents" / "reviewer.md").write_text("# Agent")
        (claude_dir / "hooks").mkdir()
        (claude_dir / "hooks" / "lib-trw.sh").write_text("#!/bin/bash")
        # A genuinely user-owned file in .claude/ that TRW never writes.
        # (``settings.json`` is NOT such a file — bootstrap merges TRW hook
        # entries into it; see TestUninstallClaudeSettings.)
        (claude_dir / "notes.md").write_text("my notes")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert (claude_dir / "skills").exists()
        assert (claude_dir / "agents").exists()
        assert (claude_dir / "hooks").exists()
        # .claude/ itself and user files preserved
        assert claude_dir.exists()
        assert (claude_dir / "notes.md").exists()

    def test_default_target_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Defaults to current directory when target_dir is '.'."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".trw").mkdir()

        args = argparse.Namespace(target_dir=".", dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (tmp_path / ".trw").exists()

    def test_dry_run_shows_file_count(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Dry run shows directory file count in output."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text("key: val")
        (trw_dir / "index.yaml").write_text("entries: []")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=True, yes=False)
        _run_uninstall(args)

        out = capsys.readouterr().out
        assert "2 files" in out
        assert "--dry-run" in out


def _trw_server(root: Path) -> dict[str, object]:
    """The ``trw`` server entry TRW's .mcp.json writer generates for *root*."""
    from trw_mcp.bootstrap._utils import _trw_mcp_server_entry

    return _trw_mcp_server_entry(root)


def _trw_codex_table(root: Path) -> str:
    """The ``[mcp_servers.trw]`` table (nested tool tables included) TRW generates for *root*."""
    from trw_mcp.bootstrap._codex import merge_codex_config
    from trw_mcp.bootstrap._codex_toml import _toml_dumps

    table = merge_codex_config({}, target_dir=root)["mcp_servers"]["trw"]
    return _toml_dumps({"mcp_servers": {"trw": table}}).removeprefix("[mcp_servers]\n\n")


def _ns(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    """Build an uninstall argparse Namespace with sensible defaults."""
    base: dict[str, object] = {
        "target_dir": str(tmp_path),
        "dry_run": False,
        "yes": True,
        "delete_memory": False,
        "keep_memory": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.mark.integration
class TestUninstallRegistryProfiles:
    """PRD-SEC-006 FR07: uninstall is registry-driven across all 8 profiles."""

    def test_profile_config_dirs_without_a_manifest_are_kept(self, tmp_path: Path) -> None:
        """Registry-driven discovery still finds every profile's surface (PRD-SEC-006 FR07),
        but PRD-INFRA-192 FR09 C3 keeps each one: none of this project's surfaces are
        recorded in a manifest, so TRW cannot prove any of it is its own unedited
        write and never guesses. Positive removal-when-covered path:
        ``tests/test_uninstall_ide_manifest.py``.

        Merged config files (.codex/config.toml, .cursor/mcp.json) are
        EXCLUDED here — they are key-stripped, not wholesale-deleted (sec-006);
        see TestUninstall*merged_config* tests for that behavior.
        """
        # One representative surface per non-claude profile (excluding merged
        # config files which are tested separately).
        surfaces = [
            tmp_path / ".opencode" / "agents",
            tmp_path / ".cursor" / "rules",
            tmp_path / ".github" / "agents",
            tmp_path / ".aider.conf.yml",
            tmp_path / ".antigravitycli" / "agents",
        ]
        for s in surfaces:
            if s.suffix:  # file
                s.parent.mkdir(parents=True, exist_ok=True)
                s.write_text("{}")
            else:  # dir
                s.mkdir(parents=True, exist_ok=True)
                (s / "marker.txt").write_text("x")

        _run_uninstall(_ns(tmp_path))

        for s in surfaces:
            assert s.exists(), f"{s} has no manifest record and must be kept, not guessed at"

    def test_merged_config_files_not_wholesale_deleted(self, tmp_path: Path) -> None:
        """sec-006: merged config files (settings.json/config.toml) are NOT deleted.

        They may contain user-owned settings; only the TRW server entry is
        stripped. A file with no TRW entry is preserved verbatim.
        """
        cursor = tmp_path / ".cursor" / "mcp.json"
        cursor.parent.mkdir(parents=True)
        cursor.write_text('{"theme": "dark"}')  # pure user content, no trw key
        codex = tmp_path / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text('model = "gpt-5"\n')  # pure user content, no trw table
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        # Both preserved because neither holds a TRW server entry.
        assert cursor.exists(), "user cursor mcp.json wholesale-deleted"
        assert '"theme": "dark"' in cursor.read_text()
        assert codex.exists(), "user codex config.toml wholesale-deleted"
        assert 'model = "gpt-5"' in codex.read_text()

    def test_merged_config_strips_only_trw_server_entry(self, tmp_path: Path) -> None:
        """The trw mcp server entry is stripped, user servers/keys preserved."""
        import json

        cursor = tmp_path / ".cursor" / "mcp.json"
        cursor.parent.mkdir(parents=True)
        cursor.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {
                        "trw": _trw_server(tmp_path),
                        "other": {"command": "other-server"},
                    },
                },
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert cursor.exists()
        data = json.loads(cursor.read_text())
        assert data["theme"] == "dark"
        assert "trw" not in data.get("mcpServers", {})
        assert "other" in data["mcpServers"]

    def test_merged_config_strips_codex_trw_table(self, tmp_path: Path) -> None:
        """Codex config.toml [mcp_servers.trw] table stripped, rest preserved."""
        codex = tmp_path / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text(
            'model = "gpt-5"\n\n' + _trw_codex_table(tmp_path) + '\n[mcp_servers.other]\ncommand = "other"\n'
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert codex.exists()
        text = codex.read_text()
        assert 'model = "gpt-5"' in text
        assert "mcp_servers.trw" not in text
        assert "mcp_servers.other" in text

    def test_does_not_remove_unmanaged_files(self, tmp_path: Path) -> None:
        """Files TRW never created are left untouched."""
        (tmp_path / ".trw").mkdir()
        user_file = tmp_path / "README.md"
        user_file.write_text("user content")
        user_cursor_file = tmp_path / ".cursor" / "user-notes.md"
        user_cursor_file.parent.mkdir()
        user_cursor_file.write_text("notes")

        _run_uninstall(_ns(tmp_path))

        assert user_file.exists()
        assert user_file.read_text() == "user content"
        assert user_cursor_file.exists()

    def test_removes_managed_block_preserves_user_content(self, tmp_path: Path) -> None:
        """Shared AGENTS.md keeps user content; only the TRW block is stripped."""
        agents = tmp_path / "AGENTS.md"
        agents.write_text(
            "# My Project\n\nUser instructions here.\n\n"
            "<!-- trw:start -->\nTRW auto-generated block\n<!-- trw:end -->\n\n"
            "More user notes.\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert agents.exists()
        text = agents.read_text()
        assert "User instructions here." in text
        assert "More user notes." in text
        assert "trw:start" not in text
        assert "TRW auto-generated block" not in text

    def test_managed_block_file_with_no_trw_block_untouched(self, tmp_path: Path) -> None:
        """A REGISTERED shared file that has no TRW markers is left alone.

        Uses AGENTS.md (a registered managed-block surface) deliberately: an
        unregistered path is untouched for the trivial reason that uninstall
        never looks at it, so it proves nothing about marker handling. The
        sibling test above proves AGENTS.md *is* processed when markers exist,
        which is what makes this assertion non-vacuous.
        """
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# Pure user AGENTS.md\nno trw markers\n")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert agents.exists()
        assert agents.read_text() == "# Pure user AGENTS.md\nno trw markers\n"

    def test_managed_block_only_file_is_deleted(self, tmp_path: Path) -> None:
        """If stripping the TRW block empties the file, the file is removed."""
        agents = tmp_path / "AGENTS.md"
        agents.write_text("<!-- trw:start -->\nonly trw\n<!-- trw:end -->\n")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not agents.exists()

    def test_dry_run_does_not_strip_managed_block(self, tmp_path: Path) -> None:
        """Dry run leaves managed-block files unchanged."""
        agents = tmp_path / "AGENTS.md"
        original = "user\n<!-- trw:start -->\ntrw\n<!-- trw:end -->\n"
        agents.write_text(original)
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path, dry_run=True))

        assert agents.read_text() == original


@pytest.mark.integration
class TestUninstallHookGroupAndMergedSurfaces:
    """FIX 1-4: hook-group merged files, antigravity, missing dirs, cursor mcp."""

    def test_codex_hooks_json_strips_trw_group_keeps_user(self, tmp_path: Path) -> None:
        """FIX 1 / PRD-INFRA-192 FR09 P0: a TRW COMMAND inside a group survives
        alongside a user command in the SAME group; only the verified TRW
        command is removed, never the whole group by its description tag.

        Built from the real codex payload (:func:`_codex_hooks_payload`) so the
        TRW command is one the verified predicate actually recognizes -- a
        fabricated placeholder command (the old fixture used ``"trw"``) can
        never be identified as TRW's own under the new per-command rule.
        """
        import json

        from trw_mcp.bootstrap._codex_hooks import _codex_hooks_payload

        real_group = _codex_hooks_payload()["hooks"]["SessionStart"][0]
        real_command = real_group["hooks"][0]["command"]

        hooks = tmp_path / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            dict(real_group),
                            {
                                "description": "My custom hook",
                                "hooks": [{"type": "command", "command": "mine"}],
                            },
                        ]
                    }
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists(), "hooks.json with a user group must be preserved"
        data = json.loads(hooks.read_text())
        groups = data["hooks"]["SessionStart"]
        assert len(groups) == 1
        assert groups[0]["description"] == "My custom hook"
        commands = [h["command"] for g in groups for h in g["hooks"]]
        assert real_command not in commands

    def test_codex_hooks_json_all_trw_deleted(self, tmp_path: Path) -> None:
        """FIX 1: a hooks.json containing only real TRW commands is deleted."""
        import json

        from trw_mcp.bootstrap._codex_hooks import _codex_hooks_payload

        payload = _codex_hooks_payload()
        hooks = tmp_path / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {"hooks": {k: payload["hooks"][k] for k in ("SessionStart", "Stop")}},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not hooks.exists()

    def test_copilot_hooks_json_all_trw_deleted_with_version(self, tmp_path: Path) -> None:
        """FIX 3: copilot hooks.json (version + only real TRW commands) is deleted."""
        import json

        from trw_mcp.bootstrap._copilot import _copilot_hooks_payload

        payload = _copilot_hooks_payload()
        session_key = next(iter(payload["hooks"]))
        hooks = tmp_path / ".github" / "hooks" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {"version": 1, "hooks": {session_key: payload["hooks"][session_key]}},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not hooks.exists(), "version-only + only-TRW-commands is not user content"

    def test_copilot_hooks_json_preserves_user_group_and_unknown_keys(self, tmp_path: Path) -> None:
        """FIX 3: user groups and unknown top-level keys survive TRW-command stripping."""
        import json

        from trw_mcp.bootstrap._copilot import _copilot_hooks_payload

        payload = _copilot_hooks_payload()
        session_key = next(iter(payload["hooks"]))
        real_group = payload["hooks"][session_key][0]

        hooks = tmp_path / ".github" / "hooks" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {
                    "version": 1,
                    "customTop": {"keep": True},
                    "hooks": {
                        session_key: [
                            dict(real_group),
                            {"description": "user group", "hooks": [{"type": "command", "command": "u"}]},
                        ]
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists()
        data = json.loads(hooks.read_text())
        assert data["customTop"] == {"keep": True}
        groups = data["hooks"][session_key]
        assert len(groups) == 1
        assert groups[0]["description"] == "user group"

    def test_antigravity_preserves_user_files_strips_trw(self, tmp_path: Path) -> None:
        """FIX 2 + PRD-INFRA-192 FR09 C3: user files under .antigravitycli survive.

        ``.antigravitycli/agents`` has no manifest record in this hand-built
        project (it is also a legacy surface no writer produces any more --
        see ``_PLAIN_SURFACES_WITHOUT_A_CURRENT_PRODUCER``), so rule 3 keeps it
        whole rather than guessing; ``settings.json`` is a merged config and is
        stripped, never wholesale-deleted, whether or not a manifest exists.
        """
        import json

        ag = tmp_path / ".antigravitycli"
        agents = ag / "agents"
        agents.mkdir(parents=True)
        (agents / "trw-explorer.md").write_text("trw")
        settings = ag / "settings.json"
        settings.write_text(
            json.dumps({"mcpServers": {"trw": _trw_server(tmp_path), "mine": {"command": "m"}}}, indent=2) + "\n"
        )
        user_file = ag / "my-notes.md"
        user_file.write_text("keep me")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        # No manifest record for this legacy surface: kept, not guessed at.
        assert agents.exists()
        # settings.json preserved with only the trw server entry stripped
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert "trw" not in data["mcpServers"]
        assert "mine" in data["mcpServers"]
        # user file elsewhere under the dir survives (no rmtree of .antigravitycli)
        assert user_file.exists()
        assert ag.exists()

    def test_antigravity_uninstall_removes_live_ag03_hook(self, tmp_path: Path) -> None:
        """P1: install the real AG-03 hook via a real init, then uninstall must leave no live hook.

        Regression for the narrowed antigravity surface that dropped
        hooks.json + hooks/ cleanup while install_before_edit_hook still wrote
        (and registered) a PreToolUse hook -- uninstall left the hook live.

        Goes through ``init_project`` (rather than calling
        ``install_before_edit_hook`` directly against a bare ``.trw`` mkdir)
        so the manifest actually records the hook script's content hash --
        PRD-INFRA-192 FR09 C3 keeps an unrecorded plain surface whole, so
        without a real manifest this hook would now (correctly) survive.
        """
        from trw_mcp.bootstrap import init_project
        from trw_mcp.channels.antigravity._before_edit_hook import _AG03_HOOK_SCRIPT_PATH, AG03_HOOKS_PATH

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="antigravity-cli")
        assert not result["errors"], result["errors"]
        hooks_json = tmp_path / AG03_HOOKS_PATH
        hook_script = tmp_path / _AG03_HOOK_SCRIPT_PATH
        assert hooks_json.exists()
        assert hook_script.exists()

        _run_uninstall(_ns(tmp_path))

        # No live TRW hook may remain: both the registration file and the script.
        assert not hooks_json.exists(), "uninstall left the registered AG-03 hook behind"
        assert not hook_script.exists(), "uninstall left the AG-03 hook script behind"
        assert not (tmp_path / ".antigravitycli" / "hooks").exists()

    def test_github_skills_removed(self, tmp_path: Path) -> None:
        """FIX 3: .github/skills TRW artifacts are removed.

        Goes through a real ``init_project`` (PRD-INFRA-192 FR09 C3): a hand-
        built ``.github/skills`` fixture with no manifest record is now kept,
        not guessed at, so the removal path needs a real manifest to exercise.
        """
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="copilot")
        assert not result["errors"], result["errors"]
        assert (tmp_path / ".github" / "skills").is_dir(), "precondition: copilot skills installed"

        _run_uninstall(_ns(tmp_path))

        assert not (tmp_path / ".github" / "skills").exists()

    def test_github_instructions_only_trw_files_removed(self, tmp_path: Path) -> None:
        """FIX 3: only the specific TRW instruction files are removed; user file kept.

        Goes through a real ``init_project`` so the instruction files' content
        matches the manifest's recorded hashes (PRD-INFRA-192 FR09 C3) --
        placeholder ``"trw"`` fixture bytes have no bundled source to match.
        """
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="copilot")
        assert not result["errors"], result["errors"]
        instr = tmp_path / ".github" / "instructions"
        assert (instr / "python-testing.instructions.md").is_file(), "precondition"
        assert (instr / "typescript-react.instructions.md").is_file(), "precondition"
        user = instr / "my-own.instructions.md"
        user.write_text("mine")

        _run_uninstall(_ns(tmp_path))

        assert not (instr / "python-testing.instructions.md").exists()
        assert not (instr / "typescript-react.instructions.md").exists()
        # the shared dir and the user's own instructions survive
        assert user.exists()
        assert instr.exists()

    def test_cursor_mcp_json_strips_trw_keeps_user(self, tmp_path: Path) -> None:
        """FIX 4: .cursor/mcp.json trw entry stripped, user servers preserved."""
        import json

        mcp = tmp_path / ".cursor" / "mcp.json"
        mcp.parent.mkdir(parents=True)
        mcp.write_text(
            json.dumps({"mcpServers": {"trw": _trw_server(tmp_path), "other": {"command": "o"}}}, indent=2) + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert mcp.exists(), "user .cursor/mcp.json must not be wholesale-deleted"
        data = json.loads(mcp.read_text())
        assert "trw" not in data["mcpServers"]
        assert "other" in data["mcpServers"]

    def test_root_mcp_json_strips_trw_keeps_user_servers(self, tmp_path: Path) -> None:
        """Uninstall must not destroy the user's other MCP servers.

        The root ``.mcp.json`` is the map claude-code reads, and
        ``bootstrap/_mcp_json.py::_merge_mcp_json`` merges the ``trw`` key
        "while preserving all other user-configured servers". It was registered
        as a plain wholesale-delete surface, so uninstalling TRW took every
        unrelated server with it. The existing fixtures could not catch this:
        they wrote ``{}``, which has nothing to lose.
        """
        import json

        mcp = tmp_path / ".mcp.json"
        mcp.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "trw": _trw_server(tmp_path),
                        "github": {"command": "gh-mcp"},
                        "postgres": {"command": "pg-mcp", "args": ["--dsn", "x"]},
                    }
                },
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert mcp.exists(), "user .mcp.json wholesale-deleted — unrelated servers lost"
        data = json.loads(mcp.read_text())
        assert "trw" not in data["mcpServers"]
        assert "github" in data["mcpServers"]
        assert data["mcpServers"]["postgres"]["args"] == ["--dsn", "x"]

    def test_root_mcp_json_preserved_when_only_trw_remains(self, tmp_path: Path) -> None:
        """A TRW-only map is emptied, not deleted.

        Deliberate, and consistent with the other two server maps: only the
        ``hook-group-list`` shape deletes itself when nothing user-owned is
        left. An MCP map is a file the user's client owns, so uninstall
        withdraws TRW's entry from it rather than removing the file.
        """
        import json

        mcp = tmp_path / ".mcp.json"
        mcp.write_text(json.dumps({"mcpServers": {"trw": _trw_server(tmp_path)}}, indent=2) + "\n")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert mcp.exists()
        assert "trw" not in json.loads(mcp.read_text()).get("mcpServers", {})


@pytest.mark.unit
class TestStripManagedBlocks:
    """sec-006: marker-strip must be line-anchored + missing-end-safe."""

    def test_inline_prose_mention_of_marker_not_stripped(self) -> None:
        """A marker substring inside a prose line must NOT trigger stripping."""
        from trw_mcp.server._subcommands_lifecycle import _strip_managed_blocks

        text = (
            "# Docs\n"
            "To opt in, add the `<!-- trw:start -->` and `<!-- trw:end -->` "
            "markers around your block.\n"
            "Important user content.\n"
        )
        # No standalone marker LINE exists, so nothing should be removed.
        assert _strip_managed_blocks(text) == text

    def test_anchored_block_stripped(self) -> None:
        """A real standalone marker block is stripped, user lines preserved."""
        from trw_mcp.server._subcommands_lifecycle import _strip_managed_blocks

        text = "user before\n<!-- trw:start -->\nmanaged line\n<!-- trw:end -->\nuser after\n"
        out = _strip_managed_blocks(text)
        assert "managed line" not in out
        assert "user before" in out
        assert "user after" in out
        assert "trw:start" not in out

    def test_missing_end_marker_leaves_text_untouched(self) -> None:
        """A start marker with no matching end must NOT delete to EOF."""
        from trw_mcp.server._subcommands_lifecycle import _strip_managed_blocks

        text = "user before\n<!-- trw:start -->\norphan managed content\ncritical user content below\n"
        # No end marker → safe: return unchanged (no delete-to-EOF).
        assert _strip_managed_blocks(text) == text

    def test_missing_end_marker_warns(self, tmp_path: Path) -> None:
        """Uninstall over a missing-end-marker file warns + preserves content.

        CHANGED (PRD-INFRA-192 FR09/FR10 P0): signature gained a *root*
        parameter (the symlink-safety guard needs it), and an orphan marker is
        now reported via ``uninstall_marker_orphan`` (one warning per orphan
        marker, from the shared ``strip_managed_block`` primitive) rather than
        the old whole-file ``uninstall_marker_unbalanced`` event.
        """
        from structlog.testing import capture_logs

        from trw_mcp.server._subcommands_lifecycle import _remove_managed_block_file

        f = tmp_path / "AGENTS.md"
        original = "user\n<!-- trw:start -->\norphan\nmore user\n"
        f.write_text(original)
        with capture_logs() as logs:
            status = _remove_managed_block_file(f, tmp_path, dry_run=False)
        assert status is None
        assert f.read_text() == original
        events = {e.get("event") for e in logs}
        assert "uninstall_marker_orphan" in events


@pytest.mark.integration
class TestUninstallSharedMemoryStore:
    """PRD-CORE-280 FR06: ~/.trw is the daemon's one store; uninstall never removes it.

    ``--delete-memory`` forgets only this checkout's own namespace, through its grant.
    """

    def test_default_keeps_home_trw_and_says_where_the_store_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        user_dir = tmp_path / "home" / ".trw"
        (user_dir / "memory").mkdir(parents=True)
        (user_dir / "memory" / "memory.db").write_text("db")
        monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
        project = tmp_path / "proj"
        (project / ".trw").mkdir(parents=True)

        _run_uninstall(_ns(project))

        assert (user_dir / "memory" / "memory.db").read_text() == "db"
        assert "--delete-memory" in capsys.readouterr().out

    @staticmethod
    def _checkout(root: Path, daemon: object, rows: int) -> tuple[str, object]:
        import asyncio

        from tests._memory_fixtures import attach_checkout

        namespace, client = attach_checkout(root / ".trw", daemon)  # type: ignore[arg-type]
        for index in range(rows):
            asyncio.run(client.store(f"row {index} of {root.name}", namespace))
        return namespace, client

    @staticmethod
    def _count(client: object, namespace: str) -> int:
        import asyncio

        return len(asyncio.run(client.list_page(namespace, 100, None))["entries"])  # type: ignore[attr-defined]

    def test_delete_memory_forgets_only_this_checkouts_namespace(
        self, tmp_path: Path, memory_daemon: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
        mine_ns, mine = self._checkout(tmp_path / "mine", memory_daemon, rows=3)
        other_ns, other = self._checkout(tmp_path / "other", memory_daemon, rows=2)

        _run_uninstall(_ns(tmp_path / "mine", delete_memory=True))

        assert self._count(other, other_ns) == 2, "another checkout's namespace survives"
        assert self._count(mine, mine_ns) == 0, "this checkout's namespace is gone"
        assert memory_daemon.paths.store.is_file(), "the shared store itself stays"  # type: ignore[attr-defined]
        assert not (tmp_path / "mine" / ".trw").exists()

    def test_delete_memory_refuses_a_namespace_the_grant_does_not_cover(
        self, tmp_path: Path, memory_daemon: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.state._store_migration import _set_pin

        monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
        other_ns, other = self._checkout(tmp_path / "other", memory_daemon, rows=2)
        self._checkout(tmp_path / "mine", memory_daemon, rows=0)
        _set_pin(tmp_path / "mine" / ".trw", other_ns)  # a pin edited to name another checkout's namespace

        with pytest.raises(SystemExit):
            _run_uninstall(_ns(tmp_path / "mine", delete_memory=True))

        assert self._count(other, other_ns) == 2
        assert (tmp_path / "mine" / ".trw").is_dir(), "a refusal removes no files"

    @pytest.mark.parametrize("inherits", ["nothing", "the_pin", "the_pin_and_token"])
    def test_a_nested_checkout_never_deletes_its_parents_namespace(
        self, tmp_path: Path, memory_daemon: object, monkeypatch: pytest.MonkeyPatch, inherits: str
    ) -> None:
        from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH

        monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
        parent = tmp_path / "parent"
        parent_ns, parent_client = self._checkout(parent, memory_daemon, rows=2)
        nested = parent / "vendor" / "nested"
        (nested / ".trw").mkdir(parents=True)
        if inherits != "nothing":
            (nested / ".trw" / "config.yaml").write_text(f"project_namespace: {parent_ns}\n", encoding="utf-8")
        if inherits == "the_pin_and_token":
            (nested / CHECKOUT_TOKEN_RELPATH).parent.mkdir(parents=True)
            (nested / CHECKOUT_TOKEN_RELPATH).write_bytes((parent / CHECKOUT_TOKEN_RELPATH).read_bytes())

        with pytest.raises(SystemExit):
            _run_uninstall(_ns(nested, delete_memory=True))

        assert self._count(parent_client, parent_ns) == 2, "the parent's rows survive"
        assert (nested / ".trw").is_dir(), "a refusal removes no files"

    def test_delete_memory_pages_past_one_page(
        self, tmp_path: Path, memory_daemon: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
        monkeypatch.setattr("trw_mcp.server._uninstall_memory._PAGE", 2)
        mine_ns, mine = self._checkout(tmp_path / "mine", memory_daemon, rows=5)

        _run_uninstall(_ns(tmp_path / "mine", delete_memory=True))

        assert self._count(mine, mine_ns) == 0

    def test_a_failure_part_way_names_the_count_and_removes_no_files(
        self,
        tmp_path: Path,
        memory_daemon: object,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from trw_memory.daemon.client import DaemonClient

        monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))  # type: ignore[attr-defined]
        mine_ns, mine = self._checkout(tmp_path / "mine", memory_daemon, rows=3)
        real_forget, calls = DaemonClient.forget, []

        async def _third_call_fails(self: DaemonClient, memory_id: str, namespace: str) -> object:
            calls.append(memory_id)
            if len(calls) == 3:
                return {"status": "error", "error": "daemon went away"}
            return await real_forget(self, memory_id, namespace)

        monkeypatch.setattr(DaemonClient, "forget", _third_call_fails)

        with pytest.raises(SystemExit) as exited:
            _run_uninstall(_ns(tmp_path / "mine", delete_memory=True))

        monkeypatch.undo()
        err = capsys.readouterr().err
        assert exited.value.code == 1
        assert f"deleted 2 of 3 row(s) of {mine_ns}" in err and "daemon went away" in err
        assert "no files were removed" in err and "re-run" in err
        assert (tmp_path / "mine" / ".trw").is_dir()
        assert self._count(mine, mine_ns) == 1

    def test_delete_memory_refuses_user_local(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state._store_migration import _set_pin

        monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "home" / ".trw"))
        project = tmp_path / "proj"
        (project / ".trw").mkdir(parents=True)
        _set_pin(project / ".trw", "user:local")

        with pytest.raises(SystemExit):
            _run_uninstall(_ns(project, delete_memory=True))

        assert (project / ".trw").is_dir()


@pytest.mark.integration
class TestUninstallSymlinkSafety:
    """PRD-INFRA-192 FR09 P0: TRW never removes a byte through a symlink it doesn't own."""

    def test_symlinked_trw_leaves_outside_content_intact(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A symlinked ``.trw`` is refused; whole-project uninstall reports it, never deletes through it."""
        project = tmp_path / "proj"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not delete me")
        (project / ".trw").symlink_to(outside, target_is_directory=True)

        with pytest.raises(SystemExit) as exc:
            _run_uninstall(_ns(project))
        assert exc.value.code == 1

        assert (outside / "sentinel.txt").exists(), "P0: symlinked .trw must not destroy outside content"
        out = capsys.readouterr().out
        assert "symlink" in out.lower(), "the refusal must be reported, not silent"

    def test_symlinked_claude_skills_scoped_and_whole_project(self, tmp_path: Path) -> None:
        """A symlinked ``.claude/skills`` is refused for BOTH ``--ide`` and whole-project uninstall."""
        import shutil

        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="claude-code")
        assert not result["errors"], result["errors"]

        outside = tmp_path.parent / f"{tmp_path.name}-outside-skills"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not delete me")
        skills_dir = tmp_path / ".claude" / "skills"
        shutil.rmtree(skills_dir)
        skills_dir.symlink_to(outside, target_is_directory=True)
        try:
            with pytest.raises(SystemExit):
                _run_uninstall(_ns(tmp_path, ide="claude-code"))
            assert (outside / "sentinel.txt").exists(), "scoped --ide removal must not follow the symlink"
            assert skills_dir.is_symlink()

            with pytest.raises(SystemExit):
                _run_uninstall(_ns(tmp_path))
            assert (outside / "sentinel.txt").exists(), "whole-project uninstall must not follow the symlink"
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_recorded_key_under_symlinked_parent_dir_is_refused(self, tmp_path: Path) -> None:
        """A manifest-recorded file whose PARENT dir is a symlink is refused, not deleted."""
        import shutil

        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="claude-code")
        assert not result["errors"], result["errors"]

        outside = tmp_path.parent / f"{tmp_path.name}-outside-hooks"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not delete me")
        hooks_dir = tmp_path / ".claude" / "hooks"
        shutil.rmtree(hooks_dir)
        hooks_dir.symlink_to(outside, target_is_directory=True)
        try:
            with pytest.raises(SystemExit):
                _run_uninstall(_ns(tmp_path))
            assert (outside / "sentinel.txt").exists(), "a recorded key under a symlinked parent must be refused"
        finally:
            shutil.rmtree(outside, ignore_errors=True)


@pytest.mark.unit
class TestUninstallManifest:
    """The uninstall surface manifest is registry-derived (catalog seam)."""

    def test_manifest_covers_all_profiles(self) -> None:
        """Manifest references surfaces for every non-claude profile."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        relpaths = {s.relpath for s in uninstall_surfaces()}
        # core
        assert ".trw" in relpaths
        assert ".mcp.json" in relpaths
        # per-profile representatives
        assert ".opencode/agents" in relpaths
        assert ".codex/config.toml" in relpaths
        assert ".codex/hooks.json" in relpaths
        assert ".github/agents" in relpaths
        assert ".aider.conf.yml" in relpaths
        # FIX 2: antigravity is no longer a single rmtree dir surface.
        assert ".antigravitycli" not in relpaths
        assert ".antigravitycli/settings.json" in relpaths
        assert ".antigravitycli/agents" in relpaths
        # P1 security invariant: the AG-03 PreToolUse hook (install_before_edit_hook
        # writes hooks.json + hooks/trw_before_edit_telemetry.py) MUST be covered by
        # uninstall or a live TRW hook is left registered after removal.
        by_path = {s.relpath: s for s in uninstall_surfaces()}
        assert ".antigravitycli/hooks.json" in relpaths
        assert ".antigravitycli/hooks" in relpaths
        # hooks.json is a FLAT ``{event: [entry]}`` map, so the codex/copilot
        # hook-group-list strategy does not match it -- but ``_merge_hooks_json``
        # preserves the user's other event keys, so it is NOT TRW-only either.
        # It gets the command-identity strategy that matches its actual shape.
        assert by_path[".antigravitycli/hooks.json"].config_shape == "antigravity-hook-map"
        # The hooks/ DIR holds only TRW scripts and stays a plain removal.
        assert by_path[".antigravitycli/hooks"].merged_config is False
        # FIX 3: previously-missing bootstrap surfaces.
        assert ".github/skills" in relpaths
        assert ".github/hooks/hooks.json" in relpaths
        assert ".github/hooks/trw-copilot-adapter.sh" in relpaths
        # SHARED .github/instructions: only the specific TRW files, not the dir.
        assert ".github/instructions" not in relpaths
        assert ".github/instructions/python-testing.instructions.md" in relpaths
        assert ".github/instructions/typescript-react.instructions.md" in relpaths
        # FIX 4: .cursor/mcp.json (distinct from the root .mcp.json).
        assert ".cursor/mcp.json" in relpaths
        assert ".grok/config.toml" in relpaths
        assert ".grok/agents" in relpaths

    def test_manifest_marks_shared_files_as_managed_blocks(self) -> None:
        """Shared root instruction files are managed-block surfaces."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        assert by_path["AGENTS.md"].managed_block is True
        assert by_path["ANTIGRAVITY.md"].managed_block is True
        # config dirs are plain removals
        assert by_path[".opencode/agents"].managed_block is False

    def test_manifest_covers_retired_instruction_surface(self) -> None:
        """The retired aider client keeps its instruction surface so
        pre-retirement installs stay removable forever (release-verify P1)."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        assert by_path[".aider/instructions.md"].managed_block is True

    def test_manifest_merged_config_surfaces_carry_shapes(self) -> None:
        """Merged-config surfaces declare the correct strip strategy shape."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        expected = {
            ".codex/config.toml": "codex-toml",
            ".codex/hooks.json": "codex-hook-group-list",
            ".github/hooks/hooks.json": "copilot-hook-group-list",
            ".cursor/mcp.json": "mcp-server-map",
            ".antigravitycli/settings.json": "mcp-server-map",
            # The root map, used by claude-code — same shape, same merge
            # semantics, and for a long time the only one not protected.
            ".mcp.json": "mcp-server-map",
            ".grok/config.toml": "codex-toml",
        }
        for relpath, shape in expected.items():
            surface = by_path[relpath]
            assert surface.merged_config is True, f"{relpath} should be merged_config"
            assert surface.config_shape == shape, f"{relpath} shape mismatch"
        # TRW-created plain files under shared dirs stay plain removals.
        assert by_path[".github/hooks/trw-copilot-adapter.sh"].merged_config is False
        assert by_path[".github/instructions/python-testing.instructions.md"].merged_config is False


def _listed_surfaces(out: str) -> list[str]:
    """Extract the project-relative paths uninstall claims as TRW surfaces.

    Listing lines are ``    <kind> <relpath>[ (note)]`` where kind is one of
    ``dir``/``file``/``block``/``entry``.
    """
    kinds = ("dir ", "file ", "block ", "entry ")
    rels: list[str] = []
    for raw in out.splitlines():
        line = raw.strip()
        if not line.startswith(kinds):
            continue
        rel = line.split(maxsplit=1)[1]
        rels.append(rel.split(" (")[0].split(" —")[0].strip())
    return rels


def _snapshot(path: Path) -> object:
    """Return a comparable snapshot of *path* (missing / dir listing / bytes)."""
    if not path.exists():
        return None
    if path.is_dir():
        return sorted(str(p.relative_to(path)) for p in path.rglob("*"))
    return path.read_bytes()


@pytest.mark.integration
class TestUninstallInstructionSurfaces:
    """The instruction surfaces uninstall covers must be the ones TRW writes.

    Two failure shapes this class pins:

    * a surface registered for a path no writer ever produces (dead cleanup),
    * a file TRW *does* write that no surface covers (leftover TRW artifact).
    """

    def test_claude_md_trw_block_is_stripped(self, tmp_path: Path) -> None:
        """CLAUDE.md is the claude-code instruction surface and must be cleaned.

        ``bootstrap/_init_project.py`` writes CLAUDE.md with a real
        ``<!-- trw:start -->``/``<!-- trw:end -->`` block. Before this test the
        manifest carried ``.claude/INSTRUCTIONS.md`` (never written by anything)
        instead, so the block survived uninstall.
        """
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# My Project\n\nUser build notes.\n\n"
            "<!-- trw:start -->\nTRW auto-generated protocol\n<!-- trw:end -->\n\n"
            "More user notes.\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert claude_md.exists(), "user CLAUDE.md wholesale-deleted"
        text = claude_md.read_text()
        assert "User build notes." in text
        assert "More user notes." in text
        assert "trw:start" not in text
        assert "TRW auto-generated protocol" not in text

    def test_claude_instructions_md_is_not_a_registered_surface(self) -> None:
        """``.claude/INSTRUCTIONS.md`` has no writer, so it must not be claimed.

        Nothing in ``src/`` writes this path — claude-code's TRW block goes to
        CLAUDE.md (or the ``.trw/INSTRUCTIONS.md`` sidecar under PRD-CORE-203,
        which the ``.trw`` surface already covers). Registering it advertised a
        cleanup that could never happen, and would make a hand-authored file of
        that name a TRW-owned artifact.
        """
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        relpaths = {s.relpath for s in uninstall_surfaces()}
        assert ".claude/INSTRUCTIONS.md" not in relpaths
        assert "CLAUDE.md" in relpaths

    def test_user_authored_claude_instructions_md_is_preserved(self, tmp_path: Path) -> None:
        """A user's own ``.claude/INSTRUCTIONS.md`` is not TRW's to remove."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        user_file = claude_dir / "INSTRUCTIONS.md"
        user_file.write_text("# My own Claude instructions\n")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert user_file.exists()
        assert user_file.read_text() == "# My own Claude instructions\n"

    def test_generated_codex_instructions_file_is_removed(self, tmp_path: Path) -> None:
        """The real generated ``.codex/INSTRUCTIONS.md`` is actually removed.

        Goes through a real ``init_project`` (PRD-INFRA-192 FR09 C3) so the
        manifest records its content hash -- calling the writer directly
        against a bare ``.trw`` mkdir leaves it unrecorded, and rule 3 now
        keeps an unrecorded file rather than guessing.
        """
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="codex")
        assert not result["errors"], result["errors"]
        target = tmp_path / ".codex" / "INSTRUCTIONS.md"
        assert target.is_file(), "precondition: writer produced the file"

        _run_uninstall(_ns(tmp_path))

        assert not target.exists(), "TRW-generated codex instructions survived uninstall"

    def test_generated_opencode_instructions_file_is_removed(self, tmp_path: Path) -> None:
        """The real generated ``.opencode/INSTRUCTIONS.md`` is actually removed.

        Goes through a real ``init_project`` for the same reason as the codex
        case above.
        """
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="opencode")
        assert not result["errors"], result["errors"]
        target = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        assert target.is_file(), "precondition: writer produced the file"

        _run_uninstall(_ns(tmp_path))

        assert not target.exists(), "TRW-generated opencode instructions survived uninstall"

    def test_reported_surfaces_all_actually_disappear(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Truthfulness: every path uninstall lists is really acted on.

        Seeds the two generated instruction files plus a CLAUDE.md TRW block,
        then asserts no listed path is still present unchanged afterwards. This
        is the invariant the ``managed_block``-without-markers classification
        broke: the listing promised a cleanup that silently no-op'd.
        """
        from trw_mcp.bootstrap._opencode_instructions import (
            generate_codex_instructions,
            generate_opencode_instructions,
        )

        generate_codex_instructions(tmp_path)
        generate_opencode_instructions(tmp_path, "generic")
        (tmp_path / "CLAUDE.md").write_text("user\n<!-- trw:start -->\ntrw\n<!-- trw:end -->\n")
        (tmp_path / ".trw").mkdir()

        # Dry run first: it prints the same listing without mutating anything,
        # so the "before" snapshot is taken against the exact set of paths the
        # real run will claim.
        _run_uninstall(_ns(tmp_path, dry_run=True))
        listed = _listed_surfaces(capsys.readouterr().out)
        assert listed, "precondition: uninstall listed at least one surface"
        before = {rel: _snapshot(tmp_path / rel) for rel in listed}

        _run_uninstall(_ns(tmp_path))

        for rel, snapshot in before.items():
            assert _snapshot(tmp_path / rel) != snapshot, (
                f"uninstall listed {rel} as a TRW surface but left it byte-identical"
            )


@pytest.mark.integration
class TestUninstallGitPostCommitHook:
    """PRD-CORE-231 installs a managed block in ``.git/hooks/post-commit``."""

    def _install_hook(self, tmp_path: Path) -> Path:
        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook

        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        result = install_git_post_commit_hook(tmp_path)
        assert not result["errors"], result["errors"]
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook.is_file(), "precondition: hook installed"
        return hook

    def test_trw_block_is_removed_from_post_commit(self, tmp_path: Path) -> None:
        """Uninstall strips the TRW dispatch block from the git hook."""
        hook = self._install_hook(tmp_path)
        assert "trw post-commit (managed by trw-mcp)" in hook.read_text()

        _run_uninstall(_ns(tmp_path))

        remaining = hook.read_text() if hook.exists() else ""
        assert "trw post-commit (managed by trw-mcp)" not in remaining
        assert "trw-post-commit.sh" not in remaining

    def test_foreign_post_commit_content_is_preserved(self, tmp_path: Path) -> None:
        """A user's own post-commit logic survives; only the TRW block goes."""
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        hook = tmp_path / ".git" / "hooks" / "post-commit"
        hook.write_text('#!/bin/sh\necho "user hook"\n')

        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook

        install_git_post_commit_hook(tmp_path)
        assert 'echo "user hook"' in hook.read_text()

        _run_uninstall(_ns(tmp_path))

        assert hook.exists(), "user post-commit hook wholesale-deleted"
        text = hook.read_text()
        assert 'echo "user hook"' in text
        assert "trw post-commit (managed by trw-mcp)" not in text

    def test_post_commit_is_a_registered_surface(self) -> None:
        """The hook path is in the manifest, not just handled incidentally."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        assert ".git/hooks/post-commit" in by_path
        assert by_path[".git/hooks/post-commit"].managed_block is True


@pytest.mark.integration
class TestUninstallClaudeSettings:
    """``.claude/settings.json`` is a merged config TRW writes hook entries into."""

    def _settings_with_trw_and_user_hooks(self) -> dict[str, object]:
        """TRW's own SessionStart groups, plus TRW's deliver-gate group with a user hook appended."""
        import copy
        import json

        from trw_mcp.bootstrap._utils import _DATA_DIR

        bundled = json.loads((_DATA_DIR / "settings.json").read_text(encoding="utf-8"))["hooks"]
        gate = copy.deepcopy(bundled["PreToolUse"][0])
        gate["hooks"].insert(0, {"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR/scripts/my-guard.sh"'})
        return {
            "env": {"ENABLE_TOOL_SEARCH": "true"},
            "permissions": {"allow": ["Bash(ls:*)"]},
            "hooks": {"SessionStart": bundled["SessionStart"], "PreToolUse": [gate]},
        }

    def _write_settings(self, tmp_path: Path, data: dict[str, object]) -> Path:
        import json

        claude = tmp_path / ".claude"
        claude.mkdir(exist_ok=True)
        path = claude / "settings.json"
        path.write_text(json.dumps(data, indent=2) + "\n")
        return path

    def test_trw_hook_entries_are_stripped(self, tmp_path: Path) -> None:
        """Hook entries pointing into ``.claude/hooks/`` are withdrawn.

        Uninstall deletes ``.claude/hooks/`` wholesale, so leaving these
        registered means every subsequent Claude Code session invokes a script
        that no longer exists.
        """
        import json

        path = self._write_settings(tmp_path, self._settings_with_trw_and_user_hooks())
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert path.exists(), "user settings.json wholesale-deleted"
        data = json.loads(path.read_text())
        assert ".claude/hooks/" not in json.dumps(data), "dangling TRW hook command left registered"

    def test_user_hooks_and_settings_are_preserved(self, tmp_path: Path) -> None:
        """Non-TRW hook commands and unrelated top-level keys survive."""
        import json

        path = self._write_settings(tmp_path, self._settings_with_trw_and_user_hooks())
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        data = json.loads(path.read_text())
        assert data["permissions"] == {"allow": ["Bash(ls:*)"]}
        rendered = json.dumps(data)
        assert "scripts/my-guard.sh" in rendered, "user hook command removed"
        # The mixed PreToolUse entry keeps its matcher and its user hook.
        pre_tool = data["hooks"]["PreToolUse"]
        assert len(pre_tool) == 1
        assert pre_tool[0]["matcher"] == "mcp__trw__trw_deliver"
        assert pre_tool[0]["hooks"] == [{"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR/scripts/my-guard.sh"'}]
        # SessionStart held only TRW hooks -> the event key is dropped entirely.
        assert "SessionStart" not in data["hooks"]

    def test_user_own_script_in_claude_hooks_dir_survives(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR09 P1-d: a user's own script in ``.claude/hooks/`` is NOT TRW's.

        The prior identity check matched ANY command referencing the
        ``.claude/hooks/`` directory, so a user's own script placed there was
        stripped from settings.json on whole-project uninstall even though
        TRW never wrote that registration.
        """
        import json

        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "my-own.sh").write_text("#!/bin/sh\necho hi\n")
        path = self._write_settings(
            tmp_path,
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/my-own.sh"',
                                }
                            ],
                        }
                    ]
                }
            },
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        data = json.loads(path.read_text())
        assert data.get("hooks"), "user's own hook registration must survive whole-project uninstall"
        assert (
            data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/my-own.sh"'
        )

    def test_settings_without_trw_hooks_untouched(self, tmp_path: Path) -> None:
        """A settings.json with no TRW hook command is preserved byte-for-byte."""
        path = self._write_settings(
            tmp_path,
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "echo hi"}]}]}},
        )
        original = path.read_text()
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert path.read_text() == original

    def test_settings_is_a_registered_merged_surface(self) -> None:
        """The manifest declares settings.json merged, never a plain deletion."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        surface = by_path[".claude/settings.json"]
        assert surface.merged_config is True
        assert surface.managed_block is False
        assert surface.config_shape == "claude-settings"

    def test_dry_run_does_not_mutate_settings(self, tmp_path: Path) -> None:
        """Dry run classifies without writing."""
        path = self._write_settings(tmp_path, self._settings_with_trw_and_user_hooks())
        original = path.read_text()
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path, dry_run=True))

        assert path.read_text() == original


# Paths a real install writes that uninstall must deliberately NOT claim.
# Every entry needs a reason; an unexplained entry here is how the gap this test
# exists to close would be re-opened under cover of an exemption.
_UNINSTALL_EXEMPT_PREFIXES: tuple[tuple[str, str], ...] = (
    # init-project scaffolds an empty docs/ dir for the user's own docs. It is
    # never TRW content, and anything under it is the user's.
    ("docs/", "user documentation directory scaffolded, never populated by TRW"),
)

# Plain surfaces (rmtree/unlink, no managed block, no config merge) that a
# current install deliberately does NOT produce. Every entry here is a
# LEGACY-CLEANUP surface: a path some earlier TRW version wrote and which
# uninstall must still be able to remove. The list exists so that a surface
# with no producer is a *decision* rather than an oversight — an unjustified
# entry is how `.claude/commands` came to be rmtree'd when TRW had never
# written it.
_PLAIN_SURFACES_WITHOUT_A_CURRENT_PRODUCER: tuple[tuple[str, str], ...] = (
    (
        ".aider.conf.yml",
        "aider was retired 2026-07-11; the surface is retained so existing "
        "installs stay removable (see the comment above _CLIENT_ORDER)",
    ),
    (
        ".github/instructions/trw-distill-hotspots.instructions.md",
        "PRD-CORE-239 stopped writing the copilot C2 path-instructions stub; "
        "the surface is retained to clean up installs that predate it",
    ),
    (
        ".antigravitycli/agents",
        "PRD-CORE-252 moved antigravity's subagents to `.agents/agents`, the "
        "directory that client's own reference documents; the surface is "
        "retained to clean up installs that predate the move",
    ),
)


@pytest.mark.slow
@pytest.mark.integration
class TestInstallUninstallParity:
    """Whatever install writes, uninstall must know about.

    Install and uninstall were two hand-maintained lists with nothing forcing
    them to agree, and every uninstall defect found so far — the phantom
    ``.claude/INSTRUCTIONS.md``, the missing ``CLAUDE.md``, codex's whole skill
    corpus under ``.agents/skills``, the root canon documents, ``.vscode/mcp.json``
    — is an instance of that one gap. This test runs a real ``init_project`` for
    every client and diffs the resulting tree against the manifest, so a new
    installed artifact fails here instead of silently surviving uninstall.
    """

    def test_every_installed_file_is_covered_by_a_surface(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import init_project
        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        result = init_project(tmp_path, ide="all")
        assert not result["errors"], result["errors"]
        install_git_post_commit_hook(tmp_path)

        home = Path.home().resolve()
        surfaces: list[str] = []
        for s in uninstall_surfaces():
            if not s.home_scoped:
                surfaces.append(s.relpath)
                continue
            # A home-scoped surface only counts when the sandboxed HOME sits inside
            # tmp_path (the autouse fixture guarantees it); rebase it so the rglob
            # below can match the install-time write.
            try:
                surfaces.append((home / s.relpath).relative_to(tmp_path.resolve()).as_posix())
            except ValueError:
                continue
        exempt = tuple(prefix for prefix, _reason in _UNINSTALL_EXEMPT_PREFIXES)

        def covered(rel: str) -> bool:
            return any(rel == s or rel.startswith(s + "/") for s in surfaces)

        installed = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
        assert installed, "precondition: init_project wrote something"
        uncovered = [rel for rel in installed if not rel.startswith(exempt) and not covered(rel)]
        assert not uncovered, (
            "install writes these paths but no uninstall surface covers them "
            f"(register them in client_profiles/catalog.py): {uncovered}"
        )

    def test_every_plain_surface_has_a_producer(self, tmp_path: Path) -> None:
        """The mirror of the test above: whatever uninstall deletes, install must write.

        ``test_every_installed_file_is_covered_by_a_surface`` only closes the
        install-to-uninstall direction, so a surface registered for wholesale
        deletion that TRW never produces passes every gate. That is how
        ``UninstallSurface(".claude/commands")`` shipped: TRW's command surface
        is skills plus MCP-prompt registration and no bootstrap writer has ever
        targeted that path, but it was classified plain, so ``trw-mcp
        uninstall`` ``rmtree``'d a directory containing only the user's own
        Claude Code slash commands and reported it as clean TRW cleanup.

        Only *plain* surfaces are checked. A managed-block or merged-config
        surface edits a file it does not own, so its absence after install is
        normal and its removal strategy is non-destructive by construction.
        """
        from trw_mcp.bootstrap import init_project
        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        result = init_project(tmp_path, ide="all")
        assert not result["errors"], result["errors"]
        install_git_post_commit_hook(tmp_path)

        legacy = {rel for rel, _reason in _PLAIN_SURFACES_WITHOUT_A_CURRENT_PRODUCER}
        plain = [s for s in uninstall_surfaces() if not s.managed_block and not s.merged_config]
        assert plain, "precondition: there are plain surfaces to check"

        unproduced = sorted(s.relpath for s in plain if s.relpath not in legacy and not (tmp_path / s.relpath).exists())
        assert not unproduced, (
            "these surfaces are registered for wholesale deletion but a full "
            "`init_project(ide='all')` never creates them — either TRW does not "
            "own the path (drop the surface) or it is a legacy-cleanup surface "
            "(add it to _PLAIN_SURFACES_WITHOUT_A_CURRENT_PRODUCER with a "
            f"reason): {unproduced}"
        )

    def test_legacy_cleanup_exemptions_are_still_needed(self) -> None:
        """An exemption that stops being necessary must not linger silently.

        The exclusion set is the mechanism that makes the test above honest, so
        it needs its own ratchet: once a legacy surface is dropped from the
        registry, its exemption is dead weight that would silently re-admit the
        path if someone re-registered it.
        """
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        registered = {s.relpath for s in uninstall_surfaces()}
        stale = sorted(rel for rel, _reason in _PLAIN_SURFACES_WITHOUT_A_CURRENT_PRODUCER if rel not in registered)
        assert not stale, f"exempted surfaces that are no longer registered — drop the exemption: {stale}"

    def test_uninstall_leaves_no_trw_artifact_behind(self, tmp_path: Path) -> None:
        """End-to-end: install everything, uninstall, assert nothing TRW remains.

        Complements the manifest diff above — a path can be *registered* and
        still survive if its removal strategy is a no-op, which is exactly how
        the marker-less instruction files and the git-hook block hid.
        """
        from trw_mcp.bootstrap import init_project
        from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook

        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print('user code')\n")
        init_project(tmp_path, ide="all")
        install_git_post_commit_hook(tmp_path)

        _run_uninstall(_ns(tmp_path))

        offenders: list[str] = []
        for path in tmp_path.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(tmp_path).as_posix()
            if rel.startswith(".git/") and rel != ".git/hooks/post-commit":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "trw:start" in text or "trw_before_edit" in text or '"trw"' in text:
                offenders.append(rel)
        assert not offenders, f"TRW content survived uninstall in: {offenders}"
        assert (tmp_path / "src" / "app.py").read_text() == "print('user code')\n"


def _cursor_entry(event: str) -> dict[str, object]:
    """The first hook entry cursor-ide generates for *event*."""
    from trw_mcp.bootstrap._cursor_ide import _IDE_HOOK_EVENTS

    return dict(_IDE_HOOK_EVENTS[event][0])


def _antigravity_entry() -> dict[str, object]:
    """The one PreToolUse entry AG-03 generates."""
    from trw_mcp.bootstrap._generated_entries import flat_hook_entries

    return dict(flat_hook_entries("antigravity-hook-map")["PreToolUse"][0])  # type: ignore[arg-type]


@pytest.mark.integration
class TestUninstallHookIdentityByCommand:
    """Two clients identify their TRW hook entries by command path, not tag."""

    def test_cursor_hooks_json_preserves_user_hooks(self, tmp_path: Path) -> None:
        """`.cursor/hooks.json` is merged on install, so it must be merged on removal.

        ``smart_merge_cursor_json`` removes only entries whose command starts
        with ``.cursor/hooks/trw-`` and preserves everything else; a wholesale
        delete on uninstall destroyed the user's own hooks.
        """
        import json

        cursor = tmp_path / ".cursor"
        cursor.mkdir()
        hooks = cursor / "hooks.json"
        # Trailing newline matches the byte-preserving canonical-or-untouched
        # rule the strip now enforces (PRD-INFRA-192 FR09/FR10 P0): TRW's own
        # writers append one, so a fixture that omits it reads as
        # hand-formatted and the strip correctly leaves it alone.
        hooks.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "sessionStart": [
                            _cursor_entry("sessionStart"),
                            {"command": "./scripts/my-own-hook.sh"},
                        ]
                    },
                },
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists(), "user cursor hooks.json wholesale-deleted"
        data = json.loads(hooks.read_text())
        commands = [h["command"] for h in data["hooks"]["sessionStart"]]
        assert commands == ["./scripts/my-own-hook.sh"]

    def test_cursor_hooks_json_all_trw_is_deleted(self, tmp_path: Path) -> None:
        """A hooks.json holding only TRW entries is removed outright."""
        import json

        cursor = tmp_path / ".cursor"
        cursor.mkdir()
        hooks = cursor / "hooks.json"
        hooks.write_text(
            json.dumps(
                {"version": 1, "hooks": {"afterFileEdit": [_cursor_entry("afterFileEdit")]}},
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not hooks.exists()

    def test_cursor_user_own_trw_prefixed_script_survives(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR09 P1-d (round 2): a NON-bundled ``trw-``-named script survives.

        A user script matching the ``trw-`` naming convention TRW itself uses,
        but never bundled/generated by TRW, must survive. The prior identity
        check was a directory+prefix substring (``.cursor/hooks/trw-``), which
        matched this too.
        """
        import json

        cursor = tmp_path / ".cursor"
        cursor.mkdir()
        hooks = cursor / "hooks.json"
        hooks.write_text(
            json.dumps(
                {"version": 1, "hooks": {"beforeShellExecution": [{"command": ".cursor/hooks/trw-my-own-thing.sh"}]}}
            )
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists(), "a non-bundled trw-prefixed user script must not cause wholesale deletion"
        data = json.loads(hooks.read_text())
        commands = [h["command"] for h in data["hooks"]["beforeShellExecution"]]
        assert commands == [".cursor/hooks/trw-my-own-thing.sh"]

    def test_antigravity_hooks_json_preserves_user_events(self, tmp_path: Path) -> None:
        """The flat antigravity map keeps user entries and drops the TRW hook."""
        import json

        ag = tmp_path / ".antigravitycli"
        ag.mkdir()
        hooks = ag / "hooks.json"
        # Trailing newline required by the byte-preserving canonical-or-untouched
        # rule (PRD-INFRA-192 FR09/FR10 P0) -- see the cursor test above.
        hooks.write_text(
            json.dumps(
                {
                    "PreToolUse": [
                        _antigravity_entry(),
                        {"matcher": "Bash", "command": "python3 tools/audit.py"},
                    ],
                    "PostToolUse": [{"matcher": "*", "command": "echo done"}],
                },
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists(), "user antigravity hooks.json wholesale-deleted"
        data = json.loads(hooks.read_text())
        assert [h["command"] for h in data["PreToolUse"]] == ["python3 tools/audit.py"]
        assert data["PostToolUse"] == [{"matcher": "*", "command": "echo done"}]

    def test_antigravity_hooks_json_all_trw_is_deleted(self, tmp_path: Path) -> None:
        """No live TRW hook may survive: an all-TRW map is removed outright."""
        import json

        ag = tmp_path / ".antigravitycli"
        ag.mkdir()
        hooks = ag / "hooks.json"
        hooks.write_text(
            json.dumps(
                {"PreToolUse": [_antigravity_entry()]},
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not hooks.exists()

    def test_antigravity_user_own_hook_registration_survives(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR09 P1-d (round 2): a user's own antigravity hook is not TRW's.

        The prior identity check matched ANY command referencing the
        ``.antigravitycli/hooks/`` directory, so a user's own script placed
        there was stripped on whole-project uninstall even though TRW never
        registered it.
        """
        import json

        ag = tmp_path / ".antigravitycli"
        ag.mkdir()
        hooks = ag / "hooks.json"
        hooks.write_text(
            json.dumps({"PreToolUse": [{"matcher": "Edit", "command": "python3 .antigravitycli/hooks/my-own.py"}]})
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists(), "user antigravity hooks.json wholesale-deleted"
        data = json.loads(hooks.read_text())
        assert data.get("PreToolUse") == [{"matcher": "Edit", "command": "python3 .antigravitycli/hooks/my-own.py"}], (
            "user's own hook registration must survive whole-project uninstall"
        )


@pytest.mark.unit
class TestUninstallMarkerTableDerivation:
    """The managed-block marker table is derived, not hand-copied."""

    def test_every_registry_pair_is_covered(self) -> None:
        """No channel marker pair may be missing from the uninstall table.

        The table was a hand-maintained subset that had drifted: it carried 3
        pairs while ``MARKER_REGISTRY`` carried 7, so uninstall left
        ``trw:distill`` / ``trw:memory`` / ``trw:cursor:mdc`` / ``trw:codex``
        blocks in user files while reporting the file cleaned.
        """
        from trw_mcp.channels._manifest_models import MARKER_REGISTRY
        from trw_mcp.server._subcommands_uninstall_config import _MANAGED_BLOCK_MARKERS

        covered = {marker for pair in _MANAGED_BLOCK_MARKERS for marker in pair}
        missing = sorted(set(MARKER_REGISTRY.values()) - covered)
        assert not missing, f"marker(s) in MARKER_REGISTRY not strippable by uninstall: {missing}"

    def test_pairs_outside_the_registry_are_retained(self) -> None:
        """The registry is not a complete inventory; local extras must survive.

        ``trw:antigravity`` and the git post-commit pair are NOT in
        MARKER_REGISTRY, so a naive "derive everything from the registry" would
        silently drop two working strips.
        """
        from trw_mcp.server._subcommands_uninstall_config import _MANAGED_BLOCK_MARKERS

        assert ("<!-- trw:antigravity:start -->", "<!-- trw:antigravity:end -->") in _MANAGED_BLOCK_MARKERS
        starts = {pair[0] for pair in _MANAGED_BLOCK_MARKERS}
        assert any("trw post-commit" in start for start in starts)
        assert "<!-- trw-distill:start -->" in starts

    def test_distill_block_after_trw_end_is_stripped(self, tmp_path: Path) -> None:
        """A distill segment placed AFTER ``trw:end`` is removed too.

        This is the shape TRW actually writes into AGENTS.md/ANTIGRAVITY.md, and
        the one the drifted table left behind permanently.
        """
        agents = tmp_path / "AGENTS.md"
        agents.write_text(
            "# Project\n\nUser notes.\n\n"
            "<!-- trw:start -->\nceremony\n<!-- trw:end -->\n\n"
            "<!-- trw:distill:start -->\ndistill hint\n<!-- trw:distill:end -->\n\n"
            "Trailing user notes.\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        text = agents.read_text()
        assert "User notes." in text
        assert "Trailing user notes." in text
        assert "distill hint" not in text
        assert "trw:distill" not in text

    def test_distill_only_file_is_listed_and_cleaned(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A file whose ONLY TRW content is a distill block is still handled."""
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# Project\n\n<!-- trw:distill:start -->\nhint\n<!-- trw:distill:end -->\n")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        out = capsys.readouterr().out
        assert "AGENTS.md" in out
        assert "hint" not in agents.read_text()


def _seed_corpus(trw_dir: Path, *, db: bool = True, learnings: int = 2) -> None:
    """Seed a project .trw with a memory.db and/or learning entry files."""
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("k: v")
    if db:
        (trw_dir / "memory").mkdir(exist_ok=True)
        (trw_dir / "memory.db").write_text("SQLITE")
    if learnings:
        entries = trw_dir / "learnings" / "entries"
        entries.mkdir(parents=True, exist_ok=True)
        (trw_dir / "learnings" / "index.yaml").write_text("entries: []")
        for i in range(learnings):
            (entries / f"learning-{i}.yaml").write_text(f"summary: l{i}")


@pytest.mark.integration
class TestUninstallCorpusBlastRadius:
    """Destructive-uninstall guard: warn + --keep-memory protect the corpus."""

    def test_warning_names_blast_radius_with_yes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Even with --yes, removing a corpus prints the blast-radius warning."""
        _seed_corpus(tmp_path / ".trw", db=True, learnings=3)

        _run_uninstall(_ns(tmp_path))

        out = capsys.readouterr().out
        assert "permanently deletes your learning corpus" in out
        assert "memory.db" in out
        assert "3 learning(s)" in out
        assert "trw-mcp export" in out
        assert "--keep-memory" in out
        # default still destroys the corpus
        assert not (tmp_path / ".trw").exists()

    def test_no_warning_when_no_corpus(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A .trw with no memory.db and no learnings gets no destructive warning."""
        trw = tmp_path / ".trw"
        trw.mkdir()
        (trw / "config.yaml").write_text("k: v")
        # index.yaml seed alone must NOT count as a corpus.
        (trw / "learnings").mkdir()
        (trw / "learnings" / "index.yaml").write_text("entries: []")

        _run_uninstall(_ns(tmp_path))

        out = capsys.readouterr().out
        assert "permanently deletes your learning corpus" not in out
        assert not trw.exists()

    def test_default_confirm_required_aborts_preserves_corpus(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --yes, declining the prompt preserves the corpus."""
        _seed_corpus(tmp_path / ".trw", db=True, learnings=2)
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")

        _run_uninstall(_ns(tmp_path, yes=False))

        assert (tmp_path / ".trw" / "memory.db").exists()
        assert (tmp_path / ".trw").exists()

    def test_confirm_prompt_mentions_corpus(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The interactive prompt names the corpus when one is at risk."""
        _seed_corpus(tmp_path / ".trw", db=True, learnings=1)
        seen: dict[str, str] = {}

        def _fake_input(prompt: str) -> str:
            seen["prompt"] = prompt
            return "n"

        monkeypatch.setattr("builtins.input", _fake_input)
        _run_uninstall(_ns(tmp_path, yes=False))

        assert "learning corpus" in seen["prompt"]

    def test_keep_memory_preserves_corpus_removes_rest(self, tmp_path: Path) -> None:
        """--keep-memory keeps memory/ + learnings/ but removes other .trw state."""
        trw = tmp_path / ".trw"
        _seed_corpus(trw, db=True, learnings=2)
        (trw / "runs").mkdir()
        (trw / "runs" / "old.json").write_text("{}")
        (trw / "context").mkdir()
        (trw / "context" / "state.json").write_text("{}")

        _run_uninstall(_ns(tmp_path, keep_memory=True))

        # corpus preserved
        assert (trw / "memory.db").exists()
        assert (trw / "memory").is_dir()
        assert (trw / "learnings" / "entries" / "learning-0.yaml").exists()
        # other state removed
        assert not (trw / "runs").exists()
        assert not (trw / "context").exists()
        assert not (trw / "config.yaml").exists()
        # .trw itself preserved (still holds the corpus)
        assert trw.exists()

    def test_keep_memory_keeps_the_nested_store_when_there_are_no_learning_files(self, tmp_path: Path) -> None:
        """The store lives at .trw/memory/memory.db; with no YAML entries it is still the corpus."""
        memory = tmp_path / ".trw" / "memory"
        memory.mkdir(parents=True)
        (memory / "memory.db").write_bytes(b"SQLite format 3\x00" + bytes(range(256)))
        (memory / "memory.db-wal").write_bytes(b"wal")
        before = {p.name: p.read_bytes() for p in memory.iterdir()}
        (tmp_path / ".trw" / "config.yaml").write_text("x: 1\n", encoding="utf-8")

        _run_uninstall(_ns(tmp_path, keep_memory=True))

        assert {p.name: p.read_bytes() for p in memory.iterdir()} == before
        assert not (tmp_path / ".trw" / "config.yaml").exists()

    def test_the_nested_store_alone_triggers_the_corpus_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / ".trw" / "memory").mkdir(parents=True)
        (tmp_path / ".trw" / "memory" / "memory.db").write_bytes(b"SQLite format 3\x00")

        _run_uninstall(_ns(tmp_path, dry_run=True))

        assert "permanently deletes your learning corpus" in capsys.readouterr().out

    def test_keep_memory_no_destructive_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--keep-memory suppresses the destructive warning (corpus is safe)."""
        _seed_corpus(tmp_path / ".trw", db=True, learnings=2)

        _run_uninstall(_ns(tmp_path, keep_memory=True))

        out = capsys.readouterr().out
        assert "permanently deletes your learning corpus" not in out
        assert "PRESERVED (--keep-memory)" in out

    def test_learnings_only_corpus_triggers_warning(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Learnings present but no memory.db still counts as a corpus."""
        _seed_corpus(tmp_path / ".trw", db=False, learnings=2)

        _run_uninstall(_ns(tmp_path))

        out = capsys.readouterr().out
        assert "permanently deletes your learning corpus" in out
        assert "2 learning(s)" in out
        assert "memory.db" not in out.split("WARNING")[1].split("Export")[0]


@pytest.mark.integration
class TestUninstallExitCode:
    """Partial-failure truthfulness: uninstall must exit non-zero on errors."""

    def test_partial_removal_failure_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".trw").mkdir()
        (project / ".trw" / "config.yaml").write_text("x: 1\n")

        import shutil as shutil_module

        def _raise_rmtree(*args: object, **kwargs: object) -> None:
            raise OSError("permission denied (simulated)")

        monkeypatch.setattr(shutil_module, "rmtree", _raise_rmtree)

        with pytest.raises(SystemExit) as exc:
            _run_uninstall(_ns(project))
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "could not be removed" in captured.err
        assert "Error removing" in captured.out

    def test_clean_removal_returns_normally(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".trw").mkdir()
        (project / ".trw" / "config.yaml").write_text("x: 1\n")

        _run_uninstall(_ns(project))  # must not raise

        assert not (project / ".trw").exists()


@pytest.mark.integration
class TestUninstallRemoveIde:
    """CLIENT-REMOVE (installer refinement 5.1.0): ``uninstall --ide <client>``.

    ``uninstall --dry-run`` already computed the per-project manifest at
    client-owned granularity; this exercises the CLI surface that filters it
    to ONE client, deletes exactly what that client owns, and drops the
    client from ``target_platforms`` — leaving every other client and the
    shared framework-core surfaces untouched.
    """

    def _seed_project_venv(self, target: Path) -> None:
        launcher = target / ".venv" / "bin" / "trw-mcp"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\n", encoding="utf-8")
        launcher.chmod(0o755)

    def _two_client_project(self, tmp_path: Path) -> Path:
        from trw_mcp.bootstrap import init_project, update_project

        (tmp_path / ".git").mkdir()
        self._seed_project_venv(tmp_path)
        result = init_project(tmp_path, ide="claude-code")
        assert not result["errors"], result["errors"]
        update_result = update_project(tmp_path, ide="grok")
        assert not update_result["errors"], update_result["errors"]
        return tmp_path

    def test_uninstall_ide_deletes_only_that_clients_surfaces(self, tmp_path: Path) -> None:
        import tomllib

        project = self._two_client_project(tmp_path)

        _run_uninstall(_ns(project, ide="grok"))

        assert not (project / ".grok" / "agents").exists(), "grok's agent surface must be removed"
        # .grok/config.toml is a MERGED config (sec-006): only the TRW entry is
        # stripped, never wholesale-deleted, since it may hold user content.
        grok_config = project / ".grok" / "config.toml"
        if grok_config.is_file():
            assert "trw" not in tomllib.loads(grok_config.read_text(encoding="utf-8")).get("mcp_servers", {})
        assert (project / ".claude" / "agents").is_dir(), "claude-code's surfaces must survive"
        assert (project / ".trw").exists(), "framework-core .trw must survive a scoped removal"
        assert (project / ".mcp.json").exists(), "shared core .mcp.json must survive a scoped removal"

    def test_uninstall_ide_drops_the_platform_even_when_no_client_files_remain(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Review follow-up: a listed client with no surfaces left must still leave target_platforms."""
        import yaml

        (tmp_path / ".git").mkdir()
        (tmp_path / ".trw").mkdir()
        config_path = tmp_path / ".trw" / "config.yaml"
        config_path.write_text("target_platforms:\n- claude-code\n- grok\n", encoding="utf-8")
        # PRD-INFRA-192 FR09 NFR02: a scoped `--ide` uninstall refuses first
        # without a readable current-schema manifest, so this needs a minimal
        # valid one even though it has nothing recorded under it.
        (tmp_path / ".trw" / "managed-artifacts.yaml").write_text(
            "version: 2\ncontent_hashes: {}\nowners: {}\n", encoding="utf-8"
        )

        _run_uninstall(_ns(tmp_path, ide="grok"))
        assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["target_platforms"] == ["claude-code"]

        capsys.readouterr()
        _run_uninstall(_ns(tmp_path, ide="grok"))  # idempotent: nothing left to do, no error
        assert "No grok files found" in capsys.readouterr().out
        assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["target_platforms"] == ["claude-code"]

    def _agents_md_project(self, tmp_path: Path, *clients: str) -> Path:
        """claude-code plus *clients*, all installed; AGENTS.md carries TRW's managed block."""
        from trw_mcp.bootstrap import update_project

        project = self._two_client_project(tmp_path)  # claude-code + grok
        for client in clients:
            assert not update_project(project, ide=client)["errors"]
        assert "trw:start" in (project / "AGENTS.md").read_text(encoding="utf-8")
        return project

    def test_uninstall_ide_keeps_a_managed_block_another_recorded_client_declares(self, tmp_path: Path) -> None:
        """Repro: removing grok stripped AGENTS.md's TRW block although cursor-cli still declares it."""
        project = self._agents_md_project(tmp_path, "cursor-cli")

        _run_uninstall(_ns(project, ide="grok"))

        assert "trw:start" in (project / "AGENTS.md").read_text(encoding="utf-8"), (
            "cursor-cli is still recorded and declares AGENTS.md: its block must survive grok's removal"
        )

    def test_uninstall_ide_removes_the_block_when_no_remaining_client_declares_it(self, tmp_path: Path) -> None:
        """Inverse: grok is the last recorded client declaring AGENTS.md, so its block goes."""
        project = self._agents_md_project(tmp_path)

        _run_uninstall(_ns(project, ide="grok"))

        agents_md = project / "AGENTS.md"
        assert not agents_md.exists() or "trw:start" not in agents_md.read_text(encoding="utf-8")

    def test_uninstall_ide_drops_client_from_target_platforms(self, tmp_path: Path) -> None:
        import yaml

        project = self._two_client_project(tmp_path)
        config_path = project / ".trw" / "config.yaml"
        before = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "grok" in before.get("target_platforms", [])
        assert "claude-code" in before.get("target_platforms", [])

        _run_uninstall(_ns(project, ide="grok"))

        after = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert "grok" not in after.get("target_platforms", []), "grok must be dropped from target_platforms"
        assert "claude-code" in after.get("target_platforms", []), "claude-code must be preserved"

    def test_uninstall_ide_dry_run_does_not_delete_or_mutate_config(self, tmp_path: Path) -> None:
        project = self._two_client_project(tmp_path)
        config_path = project / ".trw" / "config.yaml"
        before_text = config_path.read_text(encoding="utf-8")

        _run_uninstall(_ns(project, dry_run=True, ide="grok"))

        assert (project / ".grok").exists(), "dry-run must not delete anything"
        assert config_path.read_text(encoding="utf-8") == before_text, "dry-run must not mutate config.yaml"

    def test_uninstall_ide_is_idempotent(self, tmp_path: Path) -> None:
        """A second removal of an already-removed client is a clean no-op."""
        project = self._two_client_project(tmp_path)
        _run_uninstall(_ns(project, ide="grok"))

        _run_uninstall(_ns(project, ide="grok"))  # must not raise

        assert not (project / ".grok" / "agents").exists()


@pytest.mark.integration
class TestUninstallClaudeSurfaceOwnership:
    """PRD-INFRA-192 FR09 (C7): ``.claude/**`` and ``.mcp.json`` are claude-code's
    own uninstall surfaces (moved out of ``_CORE_SURFACES``), except
    ``.claude/hooks`` -- shared with codex and copilot, whose own hook commands
    also run scripts from there. Before the fix, ``uninstall --ide claude-code``
    left the ``trw`` server registered in ``.mcp.json`` and every TRW file under
    ``.claude/`` on disk, because the catalog classified them as core (written
    for, and removed for, every client alike).
    """

    def test_claude_code_only_uninstall_strips_mcp_entry_and_removes_claude_files(self, tmp_path: Path) -> None:
        """(a) init --ide claude-code, then uninstall --ide claude-code."""
        import json

        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="claude-code")
        assert not result["errors"], result["errors"]

        mcp_json = tmp_path / ".mcp.json"
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert "trw" in data.get("mcpServers", {}), "precondition: init wrote the trw server entry"
        data["mcpServers"]["other"] = {"command": "other-server"}  # a pre-seeded user server
        mcp_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        _run_uninstall(_ns(tmp_path, ide="claude-code"))

        after = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert "trw" not in after.get("mcpServers", {}), "the trw server entry must be stripped"
        assert "other" in after.get("mcpServers", {}), "a pre-seeded user server must survive"
        assert not (tmp_path / ".claude" / "skills").exists(), "claude-code's skills surface must be removed"
        assert not (tmp_path / ".claude" / "agents").exists(), "claude-code's agents surface must be removed"
        assert not (tmp_path / ".claude" / "hooks").exists(), "no other client owns .claude/hooks here"
        assert not (tmp_path / ".claude" / "loop.md").exists()
        settings = tmp_path / ".claude" / "settings.json"
        if settings.is_file():
            assert ".claude/hooks/" not in settings.read_text(encoding="utf-8"), (
                "no dangling TRW hook command may remain registered"
            )

    def test_uninstall_codex_keeps_claude_hooks_when_claude_code_still_recorded(self, tmp_path: Path) -> None:
        """(f) [claude-code, codex]: uninstalling codex keeps .claude/hooks (claude-code owns it),
        and removes claude-code's exclusive skills/agents surfaces only if codex asked for them
        (it never installed them) -- this exercises the inverse of (a): the SHARED surface
        survives a scoped removal of one of its two owners.
        """
        from trw_mcp.bootstrap import init_project, update_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="claude-code")
        assert not result["errors"], result["errors"]
        update_result = update_project(tmp_path, ide="codex")
        assert not update_result["errors"], update_result["errors"]
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "precondition: hooks installed"

        _run_uninstall(_ns(tmp_path, ide="codex"))

        assert (tmp_path / ".claude" / "hooks").is_dir(), "claude-code still owns .claude/hooks"
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "the hook scripts themselves must survive"
        assert (tmp_path / ".claude" / "skills").is_dir(), "claude-code's own surfaces are untouched"
        assert (tmp_path / ".claude" / "agents").is_dir()
        assert not (tmp_path / ".codex" / "agents").exists(), "codex's own exclusive surfaces are removed"

    def test_uninstall_codex_removes_claude_hooks_when_claude_code_not_recorded(self, tmp_path: Path) -> None:
        """(coordinator addition) codex-only project: uninstalling codex removes
        .claude/hooks' TRW scripts, since no owner remains."""
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="codex")
        assert not result["errors"], result["errors"]
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "precondition: codex-only still gets hooks"

        _run_uninstall(_ns(tmp_path, ide="codex"))

        assert not (tmp_path / ".claude" / "hooks").exists(), "no remaining recorded client owns .claude/hooks"

    def test_uninstall_codex_keeps_claude_hooks_when_copilot_still_recorded(self, tmp_path: Path) -> None:
        """(coordinator addition) [codex, copilot]: uninstalling codex keeps
        .claude/hooks, because copilot still owns it."""
        from trw_mcp.bootstrap import init_project, update_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="codex")
        assert not result["errors"], result["errors"]
        update_result = update_project(tmp_path, ide="copilot")
        assert not update_result["errors"], update_result["errors"]
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "precondition: hooks installed"

        _run_uninstall(_ns(tmp_path, ide="codex"))

        assert (tmp_path / ".claude" / "hooks").is_dir(), "copilot still owns .claude/hooks"
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "the hook scripts themselves must survive"

    def test_uninstall_copilot_keeps_claude_hooks_when_codex_still_recorded(self, tmp_path: Path) -> None:
        """(coordinator addition) [codex, copilot]: uninstalling copilot keeps
        .claude/hooks, because codex still owns it."""
        from trw_mcp.bootstrap import init_project, update_project

        (tmp_path / ".git").mkdir()
        result = init_project(tmp_path, ide="codex")
        assert not result["errors"], result["errors"]
        update_result = update_project(tmp_path, ide="copilot")
        assert not update_result["errors"], update_result["errors"]
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "precondition: hooks installed"

        _run_uninstall(_ns(tmp_path, ide="copilot"))

        assert (tmp_path / ".claude" / "hooks").is_dir(), "codex still owns .claude/hooks"
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "the hook scripts themselves must survive"
