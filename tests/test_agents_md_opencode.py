"""Tests for OpenCode instruction rendering.

PRD-CORE-301-FR02 deleted the per-model-family renderers and ``detect_model_family``:
every family rendered the same text, and ``.opencode/INSTRUCTIONS.md`` is now the
shared claude-code block with opencode framing.
"""

from __future__ import annotations

import pytest


def _render() -> str:
    from trw_mcp.state.claude_md._static_sections import render_opencode_instructions

    return render_opencode_instructions()


def test_opencode_content_has_core_protocol() -> None:
    content = _render()

    assert content.startswith("# TRW Instructions\n")
    assert "trw_session_start()" in content
    # PRD-CORE-301-FR13: the deliver gate names the tool; the workflow no longer restates it.
    assert "Do NOT call `trw_deliver` unless" in content
    assert "project-native" in content


@pytest.mark.parametrize("token", ["/think", "vLLM", "chain-of-thought", "extended thinking", "200K", "128K", "32K"])
def test_opencode_content_omits_family_prompt_recipes(token: str) -> None:
    """Provider/model prompt recipes live in adapters, not core OpenCode instructions."""
    assert token not in _render()
