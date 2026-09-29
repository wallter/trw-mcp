"""Uninstall must remove the CC-03 hook scripts TRW wrote to ``.claude/hooks``.

Canary finding (dev34): ``lib-distill-hint.sh`` and ``pre-tool-distill-hint.sh``
were left behind because ``claude-code`` had no ``.claude/hooks`` managed
source, so uninstall had no ownership proof and preserved them.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._claude_code_distill_channels import _CC03_HOOKS, _get_hook_content
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_KEYS = tuple(f".claude/hooks/{n}" for n in _CC03_HOOKS)


def _ns(project: Path) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide="claude-code"
    )


def _project(tmp_path: Path, *, enabled: bool = True) -> Path:
    (tmp_path / ".git").mkdir()
    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / "config.yaml").write_text(f"cc03_hook_enabled: {'true' if enabled else 'false'}\n", encoding="utf-8")
    result = init_project(tmp_path, ide="claude-code")
    assert not result["errors"], result["errors"]
    return tmp_path


def _hashes(project: Path) -> dict[str, str]:
    data = yaml.safe_load((project / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))
    return dict(data.get("content_hashes") or {})


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


@pytest.mark.integration
class TestCc03HooksUninstall:
    def test_canary_shape_removes_both_hooks_keeps_user_file(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        assert not update_project(project, ide="claude-code")["errors"]
        hooks = project / ".claude" / "hooks"
        assert all((project / k).is_file() for k in _KEYS), "precondition: CC-03 hooks installed"
        assert all(k in _hashes(project) for k in _KEYS)
        mine = hooks / "my-hook.sh"
        mine.write_bytes(b"#!/bin/sh\necho mine\n")
        before = _tree(project)
        recorded = _hashes(project)

        _run_uninstall(_ns(project))

        assert not any((project / k).exists() for k in _KEYS)
        assert mine.read_bytes() == b"#!/bin/sh\necho mine\n"
        after = _tree(project)
        # Whole tree: nothing but the CC-03 keys vanished from .claude/hooks; every
        # surviving pre-existing file is byte-identical to before.
        removed = set(before) - set(after)
        assert set(_KEYS) <= removed
        assert not any(r.startswith(".claude/hooks/my-hook") for r in removed)
        # Everything uninstall touched is either a TRW-recorded key or TRW-owned
        # config it rewrites/empties; anything else is byte-identical.
        touched = removed | {r for r in after if r in before and after[r] != before[r]}
        from trw_mcp.bootstrap._version_manifest import _manifest_key_path

        allowed = {_manifest_key_path(k) for k in recorded} | {
            ".mcp.json",
            ".claude/settings.json",
            ".trw/managed-artifacts.yaml",
        }
        # Skill directories: only SKILL.md is keyed, the rest of a bundled skill goes with it.
        stray = {
            r
            for r in touched
            if r not in allowed and not r.startswith((".trw/", ".claude/skills/trw-")) and r != "AGENTS.md"
        }
        assert not stray, f"uninstall touched non-TRW files: {sorted(stray)}"

    def test_edited_hook_is_kept_and_reported(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        project = _project(tmp_path)
        assert not update_project(project, ide="claude-code")["errors"]
        edited = project / _KEYS[0]
        edited.write_text(edited.read_text(encoding="utf-8") + "\n# my edit\n", encoding="utf-8")
        edited_bytes = edited.read_bytes()

        _run_uninstall(_ns(project))

        assert edited.read_bytes() == edited_bytes
        assert "Preserved" in capsys.readouterr().out
        assert not (project / _KEYS[1]).exists(), "the unedited sibling is still removed"

    def test_gate_off_records_nothing_and_touches_nothing(self, tmp_path: Path) -> None:
        project = _project(tmp_path, enabled=False)
        assert not update_project(project, ide="claude-code")["errors"]
        assert not any((project / k).exists() for k in _KEYS)
        assert not any(k in _hashes(project) for k in _KEYS)
        mine = project / ".claude" / "hooks" / "my-hook.sh"
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_bytes(b"mine\n")

        _run_uninstall(_ns(project))

        assert mine.read_bytes() == b"mine\n"

    def test_upgrade_from_unrecorded_install_is_rerecorded_then_removed(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        assert not update_project(project, ide="claude-code")["errors"]
        # Simulate the pre-fix manifest: hooks on disk, no hash record for them.
        manifest = project / ".trw" / "managed-artifacts.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        for k in _KEYS:
            data["content_hashes"].pop(k, None)
        manifest.write_text(yaml.safe_dump(data), encoding="utf-8")
        assert not any(k in _hashes(project) for k in _KEYS)
        for k in _KEYS:
            name = Path(k).name
            content = _get_hook_content(name)
            assert content is not None
            assert (
                hashlib.sha256((project / k).read_bytes()).hexdigest() == hashlib.sha256(content.encode()).hexdigest()
            )

        assert not update_project(project, ide="claude-code")["errors"]
        assert all(k in _hashes(project) for k in _KEYS), "update-project re-records byte-matching hooks"

        _run_uninstall(_ns(project))

        assert not any((project / k).exists() for k in _KEYS)

    def test_unrecorded_hook_with_differing_bytes_is_not_recorded(self, tmp_path: Path) -> None:
        # Gate off so the installer neither rewrites nor withdraws the differing file.
        project = _project(tmp_path, enabled=False)
        target = project / _KEYS[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"#!/bin/sh\n# user's own script\n")

        assert not update_project(project, ide="claude-code")["errors"]
        assert target.read_bytes() == b"#!/bin/sh\n# user's own script\n"
        assert _KEYS[0] not in _hashes(project)
        from trw_mcp.bootstrap._managed_client_artifacts import managed_client_manifest_hashes

        assert _KEYS[0] not in managed_client_manifest_hashes(project, {})

        _run_uninstall(_ns(project))

        assert target.read_bytes() == b"#!/bin/sh\n# user's own script\n"

    def test_old_bundle_hook_is_refreshed_recorded_and_removed(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        assert not update_project(project, ide="claude-code")["errors"]
        old = b"#!/bin/sh\n# old bundle\n"
        manifest = project / ".trw" / "managed-artifacts.yaml"
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        for k in _KEYS:
            (project / k).write_bytes(old)
            data["content_hashes"][k] = hashlib.sha256(old).hexdigest()
        manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

        assert not update_project(project, ide="claude-code")["errors"]
        for k in _KEYS:
            content = _get_hook_content(Path(k).name)
            assert content is not None
            assert (project / k).read_bytes() == content.encode("utf-8")
            assert _hashes(project)[k] == hashlib.sha256(content.encode("utf-8")).hexdigest()

        _run_uninstall(_ns(project))

        assert not any((project / k).exists() for k in _KEYS)

    def test_edited_hook_then_update_project_keeps_the_edit(self, tmp_path: Path) -> None:
        """CC03-INSTALL-OVERWRITES-EDITS: a plain ``update-project`` keeps a hand-edited CC-03 hook
        and names it in ``warnings`` (HB-2), git-dirty or not."""
        project = _project(tmp_path)
        assert not update_project(project, ide="claude-code")["errors"]
        target = project / _KEYS[0]
        edited = target.read_bytes() + b"\n# my edit\n"
        target.write_bytes(edited)

        result = update_project(project, ide="claude-code")

        assert not result["errors"]
        assert target.read_bytes() == edited
        assert any(w.startswith(f"{_KEYS[0]}: kept because it was edited") for w in result.get("warnings", []))


@pytest.mark.integration
class TestCc03HookEditBetweenPlanAndAct:
    def test_edit_saved_after_planning_survives_the_act(self, tmp_path: Path) -> None:
        """HB-2: ownership was proven from a planning-time hash; an edit saved before the delete must live."""
        from trw_mcp.bootstrap._uninstall_manifest import apply_removal, plan_manifest_removal

        project = _project(tmp_path)
        assert not update_project(project, ide="claude-code")["errors"]
        hashes = _hashes(project)
        plan = plan_manifest_removal(project, ".claude/hooks", "claude-code", {k: hashes[k] for k in _KEYS}, {}, [])
        assert {d.action for d in plan} == {"remove"}
        edited = project / _KEYS[0]
        edited_bytes = edited.read_bytes() + b"\n# saved after planning\n"
        edited.write_bytes(edited_bytes)

        result: dict[str, list[str]] = {}
        removed, errors = apply_removal(plan, result, project)

        assert edited.read_bytes() == edited_bytes
        assert _KEYS[0] not in removed
        assert _KEYS[1] in removed and not (project / _KEYS[1]).exists()
        assert errors == 0
        assert any(str(edited) in line for line in result["preserved"])
        # whole tree: the edited bytes exist somewhere under .claude/hooks
        assert any(p.read_bytes() == edited_bytes for p in (project / ".claude" / "hooks").iterdir())
