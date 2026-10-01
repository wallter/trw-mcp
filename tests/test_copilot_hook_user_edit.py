"""UF-BOOT-07: a user edit to a Copilot hook script survives init and update (HB-2).

The adapter and the C5 distill-hint scripts under ``.github/hooks/`` were rewritten on every run with no
user-edit guard, while the sibling surfaces (CC-03, CC-05, cursor hooks) already had one. The managed-artifact
manifest recorded them, but only uninstall read it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_EDIT = b"#!/bin/sh\n# my local tweak\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scripts() -> dict[str, bytes]:
    from trw_mcp.bootstrap._managed_client_artifacts import _copilot_hook_scripts

    scripts = _copilot_hook_scripts()
    assert scripts, "the bundle ships Copilot hook scripts"
    return scripts


def _install(target: Path, manifest: dict[str, str] | None = None, *, force: bool = False) -> dict[str, list[str]]:
    from trw_mcp.bootstrap._copilot import generate_copilot_hooks
    from trw_mcp.bootstrap._copilot_distill_channels import install_copilot_distill_channels

    result = generate_copilot_hooks(target, force=force, manifest_hashes=manifest)
    dc = install_copilot_distill_channels(target, force=force, manifest_hashes=manifest)
    for key, items in dc.items():
        result.setdefault(key, []).extend(items)
    return result


def test_an_edited_copilot_hook_script_is_kept_and_named(tmp_path: Path) -> None:
    _install(tmp_path)
    for rel in _scripts():
        (tmp_path / rel).write_bytes(_EDIT)

    result = _install(tmp_path)

    for rel in _scripts():
        assert (tmp_path / rel).read_bytes() == _EDIT, f"{rel}: a user edit must never be overwritten"
        assert rel in result["preserved"] and rel not in result.get("updated", [])


def test_a_copy_trw_wrote_earlier_is_refreshed(tmp_path: Path) -> None:
    old = b"#!/bin/sh\n# what an older TRW shipped\n"
    for rel in _scripts():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(old)

    _install(tmp_path, {rel: _sha(old) for rel in _scripts()})

    for rel, bundled in _scripts().items():
        assert (tmp_path / rel).read_bytes() == bundled, f"{rel}: TRW's own last write is refreshed"


def test_force_still_overwrites(tmp_path: Path) -> None:
    _install(tmp_path)
    for rel in _scripts():
        (tmp_path / rel).write_bytes(_EDIT)

    _install(tmp_path, force=True)

    for rel, bundled in _scripts().items():
        assert (tmp_path / rel).read_bytes() == bundled


def test_the_update_path_keeps_an_edited_c5_hook(tmp_path: Path) -> None:
    """update-project's copilot distill path passes the manifest through and reports the kept file."""
    from trw_mcp.bootstrap._ide_targets_distill import _update_copilot_distill_channels

    _install(tmp_path)
    c5 = [rel for rel in _scripts() if "distill" in rel]
    assert c5
    for rel in c5:
        (tmp_path / rel).write_bytes(_EDIT)
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

    _update_copilot_distill_channels(tmp_path, result, manifest_hashes=None)

    for rel in c5:
        assert (tmp_path / rel).read_bytes() == _EDIT
        assert rel in result["preserved"]


def test_the_default_call_never_overwrites_an_edit(tmp_path: Path) -> None:
    """Mechanism, on the pre-fix signature: a plain re-run used to replace the edit and report it 'updated'."""
    from trw_mcp.bootstrap._copilot import generate_copilot_hooks
    from trw_mcp.bootstrap._copilot_distill_channels import install_copilot_distill_channels

    generate_copilot_hooks(tmp_path)
    install_copilot_distill_channels(tmp_path)
    for rel in _scripts():
        (tmp_path / rel).write_bytes(_EDIT)

    generate_copilot_hooks(tmp_path)
    install_copilot_distill_channels(tmp_path)

    for rel in _scripts():
        assert (tmp_path / rel).read_bytes() == _EDIT, f"{rel}: replaced by a plain re-run"


def test_a_symlink_at_a_hook_path_is_refused_not_kept(tmp_path: Path) -> None:
    """The guard must not read through a planted link and call it an edit; the safe writer refuses it."""
    from trw_mcp.bootstrap._copilot import generate_copilot_hooks

    rel = next(r for r in _scripts() if r.endswith("trw-copilot-adapter.sh"))
    outside = tmp_path / "outside"
    outside.write_bytes(_EDIT)
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).symlink_to(outside)

    result = generate_copilot_hooks(tmp_path)

    assert outside.read_bytes() == _EDIT
    assert rel not in result["preserved"]
    assert any("symlink" in e for e in result["errors"])


def test_reinit_refreshes_an_unedited_older_trw_copy_its_manifest_records(tmp_path: Path) -> None:
    """UF-BOOT-07-KI2 (codex r1): init-project --ide copilot passed no manifest_hashes to the Copilot writers, so a
    hook TRW itself wrote earlier (its hash recorded) read as a user edit and was never refreshed."""
    import yaml

    from trw_mcp.bootstrap._init_project_ide import _install_copilot_artifacts
    from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE

    old = b"#!/bin/sh\n# what an older TRW shipped\n"
    for rel in _scripts():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(old)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / _MANIFEST_FILE).write_text(
        yaml.safe_dump({"version": 1, "content_hashes": {rel: _sha(old) for rel in _scripts()}}), encoding="utf-8"
    )
    # Seeded exactly as init_project seeds it (codex r1 KI: update-style keys made _extend_result raise KeyError
    # into swallowed warnings, so a hash-propagation regression in the other writers went unseen).
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    _install_copilot_artifacts(tmp_path, force=False, result=result)

    assert not result["errors"], result["errors"]
    assert not result.get("warnings"), result.get("warnings")

    for rel, bundled in _scripts().items():
        assert (tmp_path / rel).read_bytes() == bundled, f"{rel}: TRW's own older copy was kept as if edited"
