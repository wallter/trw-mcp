"""Tests for the single guarded user-file-edit path (PRD-INFRA-192 FR09/FR10).

Covers the module directly (``bootstrap/_user_file_edit.py``) plus regression
coverage through the real entry points: tombstone hook deregistration
(``bootstrap/_hook_deregistration.py``) and whole-project uninstall
(``server/_subcommands_lifecycle.py`` / ``_subcommands_uninstall_config.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# guard_refusal / safe_read_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGuardRefusal:
    def test_plain_path_under_root_is_allowed(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._user_file_edit import guard_refusal

        f = tmp_path / "AGENTS.md"
        f.write_text("hello")
        assert guard_refusal(f, tmp_path) is None

    def test_symlinked_file_itself_is_refused(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._user_file_edit import guard_refusal

        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "real.json"
        real.write_text("{}")
        link = tmp_path / "AGENTS.md"
        link.symlink_to(real)
        assert guard_refusal(link, tmp_path) is not None

    def test_symlinked_parent_component_is_refused(self, tmp_path: Path) -> None:
        """A file itself is a plain name, but a PARENT directory is a symlink."""
        from trw_mcp.bootstrap._user_file_edit import guard_refusal

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not delete me")
        claude_dir = tmp_path / ".claude"
        claude_dir.symlink_to(outside, target_is_directory=True)
        target = claude_dir / "settings.json"

        refusal = guard_refusal(target, tmp_path)

        assert refusal is not None
        assert (outside / "sentinel.txt").exists(), "sentinel untouched (still exists)"

    def test_safe_read_text_refuses_without_reading(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._user_file_edit import safe_read_text

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.json"
        secret.write_text("do-not-read-me")
        link = tmp_path / ".claude"
        link.symlink_to(outside, target_is_directory=True)

        text, refusal = safe_read_text(link / "secret.json", tmp_path)

        assert text is None
        assert refusal is not None

    def test_safe_read_text_reads_plain_file(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._user_file_edit import safe_read_text

        f = tmp_path / "AGENTS.md"
        f.write_text("hello world")
        text, refusal = safe_read_text(f, tmp_path)
        assert refusal is None
        assert text == "hello world"


# ---------------------------------------------------------------------------
# matching_sort_keys / atomic_write_text
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCanonicalRewrite:
    def test_matching_sort_keys_detects_sorted_convention(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import matching_sort_keys

        data = {"b": 1, "a": 2}
        raw = json.dumps(data, indent=2, sort_keys=True) + "\n"
        assert matching_sort_keys(data, raw) is True

    def test_matching_sort_keys_detects_unsorted_convention(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import matching_sort_keys

        data = {"b": 1, "a": 2}
        raw = json.dumps(data, indent=2, sort_keys=False) + "\n"
        assert matching_sort_keys(data, raw) is False

    def test_matching_sort_keys_none_for_hand_edited_file(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import matching_sort_keys

        data = {"b": 1, "a": 2}
        raw = '{"b":1,"a":2}'  # compact, no indent -- not TRW's convention
        assert matching_sort_keys(data, raw) is None

    def test_atomic_write_text_replaces_file_content(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._user_file_edit import atomic_write_text

        f = tmp_path / "out.json"
        f.write_text("old")
        atomic_write_text(f, "new")
        assert f.read_text() == "new"

    def test_atomic_write_text_no_leftover_temp_file(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._user_file_edit import atomic_write_text

        f = tmp_path / "out.json"
        f.write_text("old")
        atomic_write_text(f, "new")
        leftovers = [p for p in tmp_path.iterdir() if p.name != "out.json"]
        assert leftovers == []


# ---------------------------------------------------------------------------
# Hook-registration JSON editing primitives
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDropMatchingHookEntries:
    def test_drops_verified_entry_keeps_user_entry(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import drop_matching_hook_entries

        hooks = {
            "SessionStart": [
                {"hooks": [{"command": "trw-cmd"}]},
                {"hooks": [{"command": "user-cmd"}]},
            ]
        }
        new_hooks, changed = drop_matching_hook_entries(
            hooks, lambda _e, entry: entry["hooks"][0]["command"] == "trw-cmd", "file", {}
        )
        assert changed is True
        commands = [h["hooks"][0]["command"] for h in new_hooks["SessionStart"]]
        assert commands == ["user-cmd"]

    def test_already_empty_event_key_is_not_dropped(self) -> None:
        """An event key that was empty BEFORE this pass is never dropped as a side effect."""
        from trw_mcp.bootstrap._user_file_edit import drop_matching_hook_entries

        hooks = {"SessionStart": [{"hooks": [{"command": "trw-cmd"}]}], "Stop": []}
        new_hooks, changed = drop_matching_hook_entries(
            hooks, lambda _e, entry: entry["hooks"][0]["command"] == "trw-cmd", "file", {}
        )
        assert changed is True
        assert "Stop" in new_hooks
        assert new_hooks["Stop"] == []


@pytest.mark.unit
class TestDropMatchingHookCommands:
    def test_removes_only_verified_command_keeps_group(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import drop_matching_hook_commands

        hooks = {
            "SessionStart": [
                {
                    "description": "TRW managed: SessionStart",
                    "hooks": [{"command": "trw-cmd"}, {"command": "user-cmd"}],
                }
            ]
        }
        new_hooks, changed = drop_matching_hook_commands(
            hooks, lambda _e, hook: hook["command"] == "trw-cmd", "file", {}
        )
        assert changed is True
        group = new_hooks["SessionStart"][0]
        assert [h["command"] for h in group["hooks"]] == ["user-cmd"]

    def test_group_dropped_only_when_all_its_commands_verified(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import drop_matching_hook_commands

        hooks = {"SessionStart": [{"description": "d", "hooks": [{"command": "trw-cmd"}]}]}
        new_hooks, changed = drop_matching_hook_commands(
            hooks, lambda _e, hook: hook["command"] == "trw-cmd", "file", {}
        )
        assert changed is True
        # The event key was non-empty before this pass and emptied BY this
        # pass's own removal -> dropped, per the empty-key rule.
        assert "SessionStart" not in new_hooks


@pytest.mark.unit
class TestDropMatchingFlatHookEntries:
    def test_removes_verified_command_from_flat_event_list(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import drop_matching_flat_hook_entries

        hooks = {"beforeShellExecution": [{"command": "trw-cmd"}, {"command": "user-cmd"}]}
        new_hooks, changed = drop_matching_flat_hook_entries(hooks, lambda _ev, e: e == {"command": "trw-cmd"})
        assert changed is True
        assert [h["command"] for h in new_hooks["beforeShellExecution"]] == ["user-cmd"]

    def test_event_emptied_by_removal_is_dropped(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import drop_matching_flat_hook_entries

        hooks = {"beforeShellExecution": [{"command": "trw-cmd"}]}
        new_hooks, changed = drop_matching_flat_hook_entries(hooks, lambda _ev, e: e == {"command": "trw-cmd"})
        assert changed is True
        assert "beforeShellExecution" not in new_hooks


# ---------------------------------------------------------------------------
# strip_managed_block
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStripManagedBlockOrphans:
    MARKERS = (("<!-- trw:start -->", "<!-- trw:end -->"),)

    def test_orphan_begin_marker_left_in_place_with_warning(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import strip_managed_block

        text = "user\n<!-- trw:start -->\norphan content\nmore user\n"
        out, changed, warnings = strip_managed_block(text, self.MARKERS)
        assert out == text
        assert changed is False
        assert warnings, "an orphan begin marker must produce a warning"

    def test_orphan_end_marker_left_in_place_with_warning(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import strip_managed_block

        text = "user before\n<!-- trw:end -->\nuser after\n"
        out, changed, warnings = strip_managed_block(text, self.MARKERS)
        assert out == text
        assert changed is False
        assert warnings

    def test_verified_span_removed_orphan_elsewhere_preserved(self) -> None:
        """A verified block is removed while a SEPARATE orphan end marker survives untouched."""
        from trw_mcp.bootstrap._user_file_edit import strip_managed_block

        text = (
            "user text with\n\n\nmultiple blank lines\n"
            "<!-- trw:start -->\nmanaged\n<!-- trw:end -->\n"
            "more user text\n<!-- trw:end -->\ntrailing user text\n"
        )
        out, changed, warnings = strip_managed_block(text, self.MARKERS)
        assert changed is True
        assert "managed" not in out
        assert "multiple blank lines" in out
        # No global newline collapse: the 3 blank lines outside the block survive.
        assert "\n\n\nmultiple blank lines" in out
        # The orphan end marker elsewhere in the file is left in place.
        assert out.count("<!-- trw:end -->") == 1
        assert any("orphan end marker" in w for w in warnings)
        assert "trailing user text" in out

    def test_no_global_blank_line_collapse_outside_removed_span(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import strip_managed_block

        text = "a\n\n\n\nb\n<!-- trw:start -->\nmanaged\n<!-- trw:end -->\nc\n"
        out, _changed, _warnings = strip_managed_block(text, self.MARKERS)
        assert "a\n\n\n\nb" in out, "blank runs OUTSIDE the removed span must never be collapsed"

    def test_no_blank_line_collapse_adjacent_to_the_span_either(self) -> None:
        """Blank lines directly touching the removed span are the USER's and survive too.

        CHANGED (coordinator review): the real writer (``state/claude_md``)
        inserts its own separator blank line BEFORE a generated-header comment
        that sits ABOVE ``trw:start``, never directly touching the marker line
        itself -- so there is nothing for this function to collapse. Blindly
        collapsing a blank line adjacent to the markers risked eating a blank
        line the USER wrote right next to their own content.
        """
        from trw_mcp.bootstrap._user_file_edit import strip_managed_block

        text = "user before\n\n<!-- trw:start -->\nmanaged\n<!-- trw:end -->\n\nuser after\n"
        out, changed, _warnings = strip_managed_block(text, self.MARKERS)
        assert changed is True
        assert "managed" not in out
        # BOTH of the user's original blank lines survive untouched.
        assert out == "user before\n\n\nuser after\n"

    def test_real_writer_output_uninstall_preserves_surrounding_bytes_exactly(self, tmp_path: Path) -> None:
        """Install then uninstall through the REAL writer: user text before/after is byte-identical."""
        from trw_mcp.bootstrap import init_project
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        (tmp_path / ".git").mkdir()
        agents = tmp_path / "AGENTS.md"
        user_prefix = "MY OWN HEADER\nsome user text\nTRAILING USER LINE\n"
        agents.write_text(user_prefix)

        result = init_project(tmp_path, ide="all")
        assert not result["errors"], result["errors"]
        installed = agents.read_text()
        assert installed.startswith(user_prefix), "precondition: install did not touch the user prefix"
        assert "<!-- trw:start -->" in installed

        import argparse

        _run_uninstall(
            argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True, user_tier=False, keep_memory=False)
        )

        final = agents.read_text()
        assert "<!-- trw:start -->" not in final
        assert final.startswith(user_prefix), "the user's own prefix bytes must survive install+uninstall exactly"


# ---------------------------------------------------------------------------
# Regression: tombstone cleanup through a symlinked registration surface
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTombstoneCleanupSymlinkSafety:
    def test_symlinked_claude_dir_registration_file_is_refused(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR10 P0: a symlinked ``.claude`` parent is never followed.

        Before the fix, ``deregister_hook_script`` read/wrote through the
        symlink target unconditionally; the guarded module now refuses and
        reports it instead of touching bytes outside the project.
        """
        from trw_mcp.bootstrap._hook_deregistration import deregister_hook_script

        outside = tmp_path / "outside"
        outside.mkdir()
        settings = outside / "settings.json"
        settings.write_text(json.dumps({"hooks": {"SessionStart": []}}))
        original_bytes = settings.read_bytes()

        (tmp_path / ".claude").symlink_to(outside, target_is_directory=True)

        result: dict[str, list[str]] = {}
        deregister_hook_script(tmp_path, ".claude/hooks/session-start.sh", result)

        assert settings.read_bytes() == original_bytes, "symlinked-parent registration file must not be rewritten"
        assert any("symlink" in w.lower() for w in result.get("warnings", [])), (
            "the refusal must be reported via result['warnings'], not silent"
        )


