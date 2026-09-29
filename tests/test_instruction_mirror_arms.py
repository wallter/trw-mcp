"""PRD-CORE-301-FR02: one direct test per client-mirror renderer arm.

``test_instruction_single_block.py::test_every_client_mirror_renders_from_one_renderer``
proves the installed files; these call each renderer arm directly so a
failure names the arm, not the install path. Every arm is framing followed by
``render_agents_trw_section`` for that client's profile, and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from trw_mcp.bootstrap._cursor import _CURSOR_IDE_APPENDIX, cursor_rules_mdc_body
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state.claude_md.sections._delegation import DELEGATION_GUIDE_POINTER, render_agents_trw_section
from trw_mcp.state.claude_md.sections._tool_lifecycle import (
    render_codex_instructions,
    render_opencode_instructions,
)

_MDC_FRONTMATTER = "---\ndescription:"


def _shared(client_id: str) -> str:
    return render_agents_trw_section(client_profile=resolve_client_profile(client_id))


#: arm -> (client whose profile gates the block, render, text before the block, text after it)
_ARMS: dict[str, tuple[str, Callable[[], str], str, str]] = {
    "codex": ("codex", render_codex_instructions, "# Codex TRW Instructions\n", ""),
    "opencode": ("opencode", render_opencode_instructions, "# TRW Instructions\n", ""),
    "cursor-ide": (
        "cursor-ide",
        lambda: cursor_rules_mdc_body(_shared("cursor-ide"), "cursor-ide"),
        _MDC_FRONTMATTER,
        _CURSOR_IDE_APPENDIX,
    ),
}


@pytest.mark.parametrize("arm", sorted(_ARMS))
def test_each_arm_is_framing_around_the_shared_block(arm: str) -> None:
    client_id, render, head, tail = _ARMS[arm]
    shared = _shared(client_id)

    text = render()

    assert text.startswith(head)
    before, found, after = text.partition(shared)
    assert found, f"{arm} does not embed the shared block verbatim"
    assert after.strip("\n") == tail.strip("\n"), f"{arm} adds text after the shared block beyond its framing"
    assert "## Deliver Gate" not in before, f"{arm} restates the protocol in its framing"


@pytest.mark.parametrize(
    ("client_id", "delegation"),
    [("codex", True), ("opencode", False)],
    ids=["codex-delegates", "opencode-does-not"],
)
def test_the_profile_gates_the_delegation_block(client_id: str, delegation: bool) -> None:
    """Boundary: the only per-client variance inside the block is what the client's profile gates."""
    render = render_codex_instructions if client_id == "codex" else render_opencode_instructions

    text = render()

    assert resolve_client_profile(client_id).include_delegation is delegation
    # PRD-CORE-301-FR13: the block carries a pointer to the guide, not the guide.
    assert (DELEGATION_GUIDE_POINTER in text) is delegation


def test_the_cursor_cli_arm_never_carries_the_ide_appendix() -> None:
    """Negative: the cursor-ide appendix is cursor-ide framing, never added to cursor-cli's light body."""
    from trw_mcp.bootstrap._cursor_cli import _cursor_cli_trw_section

    assert _CURSOR_IDE_APPENDIX.strip() not in cursor_rules_mdc_body(_cursor_cli_trw_section(), "cursor-cli")
    assert _CURSOR_IDE_APPENDIX.strip() in cursor_rules_mdc_body(_shared("cursor-ide"), "cursor-ide")
