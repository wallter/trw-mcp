"""CSR-22: every client carrier names where the hard tier lives.

A client with no session-start hook reads only its instruction carrier. The
values-and-hard-limits block lives in the installed framework, so a carrier
that never names it leaves hookless clients to find HB-2 by luck (one profile
answered a destructive-git scenario without ever reaching it). The pointer
rides the deliver-gate block, the one text every profile's carrier embeds, so
the census below walks the authoritative client list, installs each profile
into a scratch project, and requires the pointer wherever the gate landed
(for the AGENTS.md clients that is the ``.trw/INSTRUCTIONS.md`` their link names).
"""

from __future__ import annotations

import re
from importlib.resources import files as pkg_files
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.client_profiles.catalog import client_surfaces
from trw_mcp.models.config._profiles import builtin_client_ids, resolve_client_profile
from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH
from trw_mcp.state.claude_md.sections._tool_lifecycle import (
    DELIVER_GATE_PHRASE,
    HARD_TIER_POINTER,
    render_deliver_gate_statement,
)


def _carriers(client_id: str, project: Path) -> list[Path]:
    """Every instruction file the profile loads that exists after install."""
    rels = {surface.relpath for surface in client_surfaces(client_id)}
    rels.add(resolve_client_profile(client_id).write_targets.instruction_path)
    found = [project / rel for rel in sorted(rels) if (project / rel).is_file()]
    # PRD-CORE-341: an AGENTS.md carrier holds only a link; the file it names is what the client loads.
    linked = project / INSTRUCTIONS_RELPATH
    if linked.is_file() and any(f"@{INSTRUCTIONS_RELPATH}" in path.read_text(encoding="utf-8") for path in found):
        found.append(linked)
    return found


@pytest.mark.unit
def test_the_gate_block_carries_the_hard_tier_pointer() -> None:
    block = render_deliver_gate_statement()
    assert HARD_TIER_POINTER in block
    assert block.index(HARD_TIER_POINTER) < block.index(DELIVER_GATE_PHRASE)


@pytest.mark.unit
def test_the_pointer_names_sections_the_bundled_framework_has() -> None:
    framework = (pkg_files("trw_mcp.data") / "framework.md").read_text(encoding="utf-8")
    assert re.search(r"(?m)^## EXECUTION MODEL SUMMARY$", framework)
    assert "**Values and hard limits**" in framework
    assert "HB-2 never destroy, overwrite or discard uncommitted work without explicit authorization" in framework
    assert re.search(r"(?m)^## DELEGATION", framework)
    assert "Peer coordination" in framework
    for cited in ("EXECUTION MODEL SUMMARY", "Values and hard limits", "HB-2", "DELEGATION → Peer coordination"):
        assert cited in HARD_TIER_POINTER


@pytest.mark.integration
@pytest.mark.parametrize("client_id", builtin_client_ids())
def test_every_installed_profile_carrier_names_the_hard_tier(client_id: str, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    result = init_project(project, ide=client_id)
    assert not result["errors"], result["errors"]

    carriers = _carriers(client_id, project)
    gate_carriers = [path for path in carriers if DELIVER_GATE_PHRASE in path.read_text(encoding="utf-8")]
    assert gate_carriers, f"{client_id}: no loaded carrier holds the deliver gate among {carriers}"
    for path in gate_carriers:
        text = path.read_text(encoding="utf-8")
        assert HARD_TIER_POINTER in text, f"{client_id}: {path.relative_to(project)} has the gate but no pointer"

    runtime = (project / ".trw" / "frameworks" / "FRAMEWORK.md").read_text(encoding="utf-8")
    assert "**Values and hard limits**" in runtime