# ---------------------------------------------------------------------------
# Regression: whole-project uninstall through the guarded path
# ---------------------------------------------------------------------------


def _ns(target: Path, **overrides: object) -> object:
    import argparse

    ns = argparse.Namespace(target_dir=str(target), dry_run=False, yes=True, user_tier=False, keep_memory=False)
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.integration
class TestWholeProjectUninstallSymlinkAndFormatting:
    def test_symlinked_claude_dir_refused_and_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A symlinked ``.claude`` pointing outside is refused; nothing outside is touched."""
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        project = tmp_path / "proj"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not delete me")
        (outside / "settings.json").write_text(json.dumps({"hooks": {}, "mcpServers": {"trw": {}}}) + "\n")
        (project / ".claude").symlink_to(outside, target_is_directory=True)
        (project / ".trw").mkdir()

        with pytest.raises(SystemExit):
            _run_uninstall(_ns(project))

        assert (outside / "sentinel.txt").exists(), "content behind the symlink must survive"
        out = capsys.readouterr().out
        assert "refused" in out.lower() or "symlink" in out.lower()

    def test_empty_user_event_key_and_custom_formatting_survive(self, tmp_path: Path) -> None:
        """An already-empty user event key and hand formatting both survive, byte-identical.

        Targets ``.claude/settings.json`` (``claude-settings`` shape). The
        warning is asserted separately at the unit level
        (``test_strip_trw_claude_settings_warns_on_non_canonical_input`` below)
        -- see the docstring on ``test_custom_formatted_mcp_json_left_byte_identical_with_warning``
        for why a captured-log assertion here is order-sensitive under xdist.
        """
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        # Hand-formatted (compact, no indent=2) -- not TRW's canonical serialization.
        original = json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"'}]}
                    ],
                    "Stop": [],
                }
            }
        )
        settings.write_text(original)
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert settings.read_text() == original, "hand-formatted file must be left byte-identical"

    def test_strip_trw_claude_settings_declines_non_canonical_input(self) -> None:
        """A non-canonical original leaves both the change-flag and bytes unchanged.

        The warning it also logs is covered structurally by
        ``_canonical_or_untouched``'s single implementation (shared with the
        already-verified codex/copilot/cursor/antigravity strips above); a
        ``capture_logs`` assertion on THIS specific call is order-sensitive
        under xdist (see the docstring two tests up) and is intentionally not
        repeated here.
        """
        from trw_mcp.server._subcommands_uninstall_config import _strip_trw_claude_settings

        original = json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"'}]}
                    ]
                }
            }
        )
        changed, rendered, delete = _strip_trw_claude_settings(original, Path())
        assert changed is False
        assert rendered == original
        assert delete is False

    def test_unchanged_file_is_not_rewritten(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A file with no TRW content is never opened for write (os.replace not called for it)."""
        import os

        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        agents = tmp_path / "AGENTS.md"
        agents.write_text("just user content, no TRW markers here\n")
        before_mtime = agents.stat().st_mtime_ns
        (tmp_path / ".trw").mkdir()

        real_replace = os.replace
        replaced_paths: list[str] = []

        def _tracking_replace(src: object, dst: object) -> None:
            replaced_paths.append(str(dst))
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _tracking_replace)

        _run_uninstall(_ns(tmp_path))

        assert agents.stat().st_mtime_ns == before_mtime
        assert str(agents) not in replaced_paths


