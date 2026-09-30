"""PRD-CORE-348-FR03: the packaged agent and skill sources carry the AHR guidance."""

from __future__ import annotations

from importlib import resources

import pytest

_DATA = resources.files("trw_mcp.data")
SOURCES = {
    "agents/trw-implementer.md": ("trw://templates/ahr", "trw-mcp handoff seal", "none_known"),
    "agents/trw-lead.md": ("trw://templates/ahr", "read-back before accepting", "`standard`"),
    "skills/trw-exec-plan/SKILL.md": ("trw://templates/ahr", "next_actions[0]"),
    "skills/trw-deliver/SKILL.md": ("trw://templates/ahr", "trw-mcp handoff seal"),
}


def _text(rel: str) -> str:
    return _DATA.joinpath(rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", sorted(SOURCES))
def test_source_names_the_ahr_form(rel: str) -> None:
    text = _text(rel)
    for marker in SOURCES[rel]:
        assert marker in text, f"{rel} lacks {marker!r}"


def test_implementer_completion_block_forbids_empty_risk_list() -> None:
    text = _text("agents/trw-implementer.md")
    assert "remaining_risk: []" not in text
    assert "{none_known: true, checked:" in text


@pytest.mark.parametrize("rel", sorted(SOURCES))
def test_guidance_makes_no_outcome_claim(rel: str) -> None:
    lowered = _text(rel).lower()
    for claim in ("improves outcomes", "improves handoff", "ahr improves", "read-back improves"):
        assert claim not in lowered
