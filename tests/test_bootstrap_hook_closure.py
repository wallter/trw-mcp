"""PRD-INFRA-192 FR10 closure — deploy only registered hooks + sourced helpers.

Install/update must ship exactly: the hooks some client actually registers,
plus the transitive closure of helper scripts (``lib-*.sh``) those hooks
``source``. A helper never gets a fake registration of its own just to
justify shipping it, and no second hand-kept list decides the deployed set —
it is derived from the real registration payloads (``.claude/settings.json``
for claude-code, ``_codex_hooks_payload`` for codex, ``_COPILOT_HOOK_MAP``
for copilot).

``.claude/hooks/`` is shared: codex and copilot run their own hook events off
scripts living in that same directory, so a codex-only or copilot-only
project used to receive the full claude-code hook set (14 hooks + 2 helper
libs) though it registers only 5 of them. That unconditional unwired copy is
what this file pins down as fixed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._hook_closure import (
    deployable_hook_files,
    registered_hook_scripts_for_clients,
)
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.bootstrap._version_manifest import _read_manifest

pytestmark = pytest.mark.integration

_HOOKS_SOURCE = _DATA_DIR / "hooks"

# The 5 scripts codex and copilot each register off .claude/hooks/.
_SHARED_MINIMAL_HOOKS = frozenset(
    {
        "session-start.sh",
        "user-prompt-submit.sh",
        "pre-tool-deliver-gate.sh",
        "post-tool-event.sh",
        "stop-ceremony.sh",
    }
)

# claude-code-only hooks: registered solely by the .claude/settings.json
# template, never by codex or copilot.
_CLAUDE_CODE_ONLY_HOOKS = frozenset(
    {
        "pre-tool-intent-guard.sh",
        "post-tool-intent-check.sh",
        "instructions-loaded.sh",
        "pre-compact.sh",
        "post-compact.sh",
        "subagent-start.sh",
        "subagent-stop.sh",
        "post-tool-degenerate-result.sh",
        "lib-intent-guard.sh",
    }
)


def _init(tmp_path: Path, ide: str) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    result = init_project(repo, ide=ide)
    assert not result["errors"], result["errors"]
    return repo


def _hook_names(repo: Path) -> set[str]:
    return {p.name for p in (repo / ".claude" / "hooks").glob("*.sh")}


class TestFreshInitDeploysExactlyTheClosure:
    def test_codex_only_ships_only_its_closure(self, tmp_path: Path) -> None:
        repo = _init(tmp_path, "codex")
        names = _hook_names(repo)
        assert names == _SHARED_MINIMAL_HOOKS | {"lib-trw.sh"}
        assert not names & _CLAUDE_CODE_ONLY_HOOKS, "codex-only install shipped a claude-code-only hook"

    def test_copilot_only_ships_only_its_closure(self, tmp_path: Path) -> None:
        repo = _init(tmp_path, "copilot")
        names = _hook_names(repo)
        assert names == _SHARED_MINIMAL_HOOKS | {"lib-trw.sh"}
        assert not names & _CLAUDE_CODE_ONLY_HOOKS, "copilot-only install shipped a claude-code-only hook"

    def test_claude_code_default_ships_the_full_registered_closure(self, tmp_path: Path) -> None:
        """The common case (bare init, no --ide) must not narrow at all."""
        repo = tmp_path / "proj"
        repo.mkdir()
        (repo / ".git").mkdir()
        result = init_project(repo)
        assert not result["errors"], result["errors"]

        bundled = {p.name for p in _HOOKS_SOURCE.glob("*.sh")}
        assert _hook_names(repo) == bundled, "a bare (claude-code) install must still ship every bundled hook"


class TestHelperHasNoFakeRegistration:
    def test_lib_trw_is_never_itself_a_registered_script(self) -> None:
        """lib-trw.sh earns its place by being SOURCED, not by any client registering it directly."""
        for clients in (["claude-code"], ["codex"], ["copilot"]):
            assert "lib-trw.sh" not in registered_hook_scripts_for_clients(clients)
            assert "lib-intent-guard.sh" not in registered_hook_scripts_for_clients(clients)

    def test_lib_trw_is_present_only_via_the_sourcing_closure(self) -> None:
        deployable = deployable_hook_files(["codex"], _HOOKS_SOURCE)
        assert "lib-trw.sh" in deployable, "codex's registered hooks source lib-trw.sh; it must still deploy"
        assert "lib-intent-guard.sh" not in deployable, "nothing codex registers sources lib-intent-guard.sh"


class TestRepeatUpdateIsStable:
    def test_second_update_on_codex_only_project_makes_no_changes(self, tmp_path: Path) -> None:
        repo = _init(tmp_path, "codex")
        before = _hook_names(repo)

        r1 = update_project(repo)
        assert not r1["errors"], r1["errors"]
        assert _hook_names(repo) == before

        r2 = update_project(repo)
        assert not r2["errors"], r2["errors"]
        assert _hook_names(repo) == before
        assert not r2.get("removed"), "a stable repeat update must not remove anything"


class TestUpdateNarrowsClientSetSweepsUnwiredCopies:
    def _claude_code_project_with_narrowed_record(self, tmp_path: Path) -> Path:
        """A project installed as claude-code (full closure), then re-recorded codex-only."""
        repo = _init(tmp_path, "claude-code")
        assert _hook_names(repo) == {p.name for p in _HOOKS_SOURCE.glob("*.sh")}

        config_path = repo / ".trw" / "config.yaml"
        original = config_path.read_text(encoding="utf-8")
        narrowed = original.replace('"claude-code"', '"codex"').replace("- claude-code", "- codex")
        assert narrowed != original, "fixture did not actually narrow target_platforms"
        config_path.write_text(narrowed, encoding="utf-8")
        return repo

    def test_unedited_unwired_copy_is_removed_on_narrowing_update(self, tmp_path: Path) -> None:
        repo = self._claude_code_project_with_narrowed_record(tmp_path)
        target = repo / ".claude" / "hooks" / "pre-tool-intent-guard.sh"
        assert target.is_file()

        result = update_project(repo)

        assert not result["errors"], result["errors"]
        assert not target.exists(), "an unedited claude-code-only hook must be swept once codex-only is recorded"
        assert (repo / ".claude" / "hooks" / "session-start.sh").is_file(), "the shared hook must survive"

    def test_edited_unwired_copy_is_kept_with_a_warning(self, tmp_path: Path) -> None:
        repo = self._claude_code_project_with_narrowed_record(tmp_path)
        target = repo / ".claude" / "hooks" / "lib-intent-guard.sh"
        assert target.is_file()
        edited = target.read_text(encoding="utf-8") + "\n# user edit — keep me\n"
        target.write_text(edited, encoding="utf-8")

        result = update_project(repo)

        assert not result["errors"], result["errors"]
        assert target.is_file(), "an edited copy must never be deleted"
        assert target.read_text(encoding="utf-8") == edited
        assert any("lib-intent-guard.sh" in w for w in result.get("warnings", [])), result.get("warnings")


class TestTombstoneAndReprovisionInteraction:
    def test_deleted_registered_hook_stays_deleted_on_a_narrowed_client_set(self, tmp_path: Path) -> None:
        repo = _init(tmp_path, "codex")
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        hook.unlink()

        r1 = update_project(repo)
        assert not r1["errors"], r1["errors"]
        assert not hook.exists()

        manifest = _read_manifest(repo)
        assert manifest is not None
        assert "session-start.sh" in manifest.get("tombstones", [])

        r2 = update_project(repo)
        assert not r2["errors"], r2["errors"]
        assert not hook.exists(), "a second update resurrected a deleted registered hook"

    def test_reprovision_restores_the_hook_and_its_sourced_helper(self, tmp_path: Path) -> None:
        repo = _init(tmp_path, "codex")
        hook = repo / ".claude" / "hooks" / "session-start.sh"
        lib = repo / ".claude" / "hooks" / "lib-trw.sh"
        hook.unlink()
        assert not update_project(repo)["errors"]
        assert not hook.exists()
        assert lib.is_file(), "the shared helper is still sourced by other registered hooks — must survive"

        result = update_project(repo, reprovision=["session-start.sh"])

        assert not result["errors"], result["errors"]
        assert hook.is_file(), "--reprovision must restore the registered hook"
        assert lib.is_file(), "the helper it sources must still be present after reprovision"
        assert (
            hashlib.sha256(hook.read_bytes()).hexdigest()
            == hashlib.sha256((_HOOKS_SOURCE / "session-start.sh").read_bytes()).hexdigest()
        )
