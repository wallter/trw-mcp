"""Adoption tolerates an absent LICENCE-GATED file only; a required or edited one still blocks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project, update_project

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("no_memory_daemon")]

_EXPLORER = ".opencode/agents/trw-distill-explorer.md"
_REQUIRED_AGENT = ".opencode/agents/trw-implementer.md"


def _orphaned_opencode(root: Path) -> Path:
    """An unlicensed opencode install whose record lists only codex."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    init_project(root, ide="opencode")
    config = root / ".trw" / "config.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["target_platforms"] = ["codex"]
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
        check=True,
    )
    return config


def _recorded(config: Path) -> list[str]:
    return yaml.safe_load(config.read_text(encoding="utf-8"))["target_platforms"]


def test_unentitled_install_has_no_explorer_and_is_adopted(tmp_path: Path) -> None:
    config = _orphaned_opencode(tmp_path)
    assert not (tmp_path / _EXPLORER).exists()
    result = update_project(tmp_path)
    assert "opencode" in _recorded(config), result.get("warnings")


def test_missing_required_agent_blocks_adoption(tmp_path: Path) -> None:
    config = _orphaned_opencode(tmp_path)
    assert (tmp_path / _REQUIRED_AGENT).is_file()
    (tmp_path / _REQUIRED_AGENT).unlink()
    result = update_project(tmp_path)
    assert "opencode" not in _recorded(config)
    assert any("opencode not adopted" in w and f"{_REQUIRED_AGENT} is missing" in w for w in result["warnings"])


def test_present_but_edited_explorer_blocks_adoption(tmp_path: Path) -> None:
    config = _orphaned_opencode(tmp_path)
    (tmp_path / _EXPLORER).write_text("my own explorer\n", encoding="utf-8")
    result = update_project(tmp_path)
    assert "opencode" not in _recorded(config)
    assert any(_EXPLORER in w and "differs" in w for w in result["warnings"])


def test_entitled_install_missing_its_explorer_blocks_adoption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence is licence-optional only while unentitled; an entitled install's writer would have written it."""
    config = _orphaned_opencode(tmp_path)
    monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **_: True)
    result = update_project(tmp_path)
    assert "opencode" not in _recorded(config)
    assert any(f"{_EXPLORER} is missing" in w for w in result["warnings"])
