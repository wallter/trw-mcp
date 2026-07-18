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
        """With --yes, removes files without prompting."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".mcp.json").write_text("{}")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (tmp_path / ".trw").exists()
        assert not (tmp_path / ".mcp.json").exists()

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

    def test_removes_claude_subdirs(self, tmp_path: Path) -> None:
        """Removes .claude/skills, .claude/agents, .claude/hooks but preserves .claude/ itself."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "skills").mkdir()
        (claude_dir / "skills" / "trw-review-pr").mkdir(parents=True)
        (claude_dir / "skills" / "trw-review-pr" / "SKILL.md").write_text("# Skill")
        (claude_dir / "agents").mkdir()
        (claude_dir / "agents" / "reviewer.md").write_text("# Agent")
        (claude_dir / "hooks").mkdir()
        (claude_dir / "hooks" / "lib-trw.sh").write_text("#!/bin/bash")
        # User file outside TRW-managed dirs
        (claude_dir / "settings.json").write_text("{}")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (claude_dir / "skills").exists()
        assert not (claude_dir / "agents").exists()
        assert not (claude_dir / "hooks").exists()
        # .claude/ itself and user files preserved
        assert claude_dir.exists()
        assert (claude_dir / "settings.json").exists()

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


def _ns(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    """Build an uninstall argparse Namespace with sensible defaults."""
    base: dict[str, object] = {
        "target_dir": str(tmp_path),
        "dry_run": False,
        "yes": True,
        "user_tier": False,
        "keep_memory": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.mark.integration
class TestUninstallRegistryProfiles:
    """PRD-SEC-006 FR07: uninstall is registry-driven across all 9 profiles."""

    def test_removes_all_profile_config_dirs(self, tmp_path: Path) -> None:
        """Each client profile's config-dir surfaces are removed.

        Merged config files (.codex/config.toml, .gemini/settings.json) are
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
            assert not s.exists(), f"{s} should have been removed"

    def test_merged_config_files_not_wholesale_deleted(self, tmp_path: Path) -> None:
        """sec-006: merged config files (settings.json/config.toml) are NOT deleted.

        They may contain user-owned settings; only the TRW server entry is
        stripped. A file with no TRW entry is preserved verbatim.
        """
        gemini = tmp_path / ".gemini" / "settings.json"
        gemini.parent.mkdir(parents=True)
        gemini.write_text('{"theme": "dark"}')  # pure user content, no trw key
        codex = tmp_path / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text('model = "gpt-5"\n')  # pure user content, no trw table
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        # Both preserved because neither holds a TRW server entry.
        assert gemini.exists(), "user gemini settings.json wholesale-deleted"
        assert '"theme": "dark"' in gemini.read_text()
        assert codex.exists(), "user codex config.toml wholesale-deleted"
        assert 'model = "gpt-5"' in codex.read_text()

    def test_merged_config_strips_only_trw_server_entry(self, tmp_path: Path) -> None:
        """The trw mcp server entry is stripped, user servers/keys preserved."""
        import json

        gemini = tmp_path / ".gemini" / "settings.json"
        gemini.parent.mkdir(parents=True)
        gemini.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {
                        "trw": {"command": "trw-mcp"},
                        "other": {"command": "other-server"},
                    },
                }
            )
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert gemini.exists()
        data = json.loads(gemini.read_text())
        assert data["theme"] == "dark"
        assert "trw" not in data.get("mcpServers", {})
        assert "other" in data["mcpServers"]

    def test_merged_config_strips_codex_trw_table(self, tmp_path: Path) -> None:
        """Codex config.toml [mcp_servers.trw] table stripped, rest preserved."""
        codex = tmp_path / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text(
            'model = "gpt-5"\n\n[mcp_servers.trw]\ncommand = "trw-mcp"\n\n[mcp_servers.other]\ncommand = "other"\n'
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
        """A shared file that has no TRW markers is left entirely alone."""
        gemini = tmp_path / "GEMINI.md"
        gemini.write_text("# Pure user GEMINI.md\nno trw markers\n")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert gemini.exists()
        assert gemini.read_text() == "# Pure user GEMINI.md\nno trw markers\n"

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
        """FIX 1: a TRW group + a user group leaves only the user group; file kept."""
        import json

        hooks = tmp_path / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "description": "TRW managed: SessionStart",
                                "hooks": [{"type": "command", "command": "trw"}],
                            },
                            {
                                "description": "My custom hook",
                                "hooks": [{"type": "command", "command": "mine"}],
                            },
                        ]
                    }
                }
            )
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists(), "hooks.json with a user group must be preserved"
        data = json.loads(hooks.read_text())
        groups = data["hooks"]["SessionStart"]
        assert len(groups) == 1
        assert groups[0]["description"] == "My custom hook"

    def test_codex_hooks_json_all_trw_deleted(self, tmp_path: Path) -> None:
        """FIX 1: a hooks.json containing only TRW groups is deleted."""
        import json

        hooks = tmp_path / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [{"description": "TRW managed: SessionStart", "hooks": []}],
                        "Stop": [{"description": "TRW managed: Stop", "hooks": []}],
                    }
                }
            )
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not hooks.exists()

    def test_copilot_hooks_json_all_trw_deleted_with_version(self, tmp_path: Path) -> None:
        """FIX 3: copilot hooks.json (version + only TRW groups) is deleted."""
        import json

        hooks = tmp_path / ".github" / "hooks" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {"sessionStart": [{"description": "TRW managed: session", "hooks": []}]},
                }
            )
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not hooks.exists(), "version-only + empty hooks is not user content"

    def test_copilot_hooks_json_preserves_user_group_and_unknown_keys(self, tmp_path: Path) -> None:
        """FIX 3: user groups and unknown top-level keys survive TRW-group stripping."""
        import json

        hooks = tmp_path / ".github" / "hooks" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(
            json.dumps(
                {
                    "version": 1,
                    "customTop": {"keep": True},
                    "hooks": {
                        "sessionStart": [
                            {"description": "TRW managed: session", "hooks": []},
                            {"description": "user group", "hooks": [{"type": "command", "command": "u"}]},
                        ]
                    },
                }
            )
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert hooks.exists()
        data = json.loads(hooks.read_text())
        assert data["customTop"] == {"keep": True}
        groups = data["hooks"]["sessionStart"]
        assert len(groups) == 1
        assert groups[0]["description"] == "user group"

    def test_antigravity_preserves_user_files_strips_trw(self, tmp_path: Path) -> None:
        """FIX 2: user files under .antigravitycli survive; TRW entry + subdirs removed."""
        import json

        ag = tmp_path / ".antigravitycli"
        agents = ag / "agents"
        agents.mkdir(parents=True)
        (agents / "trw-explorer.md").write_text("trw")
        settings = ag / "settings.json"
        settings.write_text(json.dumps({"mcpServers": {"trw": {"command": "trw-mcp"}, "mine": {"command": "m"}}}))
        user_file = ag / "my-notes.md"
        user_file.write_text("keep me")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        # TRW agents subdir removed
        assert not agents.exists()
        # settings.json preserved with only the trw server entry stripped
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert "trw" not in data["mcpServers"]
        assert "mine" in data["mcpServers"]
        # user file elsewhere under the dir survives (no rmtree of .antigravitycli)
        assert user_file.exists()
        assert ag.exists()

    def test_antigravity_uninstall_removes_live_ag03_hook(self, tmp_path: Path) -> None:
        """P1: install the real AG-03 hook, then uninstall must leave no live hook.

        Regression for the narrowed antigravity surface that dropped
        hooks.json + hooks/ cleanup while install_before_edit_hook still wrote
        (and registered) a PreToolUse hook -- uninstall left the hook live.
        """
        from trw_mcp.channels.antigravity._before_edit_hook import (
            _AG03_HOOK_SCRIPT_PATH,
            AG03_HOOKS_PATH,
            install_before_edit_hook,
        )

        result = install_before_edit_hook(tmp_path)
        assert result["installed"] is True
        hooks_json = tmp_path / AG03_HOOKS_PATH
        hook_script = tmp_path / _AG03_HOOK_SCRIPT_PATH
        assert hooks_json.exists()
        assert hook_script.exists()
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        # No live TRW hook may remain: both the registration file and the script.
        assert not hooks_json.exists(), "uninstall left the registered AG-03 hook behind"
        assert not hook_script.exists(), "uninstall left the AG-03 hook script behind"
        assert not (tmp_path / ".antigravitycli" / "hooks").exists()

    def test_gemini_agents_and_github_skills_removed(self, tmp_path: Path) -> None:
        """FIX 3: .gemini/agents + .github/skills TRW artifacts are removed."""
        gem_agents = tmp_path / ".gemini" / "agents"
        gem_agents.mkdir(parents=True)
        (gem_agents / "trw-explorer.md").write_text("x")
        gem_hooks = tmp_path / ".gemini" / "hooks"
        gem_hooks.mkdir(parents=True)
        (gem_hooks / "trw-before-tool-hint.sh").write_text("#!/bin/sh")
        gh_skill = tmp_path / ".github" / "skills" / "trw-review-pr"
        gh_skill.mkdir(parents=True)
        (gh_skill / "SKILL.md").write_text("# skill")
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert not gem_agents.exists()
        assert not gem_hooks.exists()
        assert not (tmp_path / ".github" / "skills").exists()

    def test_gemini_settings_strips_before_tool_hook_and_server(self, tmp_path: Path) -> None:
        """FIX 3: gemini settings.json strips mcpServers.trw AND the BeforeTool block."""
        import json

        settings = tmp_path / ".gemini" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {"trw": {"command": "trw-mcp"}},
                    "hooks": {
                        "BeforeTool": [
                            {
                                "matcher": "write_file",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "name": "trw-distill-before-edit-hint",
                                        "command": "sh x",
                                    }
                                ],
                            },
                            {
                                "matcher": "user",
                                "hooks": [{"type": "command", "name": "user-hook", "command": "sh y"}],
                            },
                        ]
                    },
                }
            )
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert settings.exists()
        data = json.loads(settings.read_text())
        assert data["theme"] == "dark"
        # trw was the only server -> container key dropped
        assert "mcpServers" not in data
        before = data["hooks"]["BeforeTool"]
        assert len(before) == 1
        assert before[0]["hooks"][0]["name"] == "user-hook"

    def test_github_instructions_only_trw_files_removed(self, tmp_path: Path) -> None:
        """FIX 3: only the specific TRW instruction files are removed; user file kept."""
        instr = tmp_path / ".github" / "instructions"
        instr.mkdir(parents=True)
        (instr / "python-testing.instructions.md").write_text("trw")
        (instr / "typescript-react.instructions.md").write_text("trw")
        user = instr / "my-own.instructions.md"
        user.write_text("mine")
        (tmp_path / ".trw").mkdir()

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
        mcp.write_text(json.dumps({"mcpServers": {"trw": {"command": "trw-mcp"}, "other": {"command": "o"}}}))
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert mcp.exists(), "user .cursor/mcp.json must not be wholesale-deleted"
        data = json.loads(mcp.read_text())
        assert "trw" not in data["mcpServers"]
        assert "other" in data["mcpServers"]


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
        """Uninstall over a missing-end-marker file warns + preserves content."""
        from structlog.testing import capture_logs

        from trw_mcp.server._subcommands_lifecycle import _remove_managed_block_file

        f = tmp_path / "AGENTS.md"
        original = "user\n<!-- trw:start -->\norphan\nmore user\n"
        f.write_text(original)
        with capture_logs() as logs:
            status = _remove_managed_block_file(f, dry_run=False)
        assert status is None
        assert f.read_text() == original
        events = {e.get("event") for e in logs}
        assert "uninstall_marker_unbalanced" in events


@pytest.mark.integration
class TestUninstallUserTier:
    """PRD-SEC-006 FR07: --user-tier removes ~/.trw."""

    def test_user_tier_removes_home_trw(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With --user-tier, ~/.trw is removed."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        user_trw = fake_home / ".trw"
        user_trw.mkdir()
        (user_trw / "memory.db").write_text("db")
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".trw").mkdir()

        _run_uninstall(_ns(project, user_tier=True))

        assert not user_trw.exists()

    def test_default_preserves_home_trw(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --user-tier, ~/.trw is preserved (default project-only)."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        user_trw = fake_home / ".trw"
        user_trw.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".trw").mkdir()

        _run_uninstall(_ns(project, user_tier=False))

        assert user_trw.exists()

    def test_user_tier_does_not_remove_project_home_collision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """~/.trw removal is skipped when project root IS the home dir."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        user_trw = fake_home / ".trw"
        user_trw.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        # Project target == home: the project .trw and the user-tier .trw are
        # the same dir; it is removed once as a project surface, not twice.
        _run_uninstall(_ns(fake_home, user_tier=True))

        assert not user_trw.exists()


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
        assert ".gemini/settings.json" in relpaths
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
        # These are FLAT ``{event: [entry]}`` files owned solely by TRW, not the
        # codex/copilot ``{"hooks": {event: [group]}}`` shape, so they are plain
        # whole-file removals -- NOT hook-group-list merged strips (which would be a
        # no-op on this shape and leave the hook behind).
        assert by_path[".antigravitycli/hooks.json"].merged_config is False
        assert by_path[".antigravitycli/hooks"].merged_config is False
        # FIX 3: previously-missing bootstrap surfaces.
        assert ".gemini/agents" in relpaths
        assert ".gemini/hooks" in relpaths
        assert ".github/skills" in relpaths
        assert ".github/hooks/hooks.json" in relpaths
        assert ".github/hooks/trw-copilot-adapter.sh" in relpaths
        # SHARED .github/instructions: only the specific TRW files, not the dir.
        assert ".github/instructions" not in relpaths
        assert ".github/instructions/python-testing.instructions.md" in relpaths
        assert ".github/instructions/typescript-react.instructions.md" in relpaths
        # FIX 4: .cursor/mcp.json (distinct from the root .mcp.json).
        assert ".cursor/mcp.json" in relpaths

    def test_manifest_marks_shared_files_as_managed_blocks(self) -> None:
        """Shared root instruction files are managed-block surfaces."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        assert by_path["AGENTS.md"].managed_block is True
        assert by_path["GEMINI.md"].managed_block is True
        # config dirs are plain removals
        assert by_path[".opencode/agents"].managed_block is False

    def test_manifest_covers_both_retired_instruction_surfaces(self) -> None:
        """Both retired clients (gemini, aider) keep their instruction surface so
        pre-retirement installs stay removable forever (release-verify P1)."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        assert by_path["GEMINI.md"].managed_block is True
        assert by_path[".aider/instructions.md"].managed_block is True

    def test_manifest_merged_config_surfaces_carry_shapes(self) -> None:
        """Merged-config surfaces declare the correct strip strategy shape."""
        from trw_mcp.client_profiles.catalog import uninstall_surfaces

        by_path = {s.relpath: s for s in uninstall_surfaces()}
        expected = {
            ".codex/config.toml": "codex-toml",
            ".codex/hooks.json": "hook-group-list",
            ".github/hooks/hooks.json": "hook-group-list",
            ".gemini/settings.json": "gemini-settings",
            ".cursor/mcp.json": "mcp-server-map",
            ".antigravitycli/settings.json": "mcp-server-map",
        }
        for relpath, shape in expected.items():
            surface = by_path[relpath]
            assert surface.merged_config is True, f"{relpath} should be merged_config"
            assert surface.config_shape == shape, f"{relpath} shape mismatch"
        # TRW-created plain files under shared dirs stay plain removals.
        assert by_path[".github/hooks/trw-copilot-adapter.sh"].merged_config is False
        assert by_path[".github/instructions/python-testing.instructions.md"].merged_config is False


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
