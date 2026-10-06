"""update-project migrates what an older install left, and its rollback reports only what survived it.

The 9.1.0 install failure on a project whose whole ``.trw/`` was uncommitted: a pre-6.0.0 channel manifest
(retired ``tier_default``/``tier_min`` keys) aborted the update, the rollback moved the migrated config.yaml
and the new credentials.yaml into ``.trw/trash``, and the failed step still printed every retired file as
removed. On a successful run the uncommitted-file guard put config.yaml back whole, so the key move was undone
on every run.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.channels._manifest_loader import load

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_KEY = "trw_dk_upgrade_fixture_0123456789"
_MANIFEST = ".trw/channels/manifest.yaml"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _old_install(tmp_path: Path) -> Path:
    """A committed install, then the two uncommitted pre-upgrade shapes: a key in config.yaml, tier keys."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code")["errors"]
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "installed")

    config = root / ".trw" / "config.yaml"
    text = re.sub(r"(?m)^platform_api_key:.*\n", "", config.read_text(encoding="utf-8"))
    config.write_text(text + f'platform_api_key: "{_KEY}"\n', encoding="utf-8")

    manifest = root / _MANIFEST
    yaml = YAML()
    data = yaml.load(manifest.read_text(encoding="utf-8"))
    assert data["channels"], "fixture: the install must have written channel entries"
    for entry in data["channels"]:
        entry["tier_default"] = "T2"
        entry["tier_min"] = "T0"
    with manifest.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    porcelain = subprocess.run(
        ["git", "-C", str(root), "status", "--short", ".trw"], capture_output=True, text=True, check=True
    ).stdout
    assert ".trw/config.yaml" in porcelain and _MANIFEST in porcelain, "fixture: both files must be git-dirty"
    return root


def _key_in(path: Path) -> str:
    match = re.search(r'(?m)^platform_api_key:\s*"?([^"\n]*)"?\s*$', path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def test_an_uncommitted_pre_6_manifest_and_config_key_are_migrated_and_stay_migrated(tmp_path: Path) -> None:
    root = _old_install(tmp_path)

    result = update_project(root, ide="claude-code")

    assert not result["errors"], result["errors"]
    text = (root / _MANIFEST).read_text(encoding="utf-8")
    assert "tier_default" not in text and "tier_min" not in text
    assert load(root / _MANIFEST).channels
    assert [w for w in result["warnings"] if "tier_default" in w] == [
        ".trw/channels/manifest.yaml: dropped the retired keys tier_default and tier_min (unused since trw-mcp 6.0.0)"
    ]
    # The key moved and stayed moved: the uncommitted-file guard no longer puts the old config.yaml back.
    assert _key_in(root / ".trw" / "config.yaml") == ""
    assert _key_in(root / ".trw" / "credentials.yaml") == _KEY
    assert sum("ROTATE" in w for w in result["warnings"]) == 1
    # This run's own writes are never captured into trash as if someone else had written them.
    assert not [w for w in result["warnings"] if "held bytes this update did not write" in w], result["warnings"]
    assert not (root / ".trw" / "trash").exists()

    again = update_project(root, ide="claude-code")
    assert not again["errors"], again["errors"]
    assert not [w for w in again["warnings"] if "tier_default" in w or "ROTATE" in w]
    assert not (root / ".trw" / "trash").exists()


def test_a_rolled_back_update_restores_config_and_leaves_no_secret_in_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _old_install(tmp_path)
    config_before = (root / ".trw" / "config.yaml").read_bytes()
    from trw_mcp.bootstrap import _update_project as up

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(up, "_verify_installation", _fail)

    result = update_project(root, ide="claude-code")

    assert any("simulated write failure" in e for e in result["errors"])
    assert "update-project rolled back managed directories after write failure" in result["warnings"]
    assert (root / ".trw" / "config.yaml").read_bytes() == config_before
    assert not (root / ".trw" / "credentials.yaml").exists()
    # The migration's writes are proven this run's own, so the rollback clears them instead of parking a copy
    # of the credential in .trw/trash.
    assert not (root / ".trw" / "trash").exists(), sorted(p.name for p in (root / ".trw" / "trash").iterdir())
    # A move the rollback undid is not reported as done.
    assert not [w for w in result["warnings"] if "ROTATE" in w]


def test_forget_rolled_back_changes_keeps_only_what_the_rollback_could_not_restore(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._update_project import _forget_rolled_back_changes

    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "old.md").write_text("x", encoding="utf-8")
    result: dict[str, list[str]] = {
        "retired": [".claude/agents/old.md", ".claude/agents/gone.md"],
        "warnings": [
            "platform_api_key moved out of git-tracked config.yaml — "
            "ROTATE the key if it was already committed to git history.",
            "kept a note",
        ],
    }

    _forget_rolled_back_changes(tmp_path, result)

    assert result["retired"] == [".claude/agents/gone.md"]
    assert result["warnings"] == [
        "kept a note",
        "update-project rolled back managed directories after write failure",
        "the rollback put back the 1 retired TRW file(s) this run had removed;"
        " the next successful update-project removes them",
    ]