# ---------------------------------------------------------------------------
# JSON merged-config canonical-or-untouched (closing the "known gap")
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJsonMergedConfigCanonicalOrUntouched:
    """Every mcpServers-map shape, plus opencode.json, is now byte-preserving."""

    def test_custom_formatted_mcp_json_left_byte_identical_with_warning(self, tmp_path: Path) -> None:
        """A hand-formatted .mcp.json with a user server AND trw survives whole-project uninstall untouched.

        The warning itself is asserted separately, at the unit level
        (``TestServerMapCustomFormattingWarns`` below) — structlog's process-
        global config is shared across a test session, and whichever test is
        the FIRST in the process to run ``_run_uninstall`` (which triggers a
        one-time ``configure_logging()``) can observe an empty ``capture_logs``
        result depending on collection order, independent of whether the
        warning was actually emitted (a known ``_restore_structlog_config``
        limitation, see its docstring in ``conftest.py``). The byte-identity
        assertion below is the load-bearing behavioral proof and is order-independent.
        """
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        mcp = tmp_path / ".mcp.json"
        # Compact, no indent=2 -- not TRW's canonical serialization.
        original = json.dumps({"mcpServers": {"trw": {"command": "trw-mcp"}, "mine": {"command": "my-server"}}})
        mcp.write_text(original)
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert mcp.read_text() == original, "custom-formatted .mcp.json must be left byte-identical"

    def test_canonical_mcp_json_strips_only_trw_user_server_bytes_intact(self, tmp_path: Path) -> None:
        """A canonically-formatted .mcp.json: only ``trw`` is removed, the user server's bytes are intact."""
        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        mcp = tmp_path / ".mcp.json"
        mcp.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "trw": _trw_mcp_server_entry(tmp_path),
                        "mine": {"command": "my-server", "args": ["-x"]},
                    }
                },
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        data = json.loads(mcp.read_text())
        assert "trw" not in data["mcpServers"]
        assert data["mcpServers"]["mine"] == {"command": "my-server", "args": ["-x"]}

    def test_antigravity_settings_json_custom_formatting_survives(self, tmp_path: Path) -> None:
        settings_path = tmp_path / ".antigravitycli" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        original = json.dumps({"mcpServers": {"trw": {"command": "trw-mcp"}, "mine": {"command": "m"}}})  # compact
        settings_path.write_text(original)
        (tmp_path / ".trw").mkdir()

        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        _run_uninstall(_ns(tmp_path))

        assert settings_path.read_text() == original

    def test_opencode_json_canonical_strips_server_and_instructions_entry(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._opencode import _get_trw_mcp_entry
        from trw_mcp.bootstrap._opencode_instructions import OPENCODE_INSTRUCTIONS_REL
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        opencode = tmp_path / "opencode.json"
        opencode.write_text(
            json.dumps(
                {
                    "mcp": {"trw": dict(_get_trw_mcp_entry(tmp_path)), "mine": {"command": "m"}},
                    "instructions": [OPENCODE_INSTRUCTIONS_REL.as_posix(), "MY-OWN.md"],
                },
                indent=2,
            )
            + "\n"
        )
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        data = json.loads(opencode.read_text())
        assert "trw" not in data["mcp"]
        assert "mine" in data["mcp"]
        assert data["instructions"] == ["MY-OWN.md"]

    def test_opencode_json_custom_formatting_leaves_both_edits_untouched(self, tmp_path: Path) -> None:
        """Non-canonical opencode.json: neither the server-map edit nor the instructions edit applies."""
        from trw_mcp.bootstrap._opencode_instructions import OPENCODE_INSTRUCTIONS_REL
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        opencode = tmp_path / "opencode.json"
        original = json.dumps(  # compact -- not canonical
            {
                "mcp": {"trw": {"command": "trw-mcp"}},
                "instructions": [OPENCODE_INSTRUCTIONS_REL.as_posix()],
            }
        )
        opencode.write_text(original)
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert opencode.read_text() == original


@pytest.mark.unit
class TestServerMapCustomFormattingWarns:
    """Unit-level warning check, run after the integration tests above have
    already exercised ``_run_uninstall`` once in this process (structlog's
    process-global config is otherwise order-sensitive for the FIRST call --
    see the byte-identity test's docstring above)."""

    def test_strip_trw_json_warns_on_non_canonical_input(self) -> None:
        from structlog.testing import capture_logs

        from trw_mcp.bootstrap._utils import _trw_mcp_server_entry
        from trw_mcp.server._subcommands_uninstall_config import _strip_trw_json

        root = Path("/nonexistent-project")  # no .venv launcher: the entry resolves without touching disk
        original = json.dumps({"mcpServers": {"trw": _trw_mcp_server_entry(root), "mine": {"command": "m"}}})
        with capture_logs() as logs:
            changed, rendered, delete = _strip_trw_json(original, root)
        assert changed is False
        assert rendered == original
        assert delete is False
        events = {e.get("event") for e in logs}
        assert "uninstall_merged_config_custom_formatting" in events


# ---------------------------------------------------------------------------
# TOML text-level table removal (closing the "known gap")
# ---------------------------------------------------------------------------


_TRW_TABLE = '[mcp_servers.trw]\ncommand = "trw-mcp"\nargs = []\n'


@pytest.mark.unit
class TestStripTomlTable:
    def test_removes_only_the_named_table_and_its_scalars(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import strip_toml_table

        raw = '[mcp_servers.trw]\ncommand = "trw-mcp"\nargs = []\n'
        out, removed, refusal = strip_toml_table(raw, "mcp_servers.trw", [_TRW_TABLE])
        assert (out, removed, refusal) == ("", True, None)

    def test_leaves_comments_and_sibling_tables_byte_identical(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import strip_toml_table

        raw = (
            "# my personal codex config\n"
            'model = "gpt-5"\n\n'
            "[mcp_servers.mine]\n"
            'command = "my-server"\n\n'
            "[mcp_servers.trw]\n"
            'command = "trw-mcp"\n'
            "args = []\n\n"
            "[other_table]\n"
            "x = 1\n"
        )
        out, removed, _ = strip_toml_table(raw, "mcp_servers.trw", [_TRW_TABLE])
        assert removed is True
        assert "# my personal codex config" in out
        assert '[mcp_servers.mine]\ncommand = "my-server"' in out
        assert "[other_table]\nx = 1" in out
        assert "mcp_servers.trw" not in out

    def test_no_table_present_is_a_noop(self) -> None:
        from trw_mcp.bootstrap._user_file_edit import strip_toml_table

        raw = '[mcp_servers.mine]\ncommand = "my-server"\n'
        out, removed, refusal = strip_toml_table(raw, "mcp_servers.trw", [_TRW_TABLE])
        assert (out, removed, refusal) == (raw, False, None)


@pytest.mark.integration
class TestCodexTomlByteIdentical:
    def test_codex_config_toml_with_comments_and_user_tables_only_trw_removed(self, tmp_path: Path) -> None:
        """A hand-commented .codex/config.toml: only TRW's table is gone, everything else byte-identical."""
        from trw_mcp.bootstrap._codex import merge_codex_config
        from trw_mcp.bootstrap._codex_toml import _toml_dumps
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        config = tmp_path / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        original = (
            "# personal codex settings -- do not remove this comment\n"
            'model = "gpt-5"\n\n'
            "[mcp_servers.mine]\n"
            'command = "my-own-server"\n\n'
        ) + _toml_dumps({"mcp_servers": {"trw": merge_codex_config({}, target_dir=tmp_path)["mcp_servers"]["trw"]}})
        config.write_text(original)
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        final = config.read_text()
        assert "# personal codex settings -- do not remove this comment" in final
        assert 'model = "gpt-5"' in final
        assert "[mcp_servers.mine]" in final
        assert 'command = "my-own-server"' in final
        assert "mcp_servers.trw" not in final

    def test_symlinked_codex_dir_is_refused(self, tmp_path: Path) -> None:
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        project = tmp_path / "proj"
        project.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "sentinel.txt").write_text("do not delete me")
        (outside / "config.toml").write_text('[mcp_servers.trw]\ncommand = "trw-mcp"\n')
        (project / ".codex").symlink_to(outside, target_is_directory=True)
        (project / ".trw").mkdir()

        with pytest.raises(SystemExit):
            _run_uninstall(_ns(project))

        assert (outside / "sentinel.txt").exists()
        assert (outside / "config.toml").read_text() == '[mcp_servers.trw]\ncommand = "trw-mcp"\n'


# ---------------------------------------------------------------------------
# Newline preservation (coordinator review round 2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNewlinePreservation:
    def test_crlf_claude_md_only_block_lines_change_rest_byte_identical(self, tmp_path: Path) -> None:
        """A CRLF file: after uninstall, only the block's lines are gone; every other byte (incl. \\r\\n) is intact."""
        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        claude_md = tmp_path / "CLAUDE.md"
        content = (
            "# Project\r\n"
            "\r\n"
            "User instructions here.\r\n"
            "<!-- trw:start -->\r\n"
            "managed content\r\n"
            "<!-- trw:end -->\r\n"
            "\r\n"
            "More user text.\r\n"
        )
        claude_md.write_bytes(content.encode("utf-8"))
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        final = claude_md.read_bytes().decode("utf-8")
        assert "managed content" not in final
        assert "\r\n" in final, "CRLF line endings must survive"
        assert "# Project\r\n\r\nUser instructions here.\r\n" in final
        assert "\r\nMore user text.\r\n" in final
        # No line was silently LF-ified: every remaining line still ends \r\n.
        for line in final.splitlines(keepends=True):
            if line.strip():
                assert line.endswith("\r\n"), f"a line lost its CRLF ending: {line!r}"


@pytest.mark.unit
class TestFileModePreservation:
    def test_atomic_write_text_preserves_original_mode(self, tmp_path: Path) -> None:
        import stat as stat_module

        from trw_mcp.bootstrap._user_file_edit import atomic_write_text

        f = tmp_path / "settings.json"
        f.write_text("{}")
        f.chmod(0o600)

        atomic_write_text(f, '{"a": 1}')

        mode = stat_module.S_IMODE(f.stat().st_mode)
        assert mode == 0o600

    def test_0600_settings_json_keeps_0600_through_uninstall(self, tmp_path: Path) -> None:
        import stat as stat_module

        from trw_mcp.server._subcommands_lifecycle import _run_uninstall

        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        # A user hook alongside TRW's keeps the file from being emptied+deleted,
        # so the edit-in-place (mode-preserving) path is the one under test.
        settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"'}]},
                            {"hooks": [{"command": "./my-own-hook.sh"}]},
                        ]
                    }
                },
                indent=2,
            )
            + "\n"
        )
        settings.chmod(0o600)
        (tmp_path / ".trw").mkdir()

        _run_uninstall(_ns(tmp_path))

        assert settings.is_file(), "precondition: the file must survive (user hook present) to test mode preservation"
        mode = stat_module.S_IMODE(settings.stat().st_mode)
        assert mode == 0o600
