"""An orphaned opencode install is adopted only on file-by-file proof, never on ``.opencode/`` existing.

The retired marker path (``_file_evidenced_clients``) put opencode into the write targets of a bare
``update-project`` because ``.opencode`` or ``opencode.json`` existed, so a writer rewrote a hand-edited
file with no proof. Adoption now goes through ``_client_adoption`` alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project, update_project

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


def _orphaned_opencode(root: Path) -> Path:
    """An opencode install the record does not name, with its implementer agent hand-edited."""
    (root / ".git").mkdir()
    assert not init_project(root, ide="opencode")["errors"]
    config = root / ".trw" / "config.yaml"
    data = yaml.safe_load(config.read_text())
    data["target_platforms"] = ["claude-code"]
    config.write_text(yaml.safe_dump(data, sort_keys=False))
    manifest = root / ".trw" / "managed-artifacts.yaml"
    hashes = yaml.safe_load(manifest.read_text())
    hashes["content_hashes"] = {k: v for k, v in hashes["content_hashes"].items() if not k.startswith(".opencode/")}
    manifest.write_text(yaml.safe_dump(hashes, sort_keys=False))
    edited = root / ".opencode" / "agents" / "trw-implementer.md"
    assert edited.is_file(), "precondition: the opencode writer target exists"
    edited.write_bytes(edited.read_bytes() + b"\nmy own instructions\n")
    return edited


def test_bare_update_keeps_an_edited_writer_target_of_an_orphaned_opencode_install(tmp_path: Path) -> None:
    edited = _orphaned_opencode(tmp_path)
    mine = edited.read_bytes()

    result = update_project(tmp_path)

    assert edited.read_bytes() == mine
    recorded = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text())["target_platforms"]
    assert "opencode" not in recorded
    blockers = [w for w in result.get("warnings", []) if w.startswith("opencode not adopted")]
    assert blockers, result.get("warnings")
    assert "differs from the bundled render" in blockers[0]
