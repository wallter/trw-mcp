"""The server ``instructions`` string is budgeted, and its routing map must be true.

WHY THIS EXISTS

Under Claude Code's default tool-schema deferral, exactly two things load at
session start: the bare tool NAMES and this string. That makes ``instructions``
disproportionately load-bearing — it is the only prose an agent has before it
spends a ToolSearch round-trip.

Claude Code truncates it at **2KB**, silently. Nothing errors, nothing logs; the
tail is simply gone, and because our routing map lives in the second paragraph,
the part that would be lost is the part doing the most work. A length assertion
is the only way to see it.

The binding number is the CLAUDE-CODE RENDERING, not the raw YAML. Every
``{tool:...}`` placeholder expands to ``mcp__trw__<name>`` for that profile —
about ten extra characters each, and the map carries ~20 of them. Measuring the
bare-name rendering would understate the real cost by ~270 chars and let a
change sail past the ceiling it actually breaks.

The second test is the one that matters more over time: a routing map naming a
tool that does not exist is worse than no map. It sends the agent to search for
a name that returns nothing, which is exactly the failure mode
(openai/codex#21503) the map was written to defend against.
"""

from __future__ import annotations

import re
from typing import Final

import pytest

pytestmark = pytest.mark.unit

# Claude Code's documented truncation point for server instructions and tool
# descriptions. This is a CLIENT limit, not a preference — do not raise it.
CLAUDE_CODE_TRUNCATION_CHARS: Final[int] = 2048

# Headroom kept below the hard truncation so an ordinary wording edit trips this
# test rather than silently losing the tail on a user's machine.
INSTRUCTIONS_CEILING_CHARS: Final[int] = 1_900


def _rendered(client_id: str) -> str:
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.prompts.messaging import render_message

    return render_message("server_instructions", resolve_client_profile(client_id))


@pytest.mark.parametrize("client_id", ["claude-code", "opencode", "codex", "cursor-ide"])
def test_instructions_fit_under_client_truncation(client_id: str) -> None:
    """No profile's rendering may exceed the 2KB point where clients cut it off."""
    rendered = _rendered(client_id)
    assert len(rendered) <= INSTRUCTIONS_CEILING_CHARS, (
        f"server_instructions rendered for {client_id} is {len(rendered)} chars, "
        f"over the {INSTRUCTIONS_CEILING_CHARS} ceiling (hard client truncation at "
        f"{CLAUDE_CODE_TRUNCATION_CHARS}). Truncation is SILENT and takes the tail — "
        "which is the tool routing map. Cut prose before adding to the map."
    )


def test_the_served_default_also_fits() -> None:
    """The string the server actually hands FastMCP is the one clients receive."""
    from trw_mcp.server._app import _load_server_instructions

    served = _load_server_instructions()
    assert served, "server instructions resolved to empty"
    assert "{tool:" not in served, "unexpanded {tool:...} placeholder reached the served instructions"
    assert len(served) <= INSTRUCTIONS_CEILING_CHARS, (
        f"served instructions are {len(served)} chars, over {INSTRUCTIONS_CEILING_CHARS}"
    )


async def test_every_tool_named_in_the_instructions_is_registered() -> None:
    """The routing map must not point at a tool that does not exist.

    A stale name is not a cosmetic defect: the agent searches for it, gets
    nothing back, and concludes the capability is absent — then falls back to
    something worse. Renaming or retiring a tool has to update this string.
    """
    from trw_mcp.server._app import mcp

    served = _load_bare()
    registered = {tool.name for tool in await mcp._list_tools()}
    referenced = set(re.findall(r"\btrw_[a-z0-9_]+\b", served))
    assert referenced, "no tool names found in the instructions — this test would be vacuous"

    unknown = sorted(referenced - registered)
    assert not unknown, (
        f"server_instructions names tools that are not registered: {unknown}. "
        "Agents will search for these and find nothing."
    )


def _load_bare() -> str:
    from trw_mcp.prompts.messaging import get_message

    return get_message("server_instructions")


async def test_the_routing_map_covers_the_always_loaded_floor_and_beyond() -> None:
    """The map earns its bytes only if it reaches tools that are NOT always-loaded.

    Non-vacuity control. A map listing only the five always-loaded ceremony
    tools would tell the agent nothing it did not already have expanded, while
    still passing every other assertion here.
    """
    from trw_mcp.server._always_load import ALWAYS_LOAD_TOOLS

    referenced = set(re.findall(r"\btrw_[a-z0-9_]+\b", _load_bare()))
    deferred_referenced = referenced - ALWAYS_LOAD_TOOLS
    assert len(deferred_referenced) >= 10, (
        "The instructions routing map references only "
        f"{sorted(deferred_referenced)} beyond the always-loaded floor. Its whole "
        "purpose is to make DEFERRED tools discoverable."
    )
