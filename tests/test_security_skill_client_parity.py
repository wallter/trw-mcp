"""Security-audit semantics must not drift by client projection."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
GENERIC = PACKAGE_ROOT / "src/trw_mcp/data/skills/trw-security-check/SKILL.md"
#: copilot no longer forks this skill on disk (PRD-CORE-291-FR04) -- it
#: renders the canonical body, which is a byte-identical pass-through since
#: copilot keeps the canonical frontmatter unchanged (see
#: test_copilot_rendering_matches_generic_contract below). ``copilot/plugin``
#: is a separate, still file-based, unwired skill subset (no production
#: reader) and stays a real path.
CLIENT_VARIANTS = (
    PACKAGE_ROOT / "src/trw_mcp/data/copilot/plugin/skills/trw-security-check/SKILL.md",
    pytest.param(ROOT / ".cursor/skills/trw-security-check/SKILL.md", marks=requires_monorepo),
    pytest.param(ROOT / ".github/skills/trw-security-check/SKILL.md", marks=requires_monorepo),
)


@pytest.mark.parametrize("path", CLIENT_VARIANTS)
def test_security_skill_client_variants_match_generic_contract(path: Path) -> None:
    expected = GENERIC.read_bytes()
    assert path.read_bytes() == expected, path


def test_copilot_rendering_matches_generic_contract() -> None:
    from trw_mcp.bootstrap._client_skills import render_skill_md

    generic = GENERIC.read_text(encoding="utf-8")
    assert render_skill_md(generic, "copilot") == generic
