"""PRD-INFRA-192 FR10/FR12 — deletion tombstones.

A manifest-tracked path the user deletes stays deleted across ``init-project``/
``update-project``; only an explicit ``--reprovision`` request clears it.
Ordinary init/update runs and unrelated config changes must not resurrect it
(the HOOK-RESURRECT defect this PRD exists to close).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

pytestmark = pytest.mark.integration

_HOOK = ".claude/hooks/post-compact.sh"
_SKILL_MD = ".claude/skills/trw-audit/SKILL.md"
_SKILL_KEY = "trw-audit/SKILL.md"
_HOOK_KEY = "post-compact.sh"

# ``session-start.sh`` (unlike ``post-compact.sh``) is registered in every
# registration surface — .claude/settings.json, .codex/hooks.json, and the
# Copilot hooks file — so it doubles as the shared fixture for the FR10 hook
# deregistration tests below.
_SESSION_START_HOOK = ".claude/hooks/session-start.sh"
_SESSION_START_KEY = "session-start.sh"


def _init(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    result = init_project(repo, ide="claude-code")
    assert not result["errors"], result["errors"]
    return repo


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _tombstones(repo: Path) -> list[str]:
    manifest = _read_manifest(repo)
    assert manifest is not None
    value = manifest.get("tombstones", [])
    assert isinstance(value, list)
    return [str(v) for v in value]


class TestDeletedArtifactStaysDeleted:
    def test_deleted_hook_stays_deleted_across_two_updates(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        hook = repo / _HOOK
        assert hook.is_file(), "non-vacuity: the hook must exist before deletion"
        hook.unlink()

        r1 = update_project(repo)
        assert not r1["errors"], r1["errors"]
        assert not hook.exists(), "update-project resurrected a hook the user deleted"
        assert _HOOK_KEY in _tombstones(repo)
        hashes = _manifest_content_hashes(_read_manifest(repo)) or {}
        assert _HOOK_KEY not in hashes

        r2 = update_project(repo)
        assert not r2["errors"], r2["errors"]
        assert not hook.exists(), "a SECOND update-project resurrected the deleted hook"
        assert _HOOK_KEY in _tombstones(repo)

    def test_deleted_skill_dir_stays_deleted(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        skill_dir = repo / ".claude" / "skills" / "trw-audit"
        assert skill_dir.is_dir(), "non-vacuity: the skill dir must exist before deletion"
        shutil.rmtree(skill_dir)

        r1 = update_project(repo)
        assert not r1["errors"], r1["errors"]
        assert not skill_dir.exists(), "update-project resurrected a skill dir the user deleted"
        assert _SKILL_KEY in _tombstones(repo)

        r2 = update_project(repo)
        assert not r2["errors"], r2["errors"]
        assert not skill_dir.exists(), "a SECOND update-project resurrected the deleted skill"

    def test_deleted_skill_md_with_a_surviving_sibling_stays_deleted_and_sibling_is_untouched(
        self, tmp_path: Path
    ) -> None:
        """Deleting only ``SKILL.md`` (keeping a user file) must tombstone SKILL.md, not the directory."""
        repo = _init(tmp_path)
        skill_dir = repo / ".claude" / "skills" / "trw-audit"
        skill_md = skill_dir / "SKILL.md"
        sibling = skill_dir / "my-notes.md"
        assert skill_md.is_file(), "non-vacuity: SKILL.md must exist before deletion"
        sibling.write_text("the user's own notes\n", encoding="utf-8")
        sibling_bytes = sibling.read_bytes()
        skill_md.unlink()

        r1 = update_project(repo)
        assert not r1["errors"], r1["errors"]
        assert not skill_md.exists(), "update-project resurrected a SKILL.md the user deleted"
        assert sibling.is_file(), "a sibling file the user kept must survive enforcement"
        assert sibling.read_bytes() == sibling_bytes, "the surviving sibling must be byte-identical"
        assert _SKILL_KEY in _tombstones(repo)

        r2 = update_project(repo)
        assert not r2["errors"], r2["errors"]
        assert not skill_md.exists(), "a SECOND update-project resurrected the deleted SKILL.md"
        assert sibling.is_file()
        assert sibling.read_bytes() == sibling_bytes
        assert _SKILL_KEY in _tombstones(repo)

    def test_reprovision_restores_skill_md_without_disturbing_the_surviving_sibling(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        skill_dir = repo / ".claude" / "skills" / "trw-audit"
        skill_md = skill_dir / "SKILL.md"
        sibling = skill_dir / "my-notes.md"
        sibling.write_text("the user's own notes\n", encoding="utf-8")
        sibling_bytes = sibling.read_bytes()
        skill_md.unlink()
        assert not update_project(repo)["errors"]
        assert _SKILL_KEY in _tombstones(repo)

        result = update_project(repo, reprovision=[_SKILL_KEY])

        assert not result["errors"], result["errors"]
        assert skill_md.is_file(), "--reprovision must restore SKILL.md"
        assert sibling.is_file(), "the surviving sibling must not be disturbed by reprovision"
        assert sibling.read_bytes() == sibling_bytes
        assert _SKILL_KEY not in _tombstones(repo)


class TestNeverProvisionedIsNotTombstoned:
    def test_removing_the_manifest_record_and_the_file_makes_it_reinstallable(self, tmp_path: Path) -> None:
        """A key absent from ``content_hashes`` (never recorded as ours) is written normally."""
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        repo = _init(tmp_path)
        hook = repo / _HOOK
        manifest_path = repo / ".trw" / "managed-artifacts.yaml"
        data = FileStateReader().read_yaml(manifest_path)
        del data["content_hashes"][_HOOK_KEY]
        FileStateWriter().write_yaml(manifest_path, data)
        hook.unlink()

        result = update_project(repo)

        assert not result["errors"], result["errors"]
        assert hook.is_file(), "a key never recorded as TRW's own must be written normally, not tombstoned"
        assert _HOOK_KEY not in _tombstones(repo)


class TestReprovision:
    def test_reprovision_named_path_clears_only_that_tombstone(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        hook = repo / _HOOK
        skill_dir = repo / ".claude" / "skills" / "trw-audit"
        hook.unlink()
        shutil.rmtree(skill_dir)
        assert not update_project(repo)["errors"]
        assert {_HOOK_KEY, _SKILL_KEY} <= set(_tombstones(repo))

        result = update_project(repo, reprovision=[_HOOK_KEY])

        assert not result["errors"], result["errors"]
        assert hook.is_file(), "--reprovision must restore the named path"
        assert not skill_dir.exists(), "an untouched tombstone must survive --reprovision of a different path"
        remaining = _tombstones(repo)
        assert _HOOK_KEY not in remaining
        assert _SKILL_KEY in remaining

    def test_reprovision_all_clears_every_tombstone(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        hook = repo / _HOOK
        skill_dir = repo / ".claude" / "skills" / "trw-audit"
        hook.unlink()
        shutil.rmtree(skill_dir)
        assert not update_project(repo)["errors"]
        assert {_HOOK_KEY, _SKILL_KEY} <= set(_tombstones(repo))

        result = update_project(repo, reprovision=["all"])

        assert not result["errors"], result["errors"]
        assert hook.is_file()
        assert skill_dir.is_dir()
        assert _tombstones(repo) == []

    def test_unknown_reprovision_path_errors_with_no_writes(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        before = _snapshot_tree(repo)

        result = update_project(repo, reprovision=["does-not-exist.sh"])

        assert len(result["errors"]) == 1, result["errors"]
        assert "does not match a tombstoned path" in result["errors"][0]
        assert _snapshot_tree(repo) == before, "an unknown --reprovision target must write nothing"

    def test_cli_reprovision_flag_reaches_update_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ``--reprovision`` argparse entry point threads through to ``update_project``."""
        import argparse

        from trw_mcp.server._subcommands import _run_update_project

        captured: dict[str, object] = {}

        def _fake_update_project(*_args: object, **kwargs: object) -> dict[str, list[str]]:
            captured.update(kwargs)
            return {"updated": [], "created": [], "preserved": [], "errors": [], "warnings": [], "cleaned": []}

        monkeypatch.setattr("trw_mcp.bootstrap.update_project", _fake_update_project)
        args = argparse.Namespace(
            target_dir=".",
            pip_install=False,
            dry_run=False,
            ide=None,
            reprovision=["a.sh", "all"],
            log_json=False,
            debug=False,
            verbose=0,
            quiet=True,
        )

        with pytest.raises(SystemExit):
            _run_update_project(args)

        assert captured.get("reprovision") == ["a.sh", "all"]


