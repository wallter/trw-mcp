"""PRD-QUAL-143-FR01: one inline instruction block, no ``.trw`` sidecar.

The externalization carrier moved the TRW block into ``.trw/INSTRUCTIONS.md``
(Copilot: ``.trw/COPILOT-INSTRUCTIONS.md``) behind an ``@`` import. Two writers
then delivered the same content, the sidecar went unregenerated, and an agent
read a deliver gate that no longer matched the bundled one. These tests pin the
single-writer contract: the block is inline, it carries memory routing, feedback
reporting and the offline table once each, the deliver gate occurs once, and a
sync retires any sidecar and import it finds.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests._layout import PACKAGE_ROOT

_SIDECARS = (".trw/INSTRUCTIONS.md", ".trw/COPILOT-INSTRUCTIONS.md")
#: The header line the removed generator wrote into every sidecar.
_GENERATED = (
    "<!-- TRW AUTO-GENERATED \u2014 do not edit. Imported into your instruction file via @x (PRD-CORE-203). -->"
)
_MEMORY = "### Memory Routing"
_OFFLINE = "### Troubleshooting: the MCP surface is absent"
_GATE = "## Deliver Gate"
_DELEGATION = "not to verify your own work"
_STALE_GATE = "build_check_result=pass"
_FEEDBACK = re.compile(r"^### Reporting Issues to TRW$|^TRW issues: see ", re.MULTILINE)

#: Sidecar headings that do not survive verbatim in the AGENTS.md block, each
#: with the surface that now carries the statement.
_DISPOSITIONS = {
    "## TRW Behavioral Protocol (Auto-Generated)": "the block's Start/Accept/Verify list; full table in trw_session_start",
    "### Session Boundaries": "the block's closing paragraph (render_deliver_gate_statement)",
    "#### Project vs user tier": "memory-routing.md, rendered under Memory Routing",
    "#### Feedback semantics": "memory-routing.md, rendered under Memory Routing",
}


def _agents_bodies() -> dict[str, Callable[[], str]]:
    from trw_mcp.state.claude_md._static_sections import render_agents_trw_section, render_minimal_protocol

    return {"minimal": render_minimal_protocol, "full": render_agents_trw_section}


@pytest.mark.parametrize("body_name", ["minimal", "full"])
def test_agents_block_states_each_section_once(body_name: str) -> None:
    body = _agents_bodies()[body_name]()

    assert body.count(_MEMORY) == 1
    assert body.count(_OFFLINE) == 1
    assert body.count(_GATE) == 1
    assert len(_FEEDBACK.findall(body)) == 1
    assert body.replace("\n", " ").count(_DELEGATION) == 1
    assert _STALE_GATE not in body


def test_every_sidecar_heading_survives_or_has_a_home() -> None:
    """Diff the retired sidecar's headings into the post-change closure."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md._static_sections import (
        render_ceremony_quick_ref,
        render_closing_reminder,
        render_imperative_opener,
        render_memory_harmonization,
        render_minimal_protocol,
    )
    from trw_mcp.state.claude_md.sections._feedback import render_feedback_reporting

    sidecar = "".join(
        (
            render_imperative_opener(),
            render_ceremony_quick_ref(),
            render_memory_harmonization(),
            render_feedback_reporting(get_config().client_profile),
            render_closing_reminder(),
        )
    )
    closure = render_minimal_protocol()
    missing = [
        heading
        for heading in re.findall(r"^#{2,4} .+$", sidecar, re.MULTILINE)
        if heading not in closure and heading not in _DISPOSITIONS
    ]
    assert missing == []
    assert _DELEGATION in render_imperative_opener().replace("\n", " ")
    assert _DELEGATION in closure.replace("\n", " ")


def test_sidecar_plumbing_is_gone() -> None:
    from trw_mcp.models.config import TRWConfig

    src = PACKAGE_ROOT / "src/trw_mcp"
    assert ".trw/INSTRUCTIONS.md" not in (src / "bootstrap/_update_transaction.py").read_text(encoding="utf-8")
    sync_hash = (src / "state/claude_md/_sync_hash.py").read_text(encoding="utf-8")
    assert "instruction_externalize" not in sync_hash
    assert "instruction_external_filename" not in sync_hash
    assert not (src / "state/claude_md/_carrier_externalize.py").exists()
    assert "instruction_externalize" not in TRWConfig.model_fields
    assert "instruction_external_filename" not in TRWConfig.model_fields


