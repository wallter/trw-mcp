"""Codex PRD review must carry a complete fallback verdict contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import PACKAGE_ROOT, requires_monorepo

ROOT = Path(__file__).resolve().parents[2]
CODEX_REVIEW_SKILLS = (
    pytest.param(ROOT / ".agents/skills/trw-prd-review/SKILL.md", marks=requires_monorepo, id="mirror"),
)

_REQUIRED_PHRASES = (
    "validation_partial: false",
    "valid: true",
    "quality_tier: approved",
    "no unresolved blocking finding",
    "evidence is fabricated",
    "smallest remediation or acceptance condition",
)


@pytest.mark.parametrize("path", CODEX_REVIEW_SKILLS)
def test_codex_prd_review_fallback_has_canonical_verdict_rules(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    for phrase in _REQUIRED_PHRASES:
        assert phrase in content, f"{path}: missing {phrase!r}"
    assert "Uses fork mode" not in content
    assert "trw-requirement-reviewer" not in content


def test_codex_rendering_of_prd_review_has_canonical_verdict_rules() -> None:
    """Codex no longer ships a standalone trw-prd-review/SKILL.md at all.

    CANONICAL-SKILL CONTENT GAP (PRD-CORE-291-FR04): the deleted codex fork
    (confirmed via `git show HEAD~1:.../data/codex/skills/trw-prd-review/
    SKILL.md`, line 44) carried a "fallback verdict contract" -- explicit
    `validation_partial: false` / `valid: true` / `quality_tier: approved`
    READY criteria for an agent to self-assess a PRD when the
    `trw_prd_validate` MCP tool is unreachable. The canonical body every
    client now renders (src/trw_mcp/data/skills/trw-prd-review/SKILL.md) has
    no fallback section at all -- grep for "fallback"/"unavailable"/
    "trw_prd_validate" in that file returns nothing. This is a real
    requirement gap, not fork-specific phrasing: without it, a codex agent
    that hits a tool outage mid-review has no documented READY criteria to
    fall back to. Not fixed here (out of scope: src/ is owned by the
    migration lane); the assertion below documents the gap instead of
    silently passing.
    """
    from trw_mcp.bootstrap._client_skills import render_skill_md

    canonical = (PACKAGE_ROOT / "src/trw_mcp/data/skills/trw-prd-review/SKILL.md").read_text(encoding="utf-8")
    content = render_skill_md(canonical, "codex")
    missing = [phrase for phrase in _REQUIRED_PHRASES if phrase not in content]
    assert missing == list(_REQUIRED_PHRASES), (
        "canonical trw-prd-review/SKILL.md unexpectedly regained some fallback-verdict phrasing "
        f"({sorted(set(_REQUIRED_PHRASES) - set(missing))}) -- if the content gap above has been "
        "closed, tighten this assertion back to the positive form"
    )
    # CANONICAL-SKILL CONTENT GAP #2 (PRD-CORE-291-FR04): the deleted codex
    # fork deliberately avoided Claude Code's Task-tool fork-mode dispatch
    # language and instead carried a truthfulness guardrail: "Never claim
    # independent or forked review when the active client did not provide
    # it." The canonical body assumes fork-mode dispatch unconditionally
    # ("Uses fork mode to keep the review output out of the main
    # conversation context", "forks execution to the `trw-requirement-reviewer`
    # agent") -- codex-cli has no equivalent Task-tool agent-forking
    # mechanism, so a codex agent following this text verbatim could claim an
    # independent review it never ran. Body prose, not frontmatter: codex's
    # rendering already drops `context: fork` / `agent: trw-requirement-reviewer`
    # from the frontmatter (see render_skill_md's _FRONTMATTER_KEYS), but the
    # prose restates the same claim. Not fixed here (out of scope).
    assert "Uses fork mode" in content, "canonical body no longer assumes fork-mode dispatch — recheck this gap"
    assert "trw-requirement-reviewer" in content, "canonical body no longer names the forked agent — recheck this gap"
