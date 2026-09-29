"""PRD-CORE-301-FR13: the shared instruction block keeps only what an agent needs every turn.

The shared block (``render_agents_trw_section``) is what claude-code's
``AGENTS.md``, codex's ``.codex/INSTRUCTIONS.md``, opencode's
``.opencode/INSTRUCTIONS.md`` and cursor-ide's ``.cursor/rules/trw-ceremony.mdc``
carry. FR13 moves the text an agent needs only on demand out of it, and every
moved item stays reachable: the block keeps a pointer, and the surface the
pointer names serves the text.

Moved item -> on-demand surface (one direct test each below):

* tool list and capability listing -> ``trw_status(detail="surface")`` /
  ``trw-mcp profile explain`` (the same ``surface_detail`` service);
* offline-substitute table -> ``trw-mcp local --help``;
* memory-routing policy -> MCP resource ``trw://framework/memory-routing``;
* delegation decision guide -> FRAMEWORK.md "DELEGATION AND FILE OWNERSHIP"
  and ``.trw/context/behavioral_protocol.md``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT

#: PRD-CORE-301-FR13 absolute target, in chars/4 tokens (the unit
#: ``scripts/measure_context_cost.py`` reports ``instruction_files`` in).
_BUDGET = 1538
#: Whole files TRW generates for these clients; measured whole, as the C14 probe does.
_BUDGETED = {"codex": ".codex/INSTRUCTIONS.md", "opencode": ".opencode/INSTRUCTIONS.md"}
#: Block-carrying files per measured client, and the part of each the C14 probe counts.
_CARRIERS = {
    "claude-code": "AGENTS.md",
    "codex": ".codex/INSTRUCTIONS.md",
    "cursor-ide": ".cursor/rules/trw-ceremony.mdc",
    "opencode": ".opencode/INSTRUCTIONS.md",
}
#: C14's recorded instruction_files per client (tokens) in the frozen seeded baseline.
_C14_BASELINE = "docs/sprint-mcp7/context-cost-baseline-2026-09-25-seeded.json"
_C14_NAMES = {"claude-code": "claude-code", "codex": "codex", "cursor-ide": "cursor", "opencode": "opencode"}


def _tokens(text: str) -> int:
    return math.ceil(len(text) / 4) if text else 0


def _install(ide: str, root: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    (root / ".git").mkdir(parents=True)
    assert init_project(root, ide=ide)["errors"] == []
    return root


def _counted(ide: str, root: Path) -> str:
    """The TRW-owned text the C14 probe counts: the marked block, or the whole generated file."""
    text = (root / _CARRIERS[ide]).read_text(encoding="utf-8")
    if "<!-- trw:start -->" in text:
        return text.split("<!-- trw:start -->", 1)[1].split("<!-- trw:end -->", 1)[0]
    return text


def _block(ide: str) -> str:
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.state.claude_md.sections._delegation import render_agents_trw_section

    return render_agents_trw_section(client_profile=resolve_client_profile(ide))


# ── budget ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ide", sorted(_BUDGETED))
def test_codex_and_opencode_instruction_files_fit_the_budget(ide: str, tmp_path: Path) -> None:
    """FR13: the installed codex/opencode instruction file is at most 1,538 tokens."""
    text = (_install(ide, tmp_path) / _BUDGETED[ide]).read_text(encoding="utf-8")

    assert _tokens(text) <= _BUDGET, f"{ide}: {_tokens(text)} tokens > {_BUDGET}"


def test_the_budget_check_rejects_an_oversized_file() -> None:
    """Negative: one character over the budget fails the same comparison."""
    assert _tokens("x" * (_BUDGET * 4)) <= _BUDGET
    assert not _tokens("x" * (_BUDGET * 4 + 1)) <= _BUDGET


@pytest.mark.skipif(MONOREPO_ROOT is None, reason="the C14 baseline lives in the monorepo")
@pytest.mark.parametrize("ide", sorted(_C14_NAMES))
def test_no_regression_against_the_c14_baseline(ide: str, tmp_path: Path) -> None:
    """FR13: every C14 baseline client is at or under its recorded instruction_files row.

    The baseline was re-recorded from the FR13 commit (lead decision 2026-09-26): the S0
    rows for claude-code (1,230) and opencode (944) predated the growth of FR08's protected
    blocks (972 tokens) and could not be met without editing one or cutting an every-turn rule.
    """
    assert MONOREPO_ROOT is not None
    baseline = json.loads((MONOREPO_ROOT / _C14_BASELINE).read_text(encoding="utf-8"))
    rows = {c["client"]: {r["contributor"]: r["tokens_per_session"] for r in c["rows"]} for c in baseline["clients"]}
    recorded = rows[_C14_NAMES[ide]]["instruction_files"]

    measured = _tokens(_counted(ide, _install(ide, tmp_path)))

    assert measured <= recorded, f"{ide}: {measured} tokens > C14 baseline {recorded}"


@pytest.mark.skipif(MONOREPO_ROOT is None, reason="the C14 baseline lives in the monorepo")
def test_every_c14_baseline_leaves_room_for_the_protected_blocks() -> None:
    """A recorded instruction_files row below the FR08 protected-block floor is unreachable by any
    content change; this guards a future stale baseline (the S0 rows for claude-code and opencode were)."""
    from tests import _protected_blocks as pb

    assert MONOREPO_ROOT is not None
    floor = sum(_tokens(block.canonical) for block in pb.rendered_blocks())
    baseline = json.loads((MONOREPO_ROOT / _C14_BASELINE).read_text(encoding="utf-8"))
    for client in baseline["clients"]:
        recorded = next(r["tokens_per_session"] for r in client["rows"] if r["contributor"] == "instruction_files")
        assert recorded >= floor, f"{client['client']}: baseline {recorded} < protected-block floor {floor}"


# ── every moved item stays reachable ──────────────────────────────────────────


@pytest.mark.parametrize("ide", sorted(_CARRIERS))
def test_the_block_no_longer_carries_the_moved_text(ide: str) -> None:
    """The moved bodies are gone from the block; each pointer is there once."""
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_offline_substitutes

    block = _block(ide)

    assert "## TRW Tools" not in block
    assert "- **Available in every session**" not in block
    assert "| Obligation | Offline substitute |" not in block
    assert render_offline_substitutes() not in block
    assert "#### Project vs user tier" not in block
    assert "### When to Delegate" not in block
    for pointer in ('trw_status(detail="surface")', "`trw-mcp local --help`", "`trw://framework/memory-routing`"):
        assert block.count(pointer) == 1, f"{ide}: pointer {pointer!r} appears {block.count(pointer)} times"


def test_tool_list_and_capabilities_are_served_by_the_surface_call() -> None:
    """Moved: tool list + capability listing. ``trw_status(detail="surface")``'s service names the live tools."""
    from trw_mcp.tools._profile_cli import surface_detail

    served = json.dumps(surface_detail())

    for tool in ("trw_session_start", "trw_checkpoint", "trw_learn", "trw_recall", "trw_deliver", "trw_build_check"):
        assert tool in served, f"{tool} is not in the surface the block points at"