def _legacy_project(root: Path) -> None:
    from trw_mcp.bootstrap import init_project

    subprocess.run(["git", "init", "-q", str(root)], check=True)
    init_project(root, ide="claude-code")
    start, end = "<!-- trw:start -->", "<!-- trw:end -->"
    (root / "CLAUDE.md").write_text(
        f"# Project\n\nUser prose.\n\n{start}\n@.trw/INSTRUCTIONS.md\n{end}\n\n@.trw/INSTRUCTIONS.md\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("# Agents\n\nHand-written rules.\n\n@.trw/INSTRUCTIONS.md\n", encoding="utf-8")
    copilot = root / ".github/copilot-instructions.md"
    copilot.parent.mkdir(parents=True, exist_ok=True)
    copilot.write_text(
        "# Copilot\n\n<!-- trw:copilot:start -->\n@.trw/COPILOT-INSTRUCTIONS.md\n<!-- trw:copilot:end -->\n",
        encoding="utf-8",
    )
    for rel in _SIDECARS:
        (root / rel).write_text(_legacy_sidecar_text(), encoding="utf-8")


def _legacy_sidecar_text() -> str:
    return f"{_GENERATED}\n(a) `trw_build_check` returned `{_STALE_GATE}`\n\nMy note added below the header.\n"


def _sync(root: Path) -> None:
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state.claude_md import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    cwd = os.getcwd()
    os.chdir(root)
    _reset_config()
    try:
        for client in ("claude-code", "copilot"):
            execute_claude_md_sync("root", str(root), get_config(), FileStateReader(), None, client, force=True)
    finally:
        os.chdir(cwd)
        _reset_config()


def test_sync_retires_sidecars_and_imports(tmp_path: Path) -> None:
    _legacy_project(tmp_path)
    _sync(tmp_path)

    for rel in _SIDECARS:
        assert not (tmp_path / rel).exists(), f"{rel} survived the sync"
        # Never deleted: renamed with every byte intact, in case notes were added below the header.
        assert (tmp_path / f"{rel}.retired").read_text(encoding="utf-8") == _legacy_sidecar_text()
    for rel in ("CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md"):
        text = (tmp_path / rel).read_text(encoding="utf-8")
        assert not any(line.strip() in {f"@{s}" for s in _SIDECARS} for line in text.splitlines()), rel

    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Hand-written rules." in agents
    closure = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "User prose." in closure
    assert closure.count(_MEMORY) == 1
    assert closure.count(_OFFLINE) == 1
    assert closure.count(_GATE) == 1
    assert _STALE_GATE not in closure


def test_user_authored_sidecar_is_kept_with_its_import(tmp_path: Path) -> None:
    """A file the old generator did not write is the user's: sync keeps it and its import."""
    _legacy_project(tmp_path)
    user_file = tmp_path / ".trw/INSTRUCTIONS.md"
    user_file.write_text("# My own notes\n\nNever deploy on Fridays.\n", encoding="utf-8")

    _sync(tmp_path)

    assert user_file.read_text(encoding="utf-8") == "# My own notes\n\nNever deploy on Fridays.\n"
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "@.trw/INSTRUCTIONS.md" in agents.splitlines()
    assert not (tmp_path / ".trw/COPILOT-INSTRUCTIONS.md").exists(), "the generated sidecar still retires"


def test_a_second_retirement_never_overwrites_an_earlier_one(tmp_path: Path) -> None:
    """An existing ``.retired`` file is kept; the new one gets a timestamped name."""
    _legacy_project(tmp_path)
    earlier = tmp_path / ".trw/INSTRUCTIONS.md.retired"
    earlier.write_text("retired by an earlier sync\n", encoding="utf-8")

    _sync(tmp_path)

    assert earlier.read_text(encoding="utf-8") == "retired by an earlier sync\n"
    stamped = sorted((tmp_path / ".trw").glob("INSTRUCTIONS.md.retired-*"))
    assert len(stamped) == 1
    assert stamped[0].read_text(encoding="utf-8") == _legacy_sidecar_text()


def test_a_dangling_retired_symlink_is_never_overwritten(tmp_path: Path) -> None:
    """``Path.exists()`` is false for a dangling link; retirement must still step around it."""
    _legacy_project(tmp_path)
    dangling = tmp_path / ".trw/INSTRUCTIONS.md.retired"
    dangling.symlink_to(tmp_path / "nowhere")

    _sync(tmp_path)

    assert dangling.is_symlink() and os.readlink(dangling) == str(tmp_path / "nowhere")
    stamped = sorted((tmp_path / ".trw").glob("INSTRUCTIONS.md.retired-*"))
    assert len(stamped) == 1
    assert stamped[0].read_text(encoding="utf-8") == _legacy_sidecar_text()