class TestUserRestoredFileClearsTombstone:
    def test_user_restoring_the_file_themselves_clears_the_tombstone(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        hook = repo / _HOOK
        hook.unlink()
        assert not update_project(repo)["errors"]
        assert _HOOK_KEY in _tombstones(repo)

        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho the user's own replacement\n", encoding="utf-8")

        result = update_project(repo)

        assert not result["errors"], result["errors"]
        assert _HOOK_KEY not in _tombstones(repo), "an existing path must be handed back to the normal guard"
        assert hook.is_file()


class TestInitProjectRespectsTombstones:
    def test_init_over_existing_project_neither_recreates_nor_clears(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        hook = repo / _HOOK
        hook.unlink()
        assert not update_project(repo)["errors"]
        assert _HOOK_KEY in _tombstones(repo)

        result = init_project(repo, ide="claude-code")

        assert not result["errors"], result["errors"]
        assert not hook.exists(), "init-project resurrected a tombstoned hook (HOOK-RESURRECT)"
        assert _HOOK_KEY in _tombstones(repo), "a bare re-init must not clear the tombstone"


class TestFailedUpdateLeavesManifestByteIdentical:
    def test_failed_update_leaves_manifest_including_tombstones_untouched(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _init(tmp_path)
        hook = repo / _HOOK
        hook.unlink()
        assert not update_project(repo)["errors"]
        assert _HOOK_KEY in _tombstones(repo)
        manifest_path = repo / ".trw" / "managed-artifacts.yaml"
        before = manifest_path.read_bytes()

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("simulated post-update failure")

        monkeypatch.setattr("trw_mcp.bootstrap._update_project._verify_installation", _boom)

        result = update_project(repo)

        assert result["errors"], "the simulated failure must surface as an error"
        assert manifest_path.read_bytes() == before, (
            "a failed update must roll back the manifest byte-for-byte, tombstones included"
        )
        assert not hook.exists(), "the rollback must not resurrect the tombstoned hook either"


class TestMalformedTombstonesFieldRefuses:
    def test_wrong_typed_tombstones_field_refuses_update(self, tmp_path: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        repo = _init(tmp_path)
        manifest_path = repo / ".trw" / "managed-artifacts.yaml"
        data = FileStateReader().read_yaml(manifest_path)
        data["tombstones"] = {"a.md": "oops"}
        FileStateWriter().write_yaml(manifest_path, data)

        result = update_project(repo)

        assert len(result["errors"]) == 1, result["errors"]
        assert "tombstones" in result["errors"][0]
        assert "refusing to update" in result["errors"][0]


def _hook_commands(hooks_by_event: dict[str, object]) -> list[str]:
    """Flatten every hook ``command`` string out of a hooks.json-shaped mapping."""
    commands: list[str] = []
    for entries in hooks_by_event.values():
        assert isinstance(entries, list)
        for entry in entries:
            for hook in entry.get("hooks", []):
                commands.append(str(hook.get("command", "")))
    return commands


def _entry_count(hooks_by_event: dict[str, object]) -> int:
    return sum(len(v) for v in hooks_by_event.values() if isinstance(v, list))


class TestHookDeregistration:
    """PRD-INFRA-192 FR10: a hook ships with its registration, or not at all.

    A tombstoned hook script must have every dangling registration removed
    from every registration surface TRW knows about, not just the script
    file itself.
    """

    def test_deleted_hook_registration_removed_from_claude_settings(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        settings_path = repo / ".claude" / "settings.json"
        before = json.loads(settings_path.read_text(encoding="utf-8"))
        before_count = _entry_count(before["hooks"])
        hook = repo / _HOOK
        assert hook.is_file()

        hook.unlink()
        result = update_project(repo)

        assert not result["errors"], result["errors"]
        assert not hook.exists()
        after = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = _hook_commands(after["hooks"])
        assert not any("post-compact.sh" in c for c in commands), commands
        assert "PostCompact" not in after["hooks"], "an emptied event key must be dropped, not left as []"
        # Exactly the one dangling entry is gone — every other registration untouched.
        assert _entry_count(after["hooks"]) == before_count - 1

        # Running a second time is idempotent: nothing left to remove, no error.
        result2 = update_project(repo)
        assert not result2["errors"], result2["errors"]
        after2 = json.loads(settings_path.read_text(encoding="utf-8"))
        assert after2 == after

    def test_user_added_entry_for_a_different_script_survives(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        settings_path = repo / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        data["hooks"].setdefault("Notification", []).append(
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/my-own-hook.sh"'}],
            }
        )
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        hook = repo / _HOOK
        hook.unlink()

        result = update_project(repo)

        assert not result["errors"], result["errors"]
        after = json.loads(settings_path.read_text(encoding="utf-8"))
        assert any("my-own-hook.sh" in c for c in _hook_commands(after["hooks"])), (
            "a user's unrelated hook entry must survive deregistration of a different script"
        )

    def test_users_own_entry_for_the_deleted_script_is_left_alone_with_a_warning(self, tmp_path: Path) -> None:
        """TRW never removes a byte it can't prove it wrote: a hand-added entry survives, warned about."""
        repo = _init(tmp_path)
        settings_path = repo / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        # A DIFFERENT command than TRW's own canonical invocation (extra flag) —
        # so it cannot be mistaken for the entry TRW itself would have written.
        data["hooks"].setdefault("Notification", []).append(
            {
                "matcher": "",
                "hooks": [{"type": "command", "command": 'sh -x "$CLAUDE_PROJECT_DIR/.claude/hooks/post-compact.sh"'}],
            }
        )
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        hook = repo / _HOOK
        hook.unlink()

        result = update_project(repo)

        assert not result["errors"], result["errors"]
        after = json.loads(settings_path.read_text(encoding="utf-8"))
        assert any("post-compact.sh" in c for c in _hook_commands(after["hooks"])), (
            "a user-authored registration referencing the deleted script must survive"
        )
        assert any("post-compact.sh" in w and "deleted" in w for w in result.get("warnings", [])), result.get(
            "warnings", []
        )

    def test_deleted_hook_registration_removed_from_codex_hooks_json(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import generate_codex_hooks

        repo = _init(tmp_path)
        gen_result = generate_codex_hooks(repo)
        assert not gen_result["errors"], gen_result["errors"]
        codex_path = repo / ".codex" / "hooks.json"
        before_commands = _hook_commands(json.loads(codex_path.read_text(encoding="utf-8"))["hooks"])
        hook = repo / _SESSION_START_HOOK
        assert hook.is_file()

        hook.unlink()
        result = update_project(repo, ide="codex")

        assert not result["errors"], result["errors"]
        assert not hook.exists()
        after = json.loads(codex_path.read_text(encoding="utf-8"))
        after_commands = _hook_commands(after["hooks"])
        assert not any("session-start.sh" in c for c in after_commands), after_commands
        assert "SessionStart" not in after["hooks"]
        # Every other originally-registered TRW command survives untouched. (A
        # separate, unrelated writer — e.g. distill-channel telemetry — may add
        # its OWN new entries on this same update; that is not this change's
        # concern, so the assertion is "nothing that was there is gone", not a
        # total-count comparison.)
        for command in before_commands:
            if "session-start.sh" not in command:
                assert command in after_commands

    def test_deleted_hook_registration_removed_from_copilot_hooks_json(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import generate_copilot_hooks

        repo = _init(tmp_path)
        gen_result = generate_copilot_hooks(repo)
        assert not gen_result["errors"], gen_result["errors"]
        copilot_path = repo / ".github" / "hooks" / "hooks.json"
        before = json.loads(copilot_path.read_text(encoding="utf-8"))
        before_count = _entry_count(before["hooks"])
        hook = repo / _SESSION_START_HOOK
        assert hook.is_file()

        hook.unlink()
        result = update_project(repo)

        assert not result["errors"], result["errors"]
        assert not hook.exists()
        after = json.loads(copilot_path.read_text(encoding="utf-8"))
        commands = _hook_commands(after["hooks"])
        assert not any("session-start.sh" in c for c in commands), commands
        assert "sessionStart" not in after["hooks"]
        assert _entry_count(after["hooks"]) == before_count - 1

    def test_custom_formatted_settings_json_is_left_byte_identical_with_a_warning(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR10 P1-b (i): a hand-reformatted settings.json is never rewritten.

        4-space indent is not the ``json.dumps(..., indent=2)`` convention TRW's
        own writers use, so TRW cannot prove the file's other bytes are safe to
        touch — even though it can identify and would otherwise remove its own
        dangling entry.
        """
        from trw_mcp.bootstrap._hook_deregistration import deregister_hook_script

        target_dir = tmp_path / "proj"
        settings_path = target_dir / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        data = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup|resume",
                        "hooks": [
                            {"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"'}
                        ],
                    }
                ]
            }
        }
        before = json.dumps(data, indent=4) + "\n"  # 4-space, not TRW's 2-space convention
        settings_path.write_text(before, encoding="utf-8")
        result: dict[str, list[str]] = {"warnings": []}

        deregister_hook_script(target_dir, ".claude/hooks/session-start.sh", result)

        assert settings_path.read_text(encoding="utf-8") == before, "custom-formatted file must be left untouched"
        assert any("session-start.sh" in w and "custom formatting" in w for w in result["warnings"]), result["warnings"]

    def test_canonical_settings_json_preserves_unrelated_empty_event_list(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR10 P1-b (ii): an already-empty event array must survive deregistration.

        Only an event list that became empty BECAUSE OF this run's removal may
        be dropped; one that was already empty (a hand-added ``"Notification":
        []``, say) is untouched, and the rest of the canonical document is
        byte-identical to its own re-serialization.
        """
        from trw_mcp.bootstrap._hook_deregistration import deregister_hook_script

        target_dir = tmp_path / "proj"
        settings_path = target_dir / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        data = {
            "hooks": {
                "Notification": [],
                "SessionStart": [
                    {
                        "matcher": "startup|resume",
                        "hooks": [
                            {"type": "command", "command": 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh"'}
                        ],
                    }
                ],
            }
        }
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        result: dict[str, list[str]] = {"warnings": [], "info": []}

        deregister_hook_script(target_dir, ".claude/hooks/session-start.sh", result)

        after = json.loads(settings_path.read_text(encoding="utf-8"))
        assert "Notification" in after["hooks"], "an event list that was already empty must survive"
        assert after["hooks"]["Notification"] == []
        assert "SessionStart" not in after["hooks"], "the emptied-by-removal event key must still be dropped"
        expected = {"hooks": {"Notification": []}}
        assert json.dumps(after, indent=2) + "\n" == json.dumps(expected, indent=2) + "\n"

    def test_codex_user_appended_hook_in_a_trw_group_survives(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR10 P1-a: a user hook appended into a TRW-managed Codex group survives.

        Removing the dangling TRW hook must only drop that ONE command from
        the group's ``hooks`` list, never the whole group — a whole-group drop
        would silently delete the user's own co-located hook too.
        """
        from trw_mcp.bootstrap import generate_codex_hooks
        from trw_mcp.bootstrap._hook_deregistration import deregister_hook_script

        target_dir = tmp_path / "proj"
        (target_dir / ".claude" / "hooks").mkdir(parents=True)
        gen_result = generate_codex_hooks(target_dir)
        assert not gen_result["errors"], gen_result["errors"]
        codex_path = target_dir / ".codex" / "hooks.json"
        data = json.loads(codex_path.read_text(encoding="utf-8"))
        # Append the user's own hook into the SAME TRW-managed SessionStart group.
        session_start_group = data["hooks"]["SessionStart"][0]
        session_start_group["hooks"].append({"type": "command", "command": '/bin/sh "my-own-hook.sh"'})
        codex_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result: dict[str, list[str]] = {"warnings": [], "info": []}

        deregister_hook_script(target_dir, ".claude/hooks/session-start.sh", result)

        after = json.loads(codex_path.read_text(encoding="utf-8"))
        commands = _hook_commands(after["hooks"])
        assert not any("session-start.sh" in c for c in commands), commands
        assert any("my-own-hook.sh" in c for c in commands), (
            "the user's own hook co-located in the TRW group must survive the group's partial removal"
        )
        assert "SessionStart" in after["hooks"], "the group survives because it still holds the user's hook"

    def test_reprovision_restores_both_the_script_and_its_registration(self, tmp_path: Path) -> None:
        repo = _init(tmp_path)
        settings_path = repo / ".claude" / "settings.json"
        hook = repo / _HOOK
        hook.unlink()
        assert not update_project(repo)["errors"]
        after_delete = json.loads(settings_path.read_text(encoding="utf-8"))
        assert not any("post-compact.sh" in c for c in _hook_commands(after_delete["hooks"]))

        result = update_project(repo, reprovision=[_HOOK_KEY])

        assert not result["errors"], result["errors"]
        assert hook.is_file(), "--reprovision must restore the script itself"
        restored = json.loads(settings_path.read_text(encoding="utf-8"))
        assert any("post-compact.sh" in c for c in _hook_commands(restored["hooks"])), (
            "--reprovision must let the normal writer re-register the restored hook"
        )

    def test_unparseable_settings_json_yields_a_warning_and_is_left_unchanged(self, tmp_path: Path) -> None:
        """Exercised directly against the deregistration seam.

        (Historical note, superseded by PRD-INFRA-192 FR10 P1-c: a full
        ``update_project()`` run used to be unable to reach this path for
        ``.claude/settings.json`` because its smart-merge writer recovered a
        corrupt existing file by replacing it with the bundled template
        *before* enforcement ran. That silent-clobber fallback was removed —
        see ``test_malformed_settings_json_survives_update_project_untouched``
        below — so a corrupt ``settings.json`` now reaches enforcement too.
        This test still verifies the warn-and-leave-untouched behavior
        directly against :func:`deregister_hook_script`.
        """
        from trw_mcp.bootstrap._hook_deregistration import deregister_hook_script

        target_dir = tmp_path / "proj"
        settings_path = target_dir / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text("{ not json", encoding="utf-8")
        before = settings_path.read_bytes()
        result: dict[str, list[str]] = {"warnings": []}

        deregister_hook_script(target_dir, ".claude/hooks/post-compact.sh", result)

        assert any("settings.json" in w for w in result["warnings"]), result["warnings"]
        assert settings_path.read_bytes() == before, "an unparseable registration file must be left untouched"

    def test_malformed_settings_json_survives_update_project_untouched(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR10 (P1-c): update-project must never replace a
        malformed ``.claude/settings.json`` with the bundled template — that
        silently destroys whatever the user had there. It must leave the file
        byte-identical and report the problem via ``result["errors"]``
        (which rolls the whole update-project transaction back)."""
        repo = _init(tmp_path)
        settings_path = repo / ".claude" / "settings.json"
        before = "{ this is not valid json at all"
        settings_path.write_text(before, encoding="utf-8")

        result = update_project(repo)

        assert result["errors"], "a malformed settings.json must block the transaction, not be silently replaced"
        assert any("settings.json" in e for e in result["errors"]), result["errors"]
        assert settings_path.read_text(encoding="utf-8") == before, (
            "TRW must never replace a settings.json it cannot parse with the bundled template"
        )