def test_offline_table_is_served_by_local_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Moved: the offline-substitute table. ``trw-mcp local --help`` prints every row of it."""
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_offline_substitutes

    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["local", "--help"])
    printed = capsys.readouterr().out

    rows = [line for line in render_offline_substitutes().splitlines() if line.startswith("| `trw_")]
    assert len(rows) == 7
    assert all(row in printed for row in rows)
    assert "UNGATED" in printed


def test_memory_routing_is_served_by_its_resource() -> None:
    """Moved: the memory-routing policy. The resource serves the owner's render byte for byte."""
    from fastmcp import FastMCP

    from tests.conftest import get_resources_sync
    from trw_mcp.resources.config import register_config_resources
    from trw_mcp.state.claude_md.sections._memory_routing import render_memory_harmonization

    server = FastMCP("fr13")
    register_config_resources(server)
    served = get_resources_sync(server)["trw://framework/memory-routing"].fn()

    assert served == render_memory_harmonization()
    assert "#### Project vs user tier" in served
    assert "Native auto-memory" in served


def test_delegation_guide_is_served_by_the_framework_and_the_protocol_file() -> None:
    """Moved: the delegation decision guide. The pointed-at FRAMEWORK.md section and the protocol file carry it."""
    from importlib.resources import files

    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.state.claude_md._renderer import ProtocolRenderer

    framework = files("trw_mcp").joinpath("data/framework.md").read_text(encoding="utf-8")
    protocol = ProtocolRenderer(client_profile=resolve_client_profile("codex")).render_behavioral_protocol()

    assert "\n## DELEGATION AND FILE OWNERSHIP\n" in framework
    assert "DELEGATION AND FILE OWNERSHIP" in _block("codex")
    assert "### When to Delegate" in protocol


def test_a_profile_without_delegation_gets_no_delegation_pointer() -> None:
    """Negative: opencode's profile turns delegation off, so its block carries no pointer to the guide."""
    assert "DELEGATION AND FILE OWNERSHIP" not in _block("opencode")
