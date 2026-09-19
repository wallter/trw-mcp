"""PRD-CORE-250 migration — an upgraded project keeps no copy of a deleted hook.

``_install_hooks`` copies every bundled ``*.sh`` into ``.claude/hooks/`` and does
NOT sweep, so deleting four hooks from the bundle (FR01-FR04) leaves four
orphaned copies in every project enrolled before this release. They are inert
there — unregistered by any template, exactly as they were before — but "inert
files nobody can account for" is the state this PRD exists to end.

The sweep already exists: ``_remove_stale_artifacts`` removes
``prev_hooks - bundled_hooks`` using ``.trw/managed-artifacts.yaml`` as the
record of what TRW installed. These tests prove it covers this case on the real
update path rather than assuming it does, and that it leaves a user's own hook
alone while doing so.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trw_mcp.bootstrap._version_migration import _remove_stale_artifacts

pytestmark = pytest.mark.integration

_DELETED = ("completion-gate.sh", "helper-idle.sh", "phase-cycle-stop.sh", "lib-ide-adapter.sh")
_HOOK_SHA = hashlib.sha256(b"#!/bin/sh\nexit 0\n").hexdigest()


def _enrolled_before_this_release(tmp_path: Path) -> Path:
    """A project holding the pre-PRD-CORE-250 hook set, manifest and all."""
    root = tmp_path / "legacy"
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (root / ".trw").mkdir(parents=True)

    kept = ("session-start.sh", "stop-ceremony.sh", "lib-trw.sh")
    for name in (*_DELETED, *kept):
        (hooks / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    # A hook the user wrote. TRW must never touch it.
    (hooks / "my-own-audit.sh").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")

    # Written by hand rather than by `_write_manifest`, and that is the point of
    # the fixture: `_write_manifest` derives `custom_hooks` from "in the project
    # but not in the CURRENT bundle", so calling it here would file the four
    # deleted hooks as the user's own and the sweep would correctly refuse to
    # touch them. A project enrolled BEFORE this release has them under `hooks`,
    # because they were bundled when its manifest was written.
    (root / ".trw" / "managed-artifacts.yaml").write_text(
        "version: 2\n"
        "skills: []\n"
        "agents: []\n"
        "hooks:\n" + "".join(f"- {name}\n" for name in (*_DELETED, *kept)) + "custom_skills: []\n"
        "custom_agents: []\n"
        "custom_hooks:\n- my-own-audit.sh\n"
        # What TRW last wrote: the proof of ownership every sweep requires
        # (PRD-INFRA-190-FR06). A real pre-release manifest records it.
        "content_hashes:\n" + "".join(f"  {name}: {_HOOK_SHA}\n" for name in (*_DELETED, *kept)),
        encoding="utf-8",
    )
    return root


def test_removed_bundled_hook_is_swept(tmp_path: Path) -> None:
    root = _enrolled_before_this_release(tmp_path)
    for name in _DELETED:
        assert (root / ".claude" / "hooks" / name).exists(), "fixture is not a pre-release project"

    result: dict[str, list[str]] = {"created": [], "updated": [], "skipped": []}
    _remove_stale_artifacts(root, result)

    orphans = [name for name in _DELETED if (root / ".claude" / "hooks" / name).exists()]
    assert orphans == [], f"update-project left orphaned copies of deleted hooks: {orphans}"
    assert (root / ".claude" / "hooks" / "session-start.sh").exists(), "the sweep took a hook that still ships"
    assert (root / ".claude" / "hooks" / "my-own-audit.sh").exists(), "the sweep took a user-authored hook"


def test_the_sweep_is_idempotent(tmp_path: Path) -> None:
    """A second sweep finds nothing left to do."""
    root = _enrolled_before_this_release(tmp_path)
    hooks = root / ".claude" / "hooks"
    _remove_stale_artifacts(root, {"created": [], "updated": [], "skipped": []})
    after_first = sorted(p.name for p in hooks.iterdir())
    second: dict[str, list[str]] = {"created": [], "updated": [], "skipped": []}
    _remove_stale_artifacts(root, second)

    assert not set(_DELETED) & set(after_first), f"the first sweep did not remove all four: {after_first}"
    assert sorted(p.name for p in hooks.iterdir()) == after_first, "the second sweep removed something again"
    assert not second.get("preserved")


def test_a_config_predating_the_new_fields_still_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The five FR10 fields have defaults, so an old config.yaml still loads."""
    from trw_mcp.models.config import get_config, reload_config

    (tmp_path / ".trw").mkdir(parents=True)
    (tmp_path / ".trw" / "config.yaml").write_text("task_root: docs\nhooks_enabled: true\n", encoding="utf-8")
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    reload_config()
    try:
        config = get_config()
        assert config.task_root == "docs", "the fixture config did not reach the loader"
        assert config.degenerate_result_cooldown_calls == 20
        assert config.degenerate_result_truncation_markers == ["more lines]", "Output too large"]
    finally:
        monkeypatch.undo()
        reload_config()
